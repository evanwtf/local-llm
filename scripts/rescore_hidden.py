"""Re-grade the hidden tests of saved replay patches with the current grader.

#801: `replay-web-auth-hidden` restored a held-out file that imported a name
the agent could not read, so the hidden run of almost every trial ended in a
collection error, not a verdict. The grader is fixed (`hidden_drop_imports`,
PR #867). This script gives the old trials the verdict they should have had.

For each ledger row of the task, it:

1. finds the row's saved patch and checks its sha256 against the row's
   `solution_sha256`. Patch names repeat across batches, so a later batch
   overwrites an earlier one; a mismatched patch is not that row's work, and
   the row is reported as unrecoverable;
2. rebuilds the tree the agent was handed, as the verifier does: the base
   commit, the revert, the hidden tests cut out;
3. applies the patch and restores the held-out file with the task's current
   rules, then runs the hidden tests.

The ledger is append-only, so nothing in it changes. Each re-grade is appended
to `--out` as its own record, keyed to the row it grades.

    uv run python scripts/rescore_hidden.py --task replay-web-auth-hidden \\
        --ledger hardware/<machine>/results.jsonl --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import pathlib
import subprocess
import sys
import tempfile
import tomllib
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENT = ROOT / "benchmarks" / "agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

import replay
import run as harness
import verify_replay_tasks as verifier

import logs

logger = logging.getLogger(__name__)

OUT = AGENT / "rescores" / "hidden.jsonl"


def key(row: dict[str, Any]) -> dict[str, Any]:
    """What identifies a ledger row for a re-grade record."""
    return {k: row.get(k) for k in ("task", "backend", "trial", "started")} | {
        "solution_sha256": row.get("solution_sha256")
    }


def match(row: dict[str, Any]) -> pathlib.Path | None:
    """The row's own saved patch, or None when it is gone or overwritten."""
    raw = row.get("solution_patch")
    if not raw or not row.get("solution_sha256"):
        return None
    path = pathlib.Path(raw).expanduser()
    if not path.is_file():
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != row["solution_sha256"]:
        return None
    return path


def rows_for(ledger: pathlib.Path, task: str) -> list[dict[str, Any]]:
    out = []
    for line in ledger.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("task") == task and row.get("hidden"):
            out.append(row)
    return out


def regrade(
    cfg: dict[str, Any],
    task: dict[str, Any],
    source: pathlib.Path,
    patch: pathlib.Path,
    work: pathlib.Path,
) -> dict[str, Any]:
    """Rebuild the handed tree, apply the patch, run the hidden tests."""
    target = harness.task_target(cfg, task)
    commit = target["base_commit"]
    hidden = task["hidden_tests"]
    harness.build_checkout(source, commit, work)
    replay.revert(source, commit, task["revert"], work, task.get("span_start"))
    replay.hide(work, hidden)
    # build_checkout exports with `git archive`: no .git, by design. `git
    # apply` outside a repository patches the files directly.
    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(patch)],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )
    if applied.returncode != 0:
        return {"error": f"git apply failed: {applied.stderr.strip()[-200:]}"}
    if failed := verifier.sync(work):
        return {"error": failed}
    ref = replay.resolve(source, task.get("hidden_ref") or commit)
    drop = task.get("hidden_drop_imports") or []
    with replay.hidden_restored(work, source, ref, hidden, drop):
        got = verifier.run_tests(work, target["test_command"], hidden)
    return {
        "hidden_ref": ref,
        "drop_imports": drop,
        "hidden_counts": {k: got[k] for k in ("passed", "failed", "skipped", "errors")},
        "hidden_passed": got["returncode"] == 0 and got["passed"] > 0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--task", required=True)
    ap.add_argument("--ledger", type=pathlib.Path, required=True)
    ap.add_argument("--tasks-file", type=pathlib.Path, default=AGENT / "tasks.toml")
    ap.add_argument("--source", type=pathlib.Path, help="repository to read")
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    ap.add_argument("--reason", default="#801: held-out import fixed (PR #867)")
    ap.add_argument("--dry-run", action="store_true", help="grade; append nothing")
    args = ap.parse_args(argv)
    logs.configure()
    cfg = tomllib.loads(args.tasks_file.read_text())
    task = next((t for t in cfg["task"] if t["name"] == args.task), None)
    if task is None or "hidden_tests" not in task:
        raise SystemExit(f"{args.task}: no such task with hidden tests")
    source = verifier.source_for(cfg, task, args.source)
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
        text=True, check=False,
    ).stdout.strip()  # fmt: skip
    records, lost = [], 0
    rows = rows_for(args.ledger, args.task)
    for row in rows:
        patch = match(row)
        label = f"{row.get('backend')} trial {row.get('trial')} {row.get('started')}"
        if patch is None:
            lost += 1
            logger.info("%s: unrecoverable (patch gone or overwritten)", label)
            continue
        with tempfile.TemporaryDirectory(prefix="rescore-") as tmp:
            got = regrade(cfg, task, source, patch, pathlib.Path(tmp) / "t")
        before = (row.get("hidden") or {}).get("pytest")
        logger.info("%s: was %r -> %s", label, before, got)
        now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        records.append(
            {"row": key(row), "reason": args.reason, "harness_head": head,
             "rescored_at": now, "was": before} | got
        )  # fmt: skip
    logger.info(
        "%d rows: %d re-graded, %d unrecoverable", len(rows), len(records), lost
    )
    if records and not args.dry_run:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a") as f:
            for r in records:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        logger.info("appended %d records to %s", len(records), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
