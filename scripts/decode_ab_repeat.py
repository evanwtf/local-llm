#!/usr/bin/env python3
"""Run the same decode A/B N times, into numbered directories (#136).

Port of `scripts/decode_ab_repeat.sh` (#235).

One run of an A/B is not a measurement. Four runs of the #118 comparison
returned +16.5%, +21.2%, +17.6% and +17.7% on identical inputs -- a 4.7 pp
spread, against a within-run repeat spread of 4.4 pp at a single frontier.
A single run's internal agreement is not precision, and nothing showed that
until the same A/B was deliberately run more than once.

Each repetition is a separate invocation of the underlying harness, so each
takes and releases the run lock (#133) in turn, and each alternates arm
order internally (#130). Runs are sequential: two at once would measure
contention.

    uv run python scripts/decode_ab_repeat.py <n> <outdir-prefix> <harness> [args...]

Example -- four runs of the q4/q8 A/B at the #952 engine:

    DS4=~/git/ds4-pr621 CTX_MAX=65536 \\
        uv run python scripts/decode_ab_repeat.py 4 benchmarks/ds4/pr621-recheck \\
        scripts/decode_ab.sh q4 /path/q4.gguf q8 /path/q8.gguf

Report all of them together with:

    uv run python scripts/decode_ab_report.py <outdir-prefix>-run*
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import pathlib
import sys
from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
for sub in ("scripts", "scripts/lib", "benchmarks/agent"):
    sys.path.insert(0, str(REPO / sub))

import child
import post_ab_run
import thermals

import logs

logger = logging.getLogger(__name__)


def is_complete(run: pathlib.Path) -> bool:
    """Delegates so the runner and the poster cannot drift apart (#136).

    A run is complete when every CSV has the same frontier count as the
    widest one -- not when six files exist. A file being written right now
    already has a name and a header, so counting files reports a run as
    finished while ds4-bench is still filling its last one, and any
    statistic taken then silently includes a partial arm. The shell and the
    poster already drifted apart on this three times.
    """
    return post_ab_run.is_complete(run)


def start_state_text(i: int, n: int, harness: str, args: Sequence[str]) -> str:
    """Machine state at the start of each run, for later cold-start checks.

    #118's run 2 was an outlier and the cold-start hypothesis could only be
    tested afterwards because run 4 happened to capture this; capture it
    every time instead of hoping. Fans are read only, never set.
    """
    when = (
        datetime.datetime.now(datetime.UTC).astimezone().isoformat(timespec="seconds")
    )
    lines = [f"# run {i} of {n}, launched {when}"]
    lines.append(f"# harness: {harness} {' '.join(str(a) for a in args)}")
    lines.append("# fans (read only, never set):")
    reading = thermals.reading()
    fans = {k: v for k, v in reading.items() if k.startswith("fan")}
    if fans:
        lines.append(json.dumps(fans, sort_keys=True))
    else:
        lines.append("  (fancontrol unavailable)")
    lines.append("# thermals at launch:")
    lines.append(json.dumps(reading, sort_keys=True))
    return "\n".join(lines) + "\n"


def finish_text() -> str:
    """The tail of start-state.txt: when it finished and how hot it was."""
    when = (
        datetime.datetime.now(datetime.UTC).astimezone().isoformat(timespec="seconds")
    )
    reading = thermals.reading()
    return (
        f"# finished: {when}\n"
        "# thermals at finish:\n"
        f"{json.dumps(reading, sort_keys=True)}\n"
    )


def harness_argv(harness: str, args: Sequence[str], out: pathlib.Path) -> list[str]:
    """The harness invocation. `uv run python` for a .py, `bash` otherwise.

    Mirrors the shell's `bash "$HARNESS" "$@" "$OUT"`; a python harness gets
    its interpreter so a shebang-less script still runs.
    """
    tail = [*[str(a) for a in args], str(out)]
    if harness.endswith(".py"):
        return ["uv", "run", "python", harness, *tail]
    return ["bash", harness, *tail]


def repeat(
    n: int,
    prefix: pathlib.Path,
    harness: str,
    args: Sequence[str],
    owner_pid: int,
) -> int:
    """Run the harness n times into `{prefix}-run{i}`, sequentially.

    This runner does NOT hold the run lock. Each harness invocation takes and
    releases it in turn (#133); two runners would measure contention, and the
    lock is the only thing keeping this sequential. Returns an exit code.
    """
    parent = prefix.resolve().parent
    base = prefix.name
    code = 0
    for i in range(1, n + 1):
        out = parent / f"{base}-run{i}"
        if out.is_dir() and is_complete(out):
            # Never silently overwrite a completed run: the whole point of
            # this script is accumulating runs, and a clobbered one is
            # unrecoverable.
            logger.warning("run %d: %s already holds CSVs, skipping", i, out)
            continue
        out.mkdir(parents=True, exist_ok=True)
        (out / "start-state.txt").write_text(start_state_text(i, n, harness, args))
        logger.info("run %d of %d -> %s", i, n, out)
        # child.run, not subprocess.run: a driver stopped mid-repeat must take
        # the harness (and ds4-bench under it) with it (#268). The log is per
        # run, not one shared stream, so a later reader can tell which
        # repetition produced what.
        rc = child.run(
            harness_argv(harness, args, out),
            cwd=REPO,
            log=out / "harness.log",
        )
        with (out / "start-state.txt").open("a") as handle:
            handle.write(finish_text())
        if rc != 0:
            logger.error("run %d FAILED (%d) -- see %s", i, rc, out / "harness.log")
            code = rc
            continue
        logger.info("run %d done", i)
    if code != 0:
        return code
    logger.info("all %d runs complete under %s-run*", n, base)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("n", type=int, help="number of runs")
    p.add_argument("prefix", type=pathlib.Path, help="output directory prefix")
    p.add_argument("harness", help="harness script")
    p.add_argument("args", nargs=argparse.REMAINDER, help="harness arguments")
    args = p.parse_args(argv)

    logs.configure()
    if args.n < 1:
        logger.error("REFUSING: n=%d must be at least 1", args.n)
        return 2
    if not pathlib.Path(args.harness).exists():
        logger.error("REFUSING: harness %s does not exist", args.harness)
        return 2
    return repeat(args.n, args.prefix, args.harness, args.args, owner_pid=os.getpid())


if __name__ == "__main__":
    raise SystemExit(main())
