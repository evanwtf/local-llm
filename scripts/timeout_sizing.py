"""Would a lower or per-task step timeout cost passes? The evidence for the flat 1,800 s.

**Why this exists.** The operator asked on 2026-10-16 whether the 30-minute
step timeout could drop, or follow each task's median pass, without hurting
the good stacks. The answer came from the Spark's ledger and decided the
policy in ``benchmarks/agent/METHODOLOGY.md`` ("The step timeout"), so the
query lives here, tested, and can be rerun when the ledger grows.

Three views of one ledger, OpenCode by default:

1. **Per task:** passes, the median, p90 and longest pass, and timeouts.
2. **A flat limit** (``--flat``, repeatable): the passes a lower limit would
   turn into failures, in all and per backend.
3. **A per-task limit** (``--k``, repeatable): k x the task's median pass,
   clamped to [``--floor``, ``--limit``]. The passes it loses and the timeout
   seconds it saves, against today's flat ``--limit``.

A pass that ran exactly as long as a limit is kept; only a longer one is lost.
A timeout counts at the full ``--limit``, as the early stop counts it.

    uv run python scripts/timeout_sizing.py --results hardware/<machine>/results.jsonl
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import logging
import pathlib
import statistics
import sys
from collections.abc import Sequence
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "benchmarks" / "agent"))

import provenance
import results

logger = logging.getLogger(__name__)

Row = dict[str, Any]


def is_timeout(row: Row) -> bool:
    return row.get("error") == "timeout"


def pass_seconds(rows: Sequence[Row]) -> list[float]:
    """Wall seconds of the passing rows, by the verdict with its guards."""
    return [
        float(r["wall_seconds"])
        for r in rows
        if results.verdict(r) and r.get("wall_seconds") is not None
    ]


@dataclasses.dataclass(frozen=True)
class TaskStats:
    task: str
    passes: int
    median: float | None
    p90: float | None
    longest: float | None
    timeouts: int


def task_stats(rows: Sequence[Row]) -> list[TaskStats]:
    by_task: dict[str, list[Row]] = collections.defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(r)
    out = []
    for task, rr in sorted(by_task.items()):
        p = sorted(pass_seconds(rr))
        timeouts = sum(1 for r in rr if is_timeout(r))
        if not p:
            out.append(TaskStats(task, 0, None, None, None, timeouts))
            continue
        p90 = p[int(0.9 * (len(p) - 1))]
        out.append(TaskStats(task, len(p), statistics.median(p), p90, p[-1], timeouts))
    return out


def lost_to_flat(rows: Sequence[Row], limit: float) -> collections.Counter[str]:
    """Per backend, the passes that ran longer than ``limit``."""
    lost: collections.Counter[str] = collections.Counter()
    for r in rows:
        if results.verdict(r) and float(r.get("wall_seconds") or 0) > limit:
            lost[r["backend"]] += 1
    return lost


@dataclasses.dataclass(frozen=True)
class PerTask:
    k: float
    passes_lost: int
    timeout_seconds: float
    seconds_saved: float


def per_task_limits(
    rows: Sequence[Row], k: float, floor: float, limit: float
) -> dict[str, float]:
    """k x each task's median pass, clamped; a task with no pass keeps ``limit``."""
    by_task: dict[str, list[Row]] = collections.defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(r)
    out = {}
    for task, rr in by_task.items():
        p = pass_seconds(rr)
        out[task] = min(limit, max(floor, k * statistics.median(p))) if p else limit
    return out


def simulate_per_task(
    rows: Sequence[Row], k: float, floor: float, limit: float
) -> PerTask:
    limits = per_task_limits(rows, k, floor, limit)
    lost = sum(
        1
        for r in rows
        if results.verdict(r) and float(r.get("wall_seconds") or 0) > limits[r["task"]]
    )
    timeouts = [r for r in rows if is_timeout(r)]
    saved = sum(limit - limits[r["task"]] for r in timeouts)
    return PerTask(k, lost, limit * len(timeouts), saved)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--results", type=pathlib.Path, default=results.default_path())
    p.add_argument("--client", default="opencode")
    p.add_argument("--limit", type=float, default=1800.0, help="today's step timeout")
    p.add_argument(
        "--flat",
        type=float,
        action="append",
        help="a lower flat limit to test, in seconds (repeatable; default 1200 1500)",
    )
    p.add_argument(
        "--k",
        type=float,
        action="append",
        help="a per-task multiple of the median pass (repeatable; default 3 4 5)",
    )
    p.add_argument("--floor", type=float, default=600.0)
    args = p.parse_args()

    provenance.configure()
    rows = [r for r in results.trials(args.results) if r.get("client") == args.client]
    logger.info("%d %s trials in %s", len(rows), args.client, args.results)
    logger.info("task | passes | median | p90 | longest | timeouts")
    for s in task_stats(rows):
        if s.median is None:
            logger.info("%s | 0 | - | - | - | %d", s.task, s.timeouts)
            continue
        logger.info(
            "%s | %d | %.0f s | %.0f s | %.0f s | %d",
            s.task,
            s.passes,
            s.median,
            s.p90,
            s.longest,
            s.timeouts,
        )
    for flat in args.flat or [1200.0, 1500.0]:
        lost = lost_to_flat(rows, flat)
        logger.info(
            "flat %.0f s: %d passes lost; most per backend: %s",
            flat,
            sum(lost.values()),
            ", ".join(f"{b} {n}" for b, n in lost.most_common(5)) or "none",
        )
    for k in args.k or [3.0, 4.0, 5.0]:
        sim = simulate_per_task(rows, k, args.floor, args.limit)
        logger.info(
            "per task %.1f x median, [%.0f, %.0f] s: %d passes lost; "
            "timeouts cost %.0f s, the limit would save %.0f s",
            k,
            args.floor,
            args.limit,
            sim.passes_lost,
            sim.timeout_seconds,
            sim.seconds_saved,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
