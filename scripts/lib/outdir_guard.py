"""Refuse to run an A/B into an output directory that already holds reps (#208).

Every driver creates its outdir with `mkdir(parents=True, exist_ok=True)`,
which succeeds on an existing directory and changes nothing. A rerun then
overwrites the reps it produces and leaves every rep it does not. A 4-rep rerun
into a 6-rep directory gives four new reps and two old ones, from different
builds and days, and `decode_ab_report.py` reports one median over all six. On
2026-09-07 the two old reps belonged to a run already marked VOID.

The guard runs before the lock, so a refusal costs nothing.
"""

from __future__ import annotations

import datetime
import pathlib

#: The flag every driver takes to opt out, for resuming an interrupted run.
REUSE_FLAG = "--reuse-outdir"


class ReusedOutdir(Exception):
    """The outdir already holds reps from an earlier run."""


def void_marker(out: pathlib.Path) -> pathlib.Path:
    """The VOID note for `out`: `<name>-VOID.md` beside the directory."""
    return out.parent / f"{out.name}-VOID.md"


def refuse_reused_outdir(out: pathlib.Path, *, reuse: bool = False) -> None:
    """Raise ReusedOutdir if `out` already holds a `*.csv` and `reuse` is False.

    A missing directory, or one without CSVs, passes. `decode_ab_repeat.py`
    creates each run directory and writes `start-state.txt` before it calls a
    driver, so only CSVs count as earlier reps.
    """
    if reuse or not out.is_dir():
        return
    csvs = sorted(out.glob("*.csv"), key=lambda p: p.stat().st_mtime)
    if not csvs:
        return
    newest = csvs[-1]
    when = (
        datetime.datetime.fromtimestamp(newest.stat().st_mtime, datetime.UTC)
        .astimezone()
        .isoformat(timespec="seconds")
    )
    message = (
        f"{out} already holds {len(csvs)} CSV file(s); the newest is "
        f"{newest.name}, modified {when}. A rerun overwrites the reps it "
        "produces and keeps the rest, and the report takes one median over "
        "both runs. Use a new directory, or pass "
        f"{REUSE_FLAG} to resume an interrupted run on purpose."
    )
    marker = void_marker(out)
    if marker.exists():
        message += f" {marker.name} is beside it: the data there is VOID, not only old."
    raise ReusedOutdir(message)
