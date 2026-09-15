"""Move every pre---dir OpenCode row out of results.jsonl into the archive.

Those trials ran a client that was never told which directory to work in
(#67). `opencode run` attaches to a persistent server holding its own cwd, so
the client solved the task and wrote the answer into the launcher's directory.
Every one of them measures this harness, not OpenCode.

They are archived rather than deleted: they are still real records of what the
harness did, and the before/after comparison in `dirfix.py` reads them.

Lines are moved BYTE-IDENTICAL. This never edits a recorded measurement -- a
previous attempt to recompute a stored field corrupted 30 rows, and the rule
since is to annotate or relocate, never to rewrite.

Idempotent: running it twice moves nothing the second time. `--check` plans
the move, writes nothing, and exits 1 if a move is due.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: The ledger moved to hardware/<machine>/results.jsonl (#20) and this script
#: kept a literal `benchmarks/agent/results.jsonl` relative to the CALLER's
#: cwd, so it raised FileNotFoundError from anywhere and archived nothing from
#: the repo root. It was the only thing enforcing the pre---dir invariant, and
#: on 2026-09-07 a branch merge restored 90 archived rows to the live ledger
#: with nothing to notice. Ask results.py where the file is, and anchor the
#: archive to the repo rather than to wherever it was invoked.
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import dirfix
import results

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

RESULTS = results.default_path()
ARCHIVE = ROOT / "docs" / "archive" / "results-opencode-pre-dir.jsonl"

#: #392: the classifier is dirfix's, not a copy. This script once kept its own
#: fixed_commits() without dirfix's #355 allowlist and archived 65 post-fix
#: rows. The names stay here because scripts/validate_ledgers.py calls
#: `apdr.fixed_commits`.
FIX = dirfix.FIX
fixed_commits = dirfix.fixed_commits


def is_pre_dir(line: str, after: set[str]) -> bool:
    """A row this fix invalidates: an OpenCode trial from before `--dir`.

    A row with no `harness_head` predates the provenance field entirely, which
    dates it before the fix.
    """
    row = json.loads(line)
    return row.get("client") == "opencode" and dirfix.era(row, after) == "before"


def plan(lines: list[str], after: set[str]) -> tuple[list[str], list[str]]:
    """Split ledger lines into (move, keep), in order, without writing."""
    move = [x for x in lines if is_pre_dir(x, after)]
    keep = [x for x in lines if not is_pre_dir(x, after)]
    # Every line must land in exactly one list, unchanged.
    assert len(move) + len(keep) == len(lines), "row lost or duplicated"
    return move, keep


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--check",
        action="store_true",
        help="plan the move, write nothing, exit 1 if any row would move",
    )
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)

    # No ledger for THIS machine is the normal case everywhere except the one
    # that took the measurements. `results.default_path()` is derived from the
    # host's own hardware (#20), so on a CI runner it names a directory that
    # has never existed. That is nothing to archive, not an error -- and the
    # crash it used to raise turned a green build red on 2026-09-07.
    if not RESULTS.exists():
        logger.info("no ledger at %s; nothing to archive on this machine", RESULTS)
        return 0

    lines = RESULTS.read_text().splitlines(keepends=True)
    move, keep = plan(lines, fixed_commits(ROOT))

    if not move:
        logger.info("nothing to archive; results.jsonl holds %d rows", len(keep))
        return 0
    if args.check:
        logger.error("%d pre---dir rows would move to %s", len(move), ARCHIVE)
        return 1

    existing = ARCHIVE.read_text() if ARCHIVE.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    ARCHIVE.write_text(existing + "".join(move))
    RESULTS.write_text("".join(keep))

    logger.info("archived %d rows -> %s", len(move), ARCHIVE)
    logger.info("results.jsonl now holds %d rows", len(keep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
