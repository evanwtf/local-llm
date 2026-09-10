#!/usr/bin/env python3
"""What slope does an already-settled die actually show? #276

Picks the bound for `thermal_settle`. A threshold guessed rather than measured
is how the previous gate came to pass every cooling rate under 36 C/hour.

Reads a monitord sensors CSV and, over stretches the machine was genuinely
idle, reports the distribution of trailing-window slopes. A die that has
finished cooling still shows a non-zero slope -- that is sensor noise fitted
by least squares -- and the bound has to sit above that noise or the wait
never ends.
"""

from __future__ import annotations

import argparse
import csv
import logging
import pathlib
import statistics as st
import sys
from collections.abc import Iterator, Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))

import thermal_settle

import logs

logger = logging.getLogger(__name__)

TIME = "time_epoch_ms"
DIE = "sensor.temperature.gpu (°C)"
GPU = "gpu.utilization (fraction)"
CPU = "cpu.total (fraction)"

#: Idle means idle for a while. The die lags the GPU stopping by minutes, so
#: an instantaneous `gpu==0` reaches 99.9 C -- established earlier on this
#: same data. A dwell requirement is what makes the sample honest.
DWELL_S = 300.0
CPU_CEILING = 0.10


def rows(path: pathlib.Path) -> Iterator[tuple[float, float, float, float]]:
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                yield (
                    float(row[TIME]) / 1000.0,
                    float(row[DIE]),
                    float(row[GPU]),
                    float(row[CPU]),
                )
            except (KeyError, TypeError, ValueError):
                continue


def idle_runs(
    data: Sequence[tuple[float, float, float, float]],
    *,
    cpu_ceiling: float = CPU_CEILING,
    grace: int = 0,
) -> list[list[tuple[float, float]]]:
    """Stretches of sustained idle, as (seconds, celsius) series.

    Only the part of a run after DWELL_S is kept -- the beginning of an idle
    stretch is the machine still cooling, which is what this excludes.

    `grace` tolerates that many consecutive non-idle samples without ending a
    run, and it is not a fudge. At `cpu_ceiling=0.10` and `grace=0` this file's
    30,324 idle samples fragment into 3,688 runs, of which **three** last five
    minutes -- because a background process spiking the CPU for one second ends
    a stretch the die never noticed. A one-second CPU blip does not reheat a
    GPU die, and treating it as the end of idle leaves nothing to calibrate on.
    """
    out, current, missed = [], [], 0
    for t, die, gpu, cpu in data:
        if gpu == 0.0 and cpu < cpu_ceiling:
            current.append((t, die))
            missed = 0
            continue
        missed += 1
        if missed > grace:
            if current:
                out.append(current)
            current, missed = [], 0
    if current:
        out.append(current)
    kept = []
    for run in out:
        if not run:
            continue
        began = run[0][0]
        tail = [(t, c) for t, c in run if t - began >= DWELL_S]
        if len(tail) >= thermal_settle.MIN_POINTS:
            kept.append(tail)
    return kept


def slopes(
    runs: Sequence[Sequence[tuple[float, float]]], width_s: float
) -> list[float]:
    """Every trailing-window slope inside every settled stretch."""
    out = []
    for run in runs:
        for index in range(len(run)):
            now = run[index][0]
            got = thermal_settle.slope_c_per_min(
                thermal_settle.window(run[: index + 1], now, width_s)
            )
            if got is not None:
                out.append(abs(got))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("csv_path", type=pathlib.Path)
    p.add_argument("--width", type=float, default=120.0, help="trailing window, s")
    p.add_argument("--cpu-ceiling", type=float, default=CPU_CEILING)
    p.add_argument(
        "--grace",
        type=int,
        default=0,
        help="consecutive non-idle samples tolerated without ending a run",
    )
    args = p.parse_args(argv)
    logs.configure()

    data = list(rows(args.csv_path.expanduser()))
    if not data:
        logger.error("no usable rows in %s", args.csv_path)
        return 2
    runs = idle_runs(data, cpu_ceiling=args.cpu_ceiling, grace=args.grace)
    logger.info("rows %d, sustained-idle stretches %d", len(data), len(runs))
    logger.info("idle samples kept %d", sum(len(r) for r in runs))
    if not runs:
        logger.error("no sustained-idle stretch; cannot calibrate")
        return 2

    got = slopes(runs, args.width)
    if not got:
        logger.error("no window wide enough at width=%.0fs", args.width)
        return 2
    got.sort()

    def pct(p: float) -> float:
        return got[min(len(got) - 1, int(len(got) * p))]

    logger.info(
        "--- |slope| over a %.0fs trailing window, at settled idle ---", args.width
    )
    logger.info("  n        %d", len(got))
    logger.info("  median   %.3f C/min  (%.1f C/hour)", pct(0.50), pct(0.50) * 60)
    logger.info("  p90      %.3f C/min  (%.1f C/hour)", pct(0.90), pct(0.90) * 60)
    logger.info("  p95      %.3f C/min  (%.1f C/hour)", pct(0.95), pct(0.95) * 60)
    logger.info("  p99      %.3f C/min  (%.1f C/hour)", pct(0.99), pct(0.99) * 60)
    logger.info("  max      %.3f C/min  (%.1f C/hour)", got[-1], got[-1] * 60)
    logger.info("  mean     %.3f C/min", st.fmean(got))
    logger.info(
        "a bound below p90 (%.3f C/min) will usually not be reached at true "
        "idle; a bound above it ends the wait on noise rather than on state",
        pct(0.90),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
