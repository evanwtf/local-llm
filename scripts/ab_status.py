#!/usr/bin/env python3
"""One status line for a set of decode-A/B run directories.

Port of `scripts/ab_status.sh` (#235).

Exists so a status monitor does not carry its own idea of what "complete"
means. Three separate copies of that rule drifted apart in one afternoon
(a03ca8d); this delegates to `post_ab_run.is_complete` like everything else.

    uv run python scripts/ab_status.py benchmarks/ds4/pr621-recheck-run

Exit 10 when every wanted run is complete, so a waiting loop can branch on the
status rather than parse the line.

## Two fields that printed the wrong thing

The shell's own docstring set the rule -- *"a field that cannot be computed
says so rather than printing empty: a blank where a number belongs is
indistinguishable from 'nothing to report', which is how a broken status line
goes unnoticed"* -- and two fields broke it in opposite directions.

**The row count printed two lines.** `grep -c` exits 1 when it matches
nothing, so `$(... | grep -c '^[0-9]' || echo 0)` ran BOTH branches: grep
printed `0` and the fallback printed `0` again, and the variable held
`"0\\n0"`. A one-line status line became two, in the case that means "the run
has not written a row yet" -- the case a monitor is watching for.

**The die temperature printed the error text.** `TEMP=$(... | sed -E 's/.*"die_max_c": ([0-9.]+).*/\\1C/') || TEMP="unreadable"`
never took its fallback: `sed` succeeds when its pattern does not match and
passes the line through unchanged, so a failing `thermals.py` put its own last
line of output into the `die=` field, where it reads as a temperature.

Both were confirmed by running them, not by reading them. Here `rows()` and
`die_temp()` return `None` and the caller renders `unreadable`, once, in one
place.

**And the lock field watched a file nobody writes.** The shell tested
`[ -f .run-lock.json ]` in the checkout. The lock moved to
`~/.local-llm-bench/run-lock.json`, and `preflight.warn_legacy_lock` exists
precisely because a lock left in a checkout is a pre-fix artifact -- so this
field has read `free` on every run since, whatever was on the machine. It
was the only live reader of that path left in the repo. The lock is now
`machine_state.lock_claim()`, which also distinguishes a holder that is
running from one that is gone: the shell's two words could not say `stale`,
and "wait for it" and "something died" are not the same instruction.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import pathlib
import sys
from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import decode_ab_report
import machine_state
import post_ab_run
import thermals

import logs

logger = logging.getLogger(__name__)

WANT_RUNS = 4

#: How `machine_state`'s verdict on the lock is spelled in the status line.
#: The shell said held or free; a lock whose holder is gone is neither, and
#: saying so is the difference between "wait" and "something died".
LOCK_WORD = {
    machine_state.RUNNING: "held",
    machine_state.MISSING: "free",
    machine_state.STALE: "stale",
    machine_state.UNCONFIRMED: "uncertain",
    machine_state.REUSED: "uncertain",
}

#: What a computable field says when it is not computable. One spelling, so a
#: reader can tell "not available" from a value in every field at once.
UNREADABLE = "unreadable"


def run_dirs(prefix: str) -> list[pathlib.Path]:
    """Directories matching `<prefix>*`, in name order.

    Sorted rather than glob order: the shell used `ls -d ... | tail -1` for
    "the current run", which is name order on macOS, and a monitor whose
    "current run" changes with the filesystem is worse than no monitor.
    """
    path = pathlib.Path(prefix)
    return sorted(p for p in path.parent.glob(path.name + "*") if p.is_dir())


def complete(dirs: Sequence[pathlib.Path]) -> list[pathlib.Path]:
    """The runs `post_ab_run` calls finished. One owner for that rule."""
    return [d for d in dirs if post_ab_run.is_complete(d)]


def rows(run: pathlib.Path | None) -> int | None:
    """Data rows across a run's CSVs, or None when there are none to count.

    A header-only CSV is zero rows, which is a number. A run directory that
    does not exist is None, which is not.
    """
    if run is None or not run.is_dir():
        return None
    csvs = sorted(run.glob("*.csv"))
    if not csvs:
        return None
    total = 0
    for csv in csvs:
        for line in csv.read_text(errors="replace").splitlines():
            if line[:1].isdigit():
                total += 1
    return total


def die_temp() -> float | None:
    """Peak die temperature, or None. Never the text of a failure."""
    try:
        got = thermals.reading()
    # Broad on purpose: a status line must not die of a sensor.
    except Exception:
        logger.debug("thermals unavailable", exc_info=True)
        return None
    value = got.get("die_max_c")
    return float(value) if isinstance(value, int | float) else None


class _Capture(logging.Handler):
    """Collect a report's own lines instead of shelling out for them.

    The shell ran `uv run python scripts/decode_ab_report.py $COMPLETE` and
    grepped stdout, paying an interpreter start per status line. The report
    writes through `logging`, so a handler reads the same words in-process.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def report_lines(dirs: Sequence[pathlib.Path]) -> tuple[list[str], int]:
    """(the report's lines, its exit status).

    The capture REPLACES the root handlers for the duration rather than
    joining them. Adding one leaves the report's own output on stdout, and
    the first version of this did exactly that: a script whose whole promise
    is one status line printed 118 lines of A/B tables and then the line.
    The shell never had the bug because a command substitution captures
    stdout by taking it away.

    `logs.configure` defaults `force=False`, so the report's own call to it
    finds a configured root and leaves this handler alone -- which is the
    reason that default exists. The same no-op means it does not set the
    LEVEL either, so the level is set here: a root left at WARNING drops
    every line the report writes, and the capture then reads as "the report
    said nothing" rather than as "nobody was listening".
    """
    capture = _Capture()
    root = logging.getLogger()
    saved, saved_level = root.handlers[:], root.level
    root.handlers = [capture]
    root.setLevel(min(root.getEffectiveLevel(), logging.INFO))
    try:
        status = decode_ab_report.main(["decode_ab_report", *[str(d) for d in dirs]])
    finally:
        root.handlers, root.level = saved, saved_level
    return capture.lines, status


def median_line(dirs: Sequence[pathlib.Path]) -> str:
    """The report's own median line, or why there is not one.

    Two spellings are searched because decode_ab_report prints one shape for a
    multi-run comparison and another for a single paired run. Both are the
    REPORT's words: this computes no ratio of its own, which is the whole
    reason the script delegates rather than counting.
    """
    if not dirs:
        return "no complete run yet"
    try:
        lines, status = report_lines(dirs)
    # Broad on purpose: a status line must not die of the report it delegates
    # to. It is reported in the field a reader is already looking at, which is
    # more use than a traceback where a ratio belongs.
    except Exception as exc:
        logger.debug("decode_ab_report raised", exc_info=True)
        return f"REPORT FAILED: {exc}"
    if status != 0:
        return f"REPORT FAILED: {lines[-1] if lines else f'exit {status}'}"
    for want in ("runs: median", "paired median"):
        for line in lines:
            if want in line:
                return line.strip()
    return "report gave no median line"


def line(prefix: str, want: int = WANT_RUNS) -> tuple[str, int]:
    """The status line and the exit code that goes with it."""
    dirs = run_dirs(prefix)
    done = complete(dirs)
    current = dirs[-1] if dirs else None
    count = rows(current)
    temp = die_temp()
    now = dt.datetime.now().astimezone().strftime("%H:%M")
    lock = LOCK_WORD.get(machine_state.lock_claim().status, UNREADABLE)
    text = (
        f"{now} | {len(done)}/{want} complete | "
        f"{current.name if current else UNREADABLE} at "
        f"{count if count is not None else UNREADABLE} rows | "
        f"lock={lock} "
        f"die={f'{temp}C' if temp is not None else UNREADABLE} | "
        f"{median_line(done)}"
    )
    return text, (10 if len(done) >= want else 0)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("prefix", help="run directory prefix, e.g. .../pr621-recheck-run")
    p.add_argument("--want", type=int, default=WANT_RUNS)
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    text, code = line(args.prefix, args.want)
    logger.info("%s", text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
