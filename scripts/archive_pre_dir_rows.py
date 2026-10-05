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

A row the archive already holds is not archived twice (review of b7a366b,
finding 5). A merge can restore one to the live ledger, and so can a kill
between the archive write and the ledger write. The restored copy is removed
from the ledger and the archive keeps its one copy, because dirfix.py reads
the archive and would count a duplicate as a second trial. If the two copies
of one trial disagree in any byte, the archiver writes nothing and names them:
one copy is wrong, and choosing is a person's job.

Both files are replaced atomically (temp file, then `os.replace`), archive
first, under the ledger lock that `results.write_row` also takes.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import sys
from zoneinfo import ZoneInfo

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


#: The zone of a naive `started`. The archive holds M5 Max rows from before
#: #209, written naive in New York; backfill_iso8601.zone_for says the same
#: for every file outside hardware/.
NAIVE_ZONE = ZoneInfo("America/New_York")


def trial_key(line: str) -> tuple[object, ...]:
    """What makes two lines the same trial, whatever their bytes.

    The start instant, not its spelling: #209 rewrote the live ledgers'
    timestamps with offsets and left the archive as it was. A row with no
    `started` has no identity but its own bytes.
    """
    row = json.loads(line)
    started = row.get("started")
    if not isinstance(started, str):
        return ("line", line.rstrip("\n"))
    try:
        at = datetime.datetime.fromisoformat(started)
    except ValueError:
        instant: object = started
    else:
        if at.utcoffset() is None:
            at = at.replace(tzinfo=NAIVE_ZONE)
        instant = at.timestamp()
    return tuple(row.get(k) for k in ("backend", "client", "task", "trial")) + (
        instant,
    )


def unarchived(archived: list[str], move: list[str]) -> tuple[list[str], list[str]]:
    """Split `move` into (lines to append, lines that conflict).

    A line whose trial the archive (or an earlier line of `move`) already
    holds byte-for-byte is dropped: it is a restored copy. One whose trial is
    held with other bytes is a conflict.
    """
    held = {trial_key(x): x.rstrip("\n") for x in archived if x.strip()}
    fresh: list[str] = []
    conflicts: list[str] = []
    for line in move:
        key = trial_key(line)
        if key not in held:
            held[key] = line.rstrip("\n")
            fresh.append(line)
        elif held[key] != line.rstrip("\n"):
            conflicts.append(line)
    return fresh, conflicts


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

    # Held from the read to the last replace: a row run.py appends meanwhile
    # waits, rather than being missing from the rewritten ledger.
    with results.ledger_lock(RESULTS):
        return _archive(check=args.check)


def _archive(*, check: bool) -> int:
    lines = RESULTS.read_text().splitlines(keepends=True)
    move, keep = plan(lines, fixed_commits(ROOT))

    if not move:
        logger.info("nothing to archive; results.jsonl holds %d rows", len(keep))
        return 0
    if check:
        logger.error("%d pre---dir rows would move to %s", len(move), ARCHIVE)
        return 1

    existing = ARCHIVE.read_text() if ARCHIVE.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    fresh, conflicts = unarchived(existing.splitlines(keepends=True), move)
    if conflicts:
        for line in conflicts:
            logger.error("archived with different content: %s", line.rstrip())
        logger.error(
            "%d row(s) are in %s with other bytes; wrote nothing. Compare the "
            "two copies by hand.",
            len(conflicts),
            ARCHIVE,
        )
        return 1
    # Archive first. A kill between the two leaves rows in both files, and the
    # next run drops the live copies without archiving them again.
    if fresh:
        results.replace_ledger(ARCHIVE, existing + "".join(fresh))
    results.replace_ledger(RESULTS, "".join(keep))

    logger.info("archived %d rows -> %s", len(fresh), ARCHIVE)
    if len(fresh) < len(move):
        logger.info(
            "dropped %d restored row(s) the archive already held",
            len(move) - len(fresh),
        )
    logger.info("results.jsonl now holds %d rows", len(keep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
