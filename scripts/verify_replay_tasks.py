"""Check that each replay task (#714) is a valid task before any trial runs it.

A replay task is valid when its tests pass on the commit's own tree and fail
once the reverted files are put back to the parent -- the control check every
trial repeats, done here once per task with the counts written down. For
each task this:

1. exports the tree at `base_commit` (the harness's own `build_checkout`),
2. runs `uv sync --frozen`, then the task's tests: they must pass,
3. applies the revert (the harness's own `replay.revert`), and runs them
   again: they must fail.

It prints one markdown row per task: commit, lines the commit changed in the
reverted files, how many files are reverted, tests run and skipped at the
commit, and passed / failed / errors after the revert. A test file whose
tests all skip (they need GMAIL_ARCHIVE_TEST_DATABASE_URL) shows up as a skip
count, which is the thing to look for before adding a task.

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
import re
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

COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed)\b")


def counts(output: str) -> dict[str, int]:
    """pytest's closing summary as numbers: passed, failed, skipped, errors.

    Reads the last line that carries any count, so a test's own output that
    happens to say "3 passed" earlier cannot be mistaken for the summary.
    """
    got = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for line in reversed(output.splitlines()):
        found = COUNT.findall(line)
        if found:
            for n, word in found:
                key = "errors" if word.startswith("error") else word
                if key in got:
                    got[key] += int(n)
            break
    return got


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


def verify(cfg: dict, task: dict, source: pathlib.Path, work: pathlib.Path) -> dict:
    """Every number for one task's row, and whether the task is valid."""
    if errors := replay.validate(task):
        return {"task": task["name"], "valid": False, "why": "; ".join(errors)}
    target = harness.task_target(cfg, task)
    commit = target["base_commit"]
    tree = work / task["name"]
    harness.build_checkout(source, commit, tree)
    sync = subprocess.run(
        ["uv", "sync", "--frozen", "--quiet"],
        cwd=tree,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        env=harness.clean_env(),
    )
    if sync.returncode != 0:
        return {
            "task": task["name"],
            "valid": False,
            "why": f"uv sync --frozen failed: {sync.stderr.strip()[-200:]}",
        }
    at_commit = run_tests(tree, target["test_command"], task["tests"])
    reverted = replay.revert(source, commit, task["revert"], tree)
    after = run_tests(tree, target["test_command"], task["tests"])
    size = replay.commit_size(source, commit, task["revert"])
    passed_at_commit = at_commit["returncode"] == 0 and at_commit["passed"] > 0
    fails_after = after["returncode"] != 0
    why = []
    if not passed_at_commit:
        why.append("tests do not pass at the commit")
    if not fails_after:
        why.append("tests still pass after the revert")
    return {
        "task": task["name"],
        "commit": replay.resolve(source, commit)[:7],
        "added": size["added"],
        "removed": size["removed"],
        "reverted": len(reverted),
        "deleted": sum(r["action"] == "deleted" for r in reverted),
        "at_commit": at_commit,
        "after": after,
        "valid": not why,
        "why": "; ".join(why),
    }


def row(got: dict) -> str:
    if "commit" not in got:
        return f"| {got['task']} | - | - | - | - | - | INVALID: {got['why']} |"
    c, a = got["at_commit"], got["after"]
    run_at_c = c["passed"] + c["failed"]
    verdict = "ok" if got["valid"] else f"INVALID: {got['why']}"
    return (
        f"| {got['task']} | {got['commit']} | +{got['added']}/-{got['removed']} "
        f"| {got['reverted']} ({got['deleted']} new) "
        f"| {run_at_c} run / {c['skipped']} skip "
        f"| {c['passed']} passed "
        f"| {a['passed']} passed, {a['failed']} failed, {a['errors']} errors "
        f"| {verdict} |"
    )


HEADER = (
    "| task | commit | reverted lines | reverted files | tests at commit "
    "| pass at commit | after revert | verdict |\n"
    "|---|---|---|---|---|---|---|---|"
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
