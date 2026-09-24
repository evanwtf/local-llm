"""Pick a replay task's held-out tests (#726) by measuring each test, not guessing.

A held-out test is worth holding out only if it passes on the reference tree
and fails once the work is reverted: a test that passes either way checks
nothing the agent did. This measures that per test function, then proposes a
deterministic subset to hold out.

Two sources, as #726 names them:

  * **split** (default): the task's own visible test files at `base_commit`.
    Every test function is measured; of those that pass at the reference and
    fail after the revert, the ones with the smallest sha256 of their node id
    are proposed, `--fraction` of them (at least one). The hash makes the
    choice reproducible and blind to what a test checks. It never takes
    every such test, so at least one visible test still fails after the
    revert and the control check keeps working. When every method of a
    class is picked, the class id is
    proposed instead, since hiding them one by one would empty the class.
  * **later** (`--later REF`): tests the repository added to the same files
    after the task's commit, in REF's version of them. Each is run against
    the reference tree with REF's test file in place, which is how the
    harness runs a `hidden_ref` test. Every one that passes at the reference
    and fails after the revert is proposed.

    uv run python scripts/hidden_test_candidates.py --task replay-web-auth
    uv run python scripts/hidden_test_candidates.py --task replay-web-auth \\
        --later origin/main

It prints a per-test table and a `hidden_tests = [...]` line to paste into
tasks.toml. scripts/verify_replay_tasks.py then proves the pasted task:
visible and hidden pass at the reference, hidden twice identically, both
fail after the revert. The source is read as verify_replay_tasks.py reads it.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import logging
import math
import pathlib
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT = ROOT / "benchmarks" / "agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import replay
import run as harness
import verify_replay_tasks as verify

import logs

logger = logging.getLogger(__name__)


def test_ids(text: str, path: str) -> list[str]:
    """Every test function in a pytest file, as a function-level node id.

    Top-level `test*` functions and `test*` methods of `Test*` classes, in
    file order. Parametrized cases share their function's id: one case cannot
    be cut out of a file, so the function is the unit.
    """
    ids = []
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith("test"):
                ids.append(f"{path}::{node.name}")
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(
                    item, ast.FunctionDef | ast.AsyncFunctionDef
                ) and item.name.startswith("test"):
                    ids.append(f"{path}::{node.name}::{item.name}")
    return ids


def outcomes(junit: str, files: list[str]) -> dict[str, str]:
    """Function-level id -> "passed", "failed" or "skipped", from junit XML.

    A function passes only if every one of its cases passed; any failure or
    error fails it. An id with no case at all (its file failed to import)
    is simply absent -- the caller reads absent as not passed.
    """
    modules = {f.removesuffix(".py").replace("/", "."): f for f in files}
    got: dict[str, str] = {}
    for case in ET.fromstring(junit).iter("testcase"):
        classname, name = case.get("classname", ""), case.get("name", "")
        name = name.split("[", 1)[0]
        path, cls = None, None
        for module, file in modules.items():
            if classname == module:
                path = file
            elif classname.startswith(module + "."):
                path, cls = file, classname[len(module) + 1 :]
        if path is None:
            continue
        node = f"{path}::{cls}::{name}" if cls else f"{path}::{name}"
        if case.find("failure") is not None or case.find("error") is not None:
            state = "failed"
        elif case.find("skipped") is not None:
            state = "skipped"
        else:
            state = "passed"
        got[node] = max(got.get(node, state), state, key=_RANK.__getitem__)
    return got


#: Which case outcome decides a function's: any failure fails it, then any skip.
_RANK = {"passed": 0, "skipped": 1, "failed": 2}


def run_ids(
    tree: pathlib.Path, command: str, ids: list[str], files: list[str]
) -> dict[str, str]:
    """Each id's outcome, one pytest run per file.

    Per file, because one file that fails to import aborts a run that names
    node ids in the others, and a test in a file that imports fine can still
    pass after the revert -- which is exactly what must be seen.
    """
    got: dict[str, str] = {}
    for rel in files:
        mine = [i for i in ids if i.split("::")[0] == rel]
        if not mine:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "junit.xml"
            subprocess.run(
                [*command.split(), f"--junitxml={report}", *mine],
                cwd=tree,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                check=False,
                env={**harness.clean_env(), "UV_FROZEN": "1"},
                timeout=900,
            )
            if report.exists():
                got.update(outcomes(report.read_text(), [rel]))
    return got


def _key(node: str) -> str:
    return hashlib.sha256(node.encode()).hexdigest()


def pick(eligible: list[str], fraction: float) -> list[str]:
    """The `fraction` of `eligible` with the smallest sha256 of their id.

    At least one, and never all: a task whose every discriminating test is
    hidden leaves the agent a visible suite that already passes.
    """
    if len(eligible) < 2:
        return []
    n = min(len(eligible) - 1, max(1, math.ceil(len(eligible) * fraction)))
    return sorted(sorted(eligible, key=_key)[:n])


def collapse(chosen: list[str], every: list[str]) -> list[str]:
    """Replace a class whose every test method is chosen by the class id."""
    out, done = [], set()
    for node in chosen:
        parts = node.split("::")
        if len(parts) == 3:
            cls = "::".join(parts[:2])
            members = [n for n in every if n.startswith(cls + "::")]
            if all(m in chosen for m in members):
                if cls not in done:
                    out.append(cls)
                    done.add(cls)
                continue
        out.append(node)
    return out


def _under(node: str, visible: list[str]) -> bool:
    """Is `node` one of the tests the visible entries select?"""
    return any(node == v or node.startswith(v + "::") for v in visible)


def measure(
    cfg: dict,
    task: dict,
    source: pathlib.Path,
    work: pathlib.Path,
    later: str | None,
) -> dict:
    target = harness.task_target(cfg, task)
    commit, command = target["base_commit"], target["test_command"]
    files = sorted({t.split("::")[0] for t in task["tests"]})
    tree = work / task["name"]
    harness.build_checkout(source, commit, tree)
    if failed := verify.sync(tree):
        raise SystemExit(f"{task['name']}: {failed}")
    ref = replay.resolve(source, later) if later else replay.resolve(source, commit)
    parent = replay.parent_of(source, replay.start_of(task))
    ids, older = [], set()
    for rel in files:
        at_commit = test_ids((tree / rel).read_text(), rel)
        blob = replay.blob(source, ref, rel)
        at_ref = test_ids(blob.decode(), rel) if blob else []
        before = replay.blob(source, parent, rel)
        older |= set(test_ids(before.decode(), rel) if before else [])
        if later:
            ids += [i for i in at_ref if i not in at_commit]
        else:
            ids += [i for i in at_commit if _under(i, task["tests"])]
    if not ids:
        return {"ids": [], "at": {}, "after": {}, "ref": ref, "older": older}
    with replay.hidden_restored(tree, source, ref, ids):
        at = run_ids(tree, command, ids, files)
    replay.revert(source, commit, task["revert"], tree, task.get("span_start"))
    with replay.hidden_restored(tree, source, ref, ids):
        after = run_ids(tree, command, ids, files)
    return {"ids": ids, "at": at, "after": after, "ref": ref, "older": older}


def propose(got: dict, later: bool, fraction: float) -> list[str]:
    """The ids to hold out: pass at the reference, fail after the revert.

    A test the file already had before the work started is never one: it
    checks code the task did not ask for, and fails after the revert only
    when its file can no longer import something the work added.
    """
    eligible = [
        i
        for i in got["ids"]
        if got["at"].get(i) == "passed"
        and got["after"].get(i) != "passed"
        and i not in got.get("older", set())
    ]
    chosen = eligible if later else pick(eligible, fraction)
    return collapse(chosen, got["ids"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks-file", type=pathlib.Path, default=AGENT / "tasks.toml")
    parser.add_argument("--task", required=True)
    parser.add_argument("--source", type=pathlib.Path, help="repository to read")
    parser.add_argument("--later", help="propose tests added by this later ref")
    parser.add_argument("--fraction", type=float, default=1 / 3)
    args = parser.parse_args(argv)
    logs.configure()
    cfg = tomllib.loads(args.tasks_file.read_text())
    task = next((t for t in cfg["task"] if t["name"] == args.task), None)
    if task is None or not replay.is_replay(task):
        raise SystemExit(f"{args.task} is not a replay task in {args.tasks_file}")
    source = verify.source_for(cfg, task, args.source)
    with tempfile.TemporaryDirectory(prefix="hidden-candidates-") as tmp:
        got = measure(cfg, task, source, pathlib.Path(tmp), args.later)
    print("| test | at reference | after revert | older than the work |")
    print("|---|---|---|---|")
    for node in got["ids"]:
        at = got["at"].get(node, "not run")
        after = got["after"].get(node, "not collected")
        older = "yes" if node in got["older"] else ""
        print(f"| {node} | {at} | {after} | {older} |")
    chosen = propose(got, bool(args.later), args.fraction)
    print()
    if args.later:
        print(f'hidden_ref = "{got["ref"][:7]}"')
    print("hidden_tests = [")
    for node in chosen:
        print(f'    "{node}",')
    print("]")
    return 0 if chosen else 1


if __name__ == "__main__":
    raise SystemExit(main())
