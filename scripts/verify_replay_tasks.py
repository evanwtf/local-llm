"""Check that each replay task (#714, #726) is a valid task before any trial runs it.

A replay task is valid when its tests pass on the commit's own tree and fail
once the reverted files are put back -- the control check every trial
repeats, done here once per task with the counts written down. For each task
this:

1. exports the tree at `base_commit` (the harness's own `build_checkout`),
2. runs `uv sync --frozen`,
3. hides the task's held-out tests, if it has any (the harness's own
   `replay.hide`), and proves the tree no longer defines them,
4. runs the visible tests: they must pass,
5. puts the held-out tests back (from `hidden_ref`, the way a trial does) and
   runs them twice: both runs must pass with the same counts, or the hidden
   verdict would be noise,
6. applies the revert (the harness's own `replay.revert`, from `span_start^`
   for a span), and runs the visible tests again: they must fail; and the
   held-out tests: they must fail too, or they check nothing the agent did.

It prints one markdown row per task: commit(s), lines the work changed in the
reverted files, how many files are reverted, visible and held-out tests run
and skipped at the reference, and passed / failed / errors after the revert.
A test file whose tests all skip (they need GMAIL_ARCHIVE_TEST_DATABASE_URL)
shows up as a skip count, which is the thing to look for before adding a task.

    uv run python scripts/verify_replay_tasks.py                 # every replay task
    uv run python scripts/verify_replay_tasks.py --task replay-defang
    uv run python scripts/verify_replay_tasks.py --source /path/to/clone

The source is `--source`, else the task's sandbox clone
(scripts/sync_sandbox_targets.py), else the configured checkout. Only its
object store is read (`git archive`, `git cat-file`); nothing is written to it.
Exit status is non-zero when any task is invalid.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import subprocess
import sys
import tempfile
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT = ROOT / "benchmarks" / "agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import replay
import run as harness

import logs

logger = logging.getLogger(__name__)

#: One parser for pytest's summary, shared with the harness's hidden oracle.
counts = replay.counts


def run_tests(tree: pathlib.Path, command: str, tests: list[str]) -> dict[str, int]:
    proc = subprocess.run(
        [*command.split(), *tests],
        cwd=tree,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        env={**harness.clean_env(), "UV_FROZEN": "1"},
        timeout=900,
    )
    got = counts(proc.stdout + "\n" + proc.stderr)
    got["returncode"] = proc.returncode
    return got


def source_for(cfg: dict, task: dict, override: pathlib.Path | None) -> pathlib.Path:
    if override is not None:
        return override
    target = harness.task_target(cfg, task)
    repo = pathlib.Path(target["repo"]).expanduser()
    return harness.sandbox_checkout(repo, target["sandbox"]) or harness.guarded_repo(
        repo
    )


def sync(tree: pathlib.Path) -> str | None:
    """Build the tree's environment from its lock. Why it failed, or None."""
    got = subprocess.run(
        ["uv", "sync", "--frozen", "--quiet"],
        cwd=tree,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        env=harness.clean_env(),
    )
    if got.returncode != 0:
        return f"uv sync --frozen failed: {got.stderr.strip()[-200:]}"
    return None


def _passes(got: dict[str, int]) -> bool:
    return got["returncode"] == 0 and got["passed"] > 0


def _check_hidden(
    task: dict, source: pathlib.Path, tree: pathlib.Path, command: str, ref: str
) -> tuple[dict, list[str]]:
    """Hide, prove hidden, and run the held-out tests twice at the reference."""
    hidden = task["hidden_tests"]
    why = []
    hid = replay.hide(tree, hidden)
    if left := replay.still_visible(tree, hidden):
        why.append(f"hidden tests still visible: {', '.join(left)}")
    runs = []
    for _ in range(2):
        with replay.hidden_restored(tree, source, ref, hidden):
            runs.append(run_tests(tree, command, hidden))
    first, second = runs
    if not _passes(first):
        why.append("hidden tests do not pass at the reference")
    if first != second:
        why.append("hidden tests are not deterministic (two runs differ)")
    return {
        "hidden_ids": len(hidden),
        "hid": hid,
        "hidden_at": first,
        "hidden_repeat_same": first == second,
    }, why


def visible_text(task: dict, tree: pathlib.Path) -> str:
    """Everything the agent can read that could name an API: the prompt and
    every Python file in its tree, tests and source alike."""
    parts = [task["prompt"]]
    for root in ("src", "tests"):
        for path in sorted((tree / root).rglob("*.py")):
            parts.append(path.read_text(errors="replace"))
    return "\n".join(parts)


def unseen(
    task: dict, source: pathlib.Path, tree: pathlib.Path, ref: str, project: set[str]
) -> dict[str, list[str]]:
    """replay.unseen_api for this task, on the tree as the agent gets it.

    `project` is the reference tree's own names, taken before the revert.
    """
    hidden = {}
    for node in task["hidden_tests"]:
        blob = replay.blob(source, ref, node.split("::")[0])
        hidden[node] = (blob or b"").decode(errors="replace")
    return replay.unseen_api(hidden, visible_text(task, tree), project)


def verify(cfg: dict, task: dict, source: pathlib.Path, work: pathlib.Path) -> dict:
    """Every number for one task's row, and whether the task is valid."""
    if errors := replay.validate(task):
        return {"task": task["name"], "valid": False, "why": "; ".join(errors)}
    target = harness.task_target(cfg, task)
    commit = target["base_commit"]
    command = target["test_command"]
    start = task.get("span_start")
    tree = work / task["name"]
    harness.build_checkout(source, commit, tree)
    if failed := sync(tree):
        return {"task": task["name"], "valid": False, "why": failed}
    why = []
    got: dict = {"task": task["name"]}
    ref = None
    project = replay.project_names(tree)
    if "hidden_tests" in task:
        # Hide first: the visible run below must pass on the tree the agent
        # is handed, with the held-out tests cut out of it.
        ref = replay.resolve(source, task.get("hidden_ref") or commit)
        extra, problems = _check_hidden(task, source, tree, command, ref)
        got.update(extra)
        why += problems
    at_commit = run_tests(tree, command, task["tests"])
    reverted = replay.revert(source, commit, task["revert"], tree, start)
    after = run_tests(tree, command, task["tests"])
    if ref is not None:
        # Measured on the tree exactly as the agent is handed it: hidden
        # tests cut, work reverted.
        if missing := unseen(task, source, tree, ref, project):
            why.append(
                "hidden tests use names the agent cannot read: "
                + "; ".join(f"{k} ({', '.join(v)})" for k, v in missing.items())
            )
        with replay.hidden_restored(tree, source, ref, task["hidden_tests"]):
            got["hidden_after"] = run_tests(tree, command, task["hidden_tests"])
        if got["hidden_after"]["returncode"] == 0:
            why.append("hidden tests still pass after the revert")
    size = replay.commit_size(source, commit, task["revert"], start)
    if not _passes(at_commit):
        why.insert(0, "tests do not pass at the commit")
    if after["returncode"] == 0:
        why.append("tests still pass after the revert")
    got.update(
        {
            "commit": replay.resolve(source, commit)[:7],
            "span": (
                f"{replay.resolve(source, start)[:7]}..{replay.resolve(source, commit)[:7]}"
                f" ({replay.span_commits(source, start, commit)} commits)"
                if start
                else None
            ),
            "added": size["added"],
            "removed": size["removed"],
            "reverted": len(reverted),
            "deleted": sum(r["action"] == "deleted" for r in reverted),
            "at_commit": at_commit,
            "after": after,
            "valid": not why,
            "why": "; ".join(why),
        }
    )
    return got


def _counted(c: dict[str, int]) -> str:
    return f"{c['passed']} passed, {c['failed']} failed, {c['errors']} errors"


def row(got: dict) -> str:
    if "commit" not in got:
        return f"| {got['task']} | - | - | - | - | - | - | - | INVALID: {got['why']} |"
    c, a = got["at_commit"], got["after"]
    run_at_c = c["passed"] + c["failed"]
    verdict = "ok" if got["valid"] else f"INVALID: {got['why']}"
    if "hidden_at" in got:
        h, ha = got["hidden_at"], got["hidden_after"]
        repeat = "same twice" if got["hidden_repeat_same"] else "DIFFERS"
        hidden = (
            f"{got['hidden_ids']} ids: {h['passed'] + h['failed']} run / "
            f"{h['skipped']} skip"
        )
        hidden_at = f"; hidden {h['passed']} passed ({repeat})"
        hidden_after = f"; hidden {_counted(ha)}"
    else:
        hidden, hidden_at, hidden_after = "-", "", ""
    return (
        f"| {got['task']} | {got.get('span') or got['commit']} "
        f"| +{got['added']}/-{got['removed']} "
        f"| {got['reverted']} ({got['deleted']} new) "
        f"| {run_at_c} run / {c['skipped']} skip "
        f"| {hidden} "
        f"| {c['passed']} passed{hidden_at} "
        f"| {_counted(a)}{hidden_after} "
        f"| {verdict} |"
    )


HEADER = (
    "| task | commit(s) | reverted lines | reverted files | visible tests at ref "
    "| hidden tests at ref | pass at ref | after revert | verdict |\n"
    "|---|---|---|---|---|---|---|---|---|"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks-file", type=pathlib.Path, default=AGENT / "tasks.toml")
    parser.add_argument("--task", action="append", help="repeatable; default all")
    parser.add_argument("--source", type=pathlib.Path, help="repository to read")
    args = parser.parse_args(argv)
    logs.configure()
    cfg = tomllib.loads(args.tasks_file.read_text())
    tasks = [
        t
        for t in cfg["task"]
        if replay.is_replay(t) and (not args.task or t["name"] in args.task)
    ]
    if not tasks:
        raise SystemExit("no replay tasks selected")
    rows = []
    with tempfile.TemporaryDirectory(prefix="verify-replay-") as tmp:
        for task in tasks:
            source = source_for(cfg, task, args.source)
            logger.info("%s: verifying from %s", task["name"], source)
            got = verify(cfg, task, source, pathlib.Path(tmp))
            logger.info("%s", row(got))
            rows.append(got)
    print(HEADER)
    for got in rows:
        print(row(got))
    return 0 if all(g["valid"] for g in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
