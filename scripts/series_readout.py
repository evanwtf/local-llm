"""Read out an interleaved two-arm series from saved row files (#970).

An A/B series runs each arm several times, interleaved (A B A B A B), and
saves each run's ledger rows to its own file. The ledger rows carry no batch
name, so `paired_ab_report.py` cannot select them; and `screen_stacks.py`
pools a backend across clients. This script reads only the files it is given.

Per arm it reports passes (by `results.verdict`, so a pass that edited the
tests is a failure), the median trial wall, output tokens, and the **sum of
medians**: the median wall of each task over every trial of that arm, summed
over tasks. Failed trials count in the walls: their time is real. A timed-out
trial has no wall of its own and counts at the full `--timeout` limit, as the
screen counts it (`screening.seconds`).

It then applies the pre-registered rule of #970: B wins if it is not worse on
pass rate and its sum of medians is at most `--max-slowdown` above A's.

    uv run python scripts/series_readout.py \\
        --arm TR3=a1.jsonl,a2.jsonl,a3.jsonl \\
        --arm quant=b1.jsonl,b2.jsonl,b3.jsonl

The first `--arm` is A (the incumbent), the second is B (the challenger).
Exit status: 0 when the rule picks B, 1 when A stays, 2 on bad input.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib
import statistics
import sys
from collections.abc import Sequence
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import results
import screening

import logs

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Run:
    """One run of one arm: the rows of one saved file."""

    path: pathlib.Path
    rows: list[dict[str, Any]]


@dataclasses.dataclass(frozen=True)
class ArmStats:
    name: str
    trials: int
    passes: int
    run_passes: list[int]
    sum_of_medians: float
    run_sums_of_medians: list[float]
    median_trial: float
    output_tokens: int
    tasks: frozenset[str]


def read_run(path: pathlib.Path) -> Run:
    """Read one run file. Excluded and dry-run rows are dropped."""
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("excluded") or row.get("dry_run"):
            continue
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: no usable rows")
    return Run(path, rows)


#: The client wall-clock limit a timed-out trial counts at (run.py's default).
DEFAULT_TIMEOUT = 1800.0


def sum_of_medians(
    rows: Sequence[dict[str, Any]], timeout: float = DEFAULT_TIMEOUT
) -> float:
    """The median wall of each task, summed over tasks."""
    by_task: dict[str, list[float]] = {}
    for row in rows:
        by_task.setdefault(row["task"], []).append(screening.seconds(row, timeout))
    return sum(statistics.median(walls) for walls in by_task.values())


def arm_stats(
    name: str, runs: Sequence[Run], timeout: float = DEFAULT_TIMEOUT
) -> ArmStats:
    rows = [row for run in runs for row in run.rows]
    backends = {row["backend"] for row in rows}
    if len(backends) != 1:
        raise ValueError(f"arm {name}: more than one backend: {sorted(backends)}")
    return ArmStats(
        name=name,
        trials=len(rows),
        passes=sum(results.verdict(row) for row in rows),
        run_passes=[sum(results.verdict(row) for row in run.rows) for run in runs],
        sum_of_medians=sum_of_medians(rows, timeout),
        run_sums_of_medians=[sum_of_medians(run.rows, timeout) for run in runs],
        median_trial=statistics.median(screening.seconds(r, timeout) for r in rows),
        output_tokens=sum(int(row.get("output_tokens") or 0) for row in rows),
        tasks=frozenset(row["task"] for row in rows),
    )


def passes_at_least(b: ArmStats, a: ArmStats) -> bool:
    """B's pass rate is at least A's. Rates, not counts (#1023): one run of
    B against three of A compared 42 passes with 123. Cross-multiplied, so
    equal trial counts reduce to the old count comparison exactly."""
    return b.passes * a.trials >= a.passes * b.trials


def rule_picks_b(a: ArmStats, b: ArmStats, max_slowdown: float) -> bool:
    """The #970 rule: B is not worse on pass rate, and not more than
    `max_slowdown` slower on the sum of medians."""
    if not passes_at_least(b, a):
        return False
    return b.sum_of_medians <= a.sum_of_medians * (1 + max_slowdown)


def parse_arm(text: str) -> tuple[str, list[pathlib.Path]]:
    name, sep, paths = text.partition("=")
    if not sep or not name or not paths:
        raise argparse.ArgumentTypeError(f"expected NAME=FILE[,FILE...], got {text!r}")
    return name, [pathlib.Path(p) for p in paths.split(",") if p]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--arm",
        type=parse_arm,
        action="append",
        required=True,
        help="NAME=FILE[,FILE...]; give A (incumbent) first, then B",
    )
    parser.add_argument(
        "--max-slowdown",
        type=float,
        default=0.05,
        help="largest fraction B's sum of medians may sit above A's (default 0.05)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="seconds a timed-out trial counts at (default %(default)s)",
    )
    args = parser.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    if len(args.arm) != 2:
        logger.error("give exactly two --arm values (A, then B)")
        return 2
    try:
        a, b = (
            arm_stats(name, [read_run(p) for p in paths], args.timeout)
            for name, paths in args.arm
        )
    except (OSError, ValueError, KeyError) as exc:
        logger.error("%s", exc)
        return 2
    if a.tasks != b.tasks:
        logger.error(
            "the arms ran different tasks: only A %s, only B %s",
            sorted(a.tasks - b.tasks),
            sorted(b.tasks - a.tasks),
        )
        return 2
    for arm in (a, b):
        logger.info(
            "%s: %d/%d passed (per run %s); sum of medians %.1f s "
            "(per run %s); median trial %.1f s; output tokens %d",
            arm.name,
            arm.passes,
            arm.trials,
            ", ".join(str(n) for n in arm.run_passes),
            arm.sum_of_medians,
            ", ".join(f"{s:.1f}" for s in arm.run_sums_of_medians),
            arm.median_trial,
            arm.output_tokens,
        )
    ceiling = a.sum_of_medians * (1 + args.max_slowdown)
    logger.info(
        "rule: %s passes %d/%d >= %s passes %d/%d (as rates): %s; "
        "%s sum %.1f s <= ceiling %.1f s (%s + %.0f%%): %s",
        b.name,
        b.passes,
        b.trials,
        a.name,
        a.passes,
        a.trials,
        "yes" if passes_at_least(b, a) else "no",
        b.name,
        b.sum_of_medians,
        ceiling,
        a.name,
        args.max_slowdown * 100,
        "yes" if b.sum_of_medians <= ceiling else "no",
    )
    if rule_picks_b(a, b, args.max_slowdown):
        logger.info("verdict: %s becomes the pick", b.name)
        return 0
    logger.info("verdict: %s stays the pick", a.name)
    return 1


if __name__ == "__main__":
    sys.exit(main())
