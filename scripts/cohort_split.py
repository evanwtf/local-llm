"""Split one backend's rows at a moment in time and compare the halves.

The recurring question in this repo is "did that change help?", and the
recurring mistake is answering it by comparing rows either side of the commit
without checking what else moved that day. #112 is the current example:
remedy 2 shipped in f2fcc1f at 04:23 on 2026-09-03, and the measurement
protocol changed from a continuous server to restart-between-trials the same
day. The pass rates either side differ. Neither number measures the remedy.

So this prints the split **and the confounds beside it** -- every distinct
client version, harness commit and context size in each half. A split whose
halves differ in more than the one thing is not evidence about that thing,
and the only way to keep noticing that is to have it printed every time.

    uv run python scripts/cohort_split.py <results.jsonl> \\
        --backend qwen38fnds4shim --at 2026-09-03T04:23:09-04:00
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import logging
import pathlib
import sys
from typing import Any

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import results as results_mod

logger = logging.getLogger(__name__)

#: Fields whose spread within a half is a confound worth printing. Each of
#: these has silently split a comparison in this repo at least once.
CONFOUNDS = ("client_version", "context_tokens", "model")


def started(row: dict[str, Any]) -> dt.datetime | None:
    raw = row.get("started")
    if not isinstance(raw, str):
        return None
    try:
        return dt.datetime.fromisoformat(raw)
    except ValueError:
        return None


def split(
    rows: list[dict[str, Any]], moment: dt.datetime
) -> tuple[list[dict], list[dict], list[dict]]:
    """(before, after, undated). Undated rows are reported, never assigned."""
    before, after, undated = [], [], []
    for row in rows:
        when = started(row)
        if when is None:
            undated.append(row)
            continue
        # Compare naively when either side lacks a timezone: the ledger is
        # written in local time and a mixed comparison raises rather than
        # silently misfiling a row.
        left = when if when.tzinfo else when.replace(tzinfo=moment.tzinfo)
        (before if left < moment else after).append(row)
    return before, after, undated


def describe(name: str, rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return [f"{name}: no rows"]
    passed = sum(1 for r in rows if r.get("passed"))
    out = [f"{name}: {passed}/{len(rows)} passed ({passed / len(rows):.1%})"]
    for field in CONFOUNDS:
        seen = collections.Counter(str(r.get(field)) for r in rows)
        shape = ", ".join(f"{k}×{v}" for k, v in sorted(seen.items()))
        flag = "  <-- SPLIT" if len(seen) > 1 else ""
        out.append(f"    {field}: {shape}{flag}")
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("ledger", type=pathlib.Path)
    p.add_argument("--backend", required=True)
    p.add_argument("--at", required=True, help="ISO timestamp of the change")
    args = p.parse_args(argv)

    moment = dt.datetime.fromisoformat(args.at)
    rows = [
        r for r in results_mod.trials(args.ledger) if r.get("backend") == args.backend
    ]
    before, after, undated = split(rows, moment)
    logger.info("%s, %d usable rows, split at %s", args.backend, len(rows), moment)
    logger.info("")
    for line in describe("BEFORE", before):
        logger.info("%s", line)
    logger.info("")
    for line in describe("AFTER", after):
        logger.info("%s", line)
    if undated:
        logger.warning(
            "%d rows carry no readable 'started' and were not split", len(undated)
        )
    logger.info("")
    logger.info(
        "A difference here is evidence about the change ONLY if the confound "
        "lines above show no SPLIT. Otherwise it is evidence about the day."
    )
    # The dangerous case is not a confound this script prints. It is one it
    # cannot see. #112 is exactly that: client_version, context_tokens and
    # model are constant across the split, so the lines above read clean --
    # while the measurement protocol changed from a continuous server to
    # restart-between-trials on the same day, worth +6 passes on its own, and
    # nothing in a row records which protocol produced it. A reader who stops
    # at "no SPLIT" concludes the opposite of the truth.
    logger.warning(
        "NOT CHECKED, because no row records it: the measurement protocol "
        "(continuous server vs restart-between-trials), the shim revision, "
        "and anything else changed by hand that day. Read the changelog for "
        "the split date before believing this table."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
