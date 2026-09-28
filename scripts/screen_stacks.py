"""#762 Stage 2: which stacks earn more runs after one full run, and which are cut.

**Why this exists.** Compute time is finite, and not spending it on bad models is
a primary goal (operator, 2026-09-27). Only survivors get runs 2 and 3; every
other stack gets one run and one line saying why. #762 applied this by hand to
the cluster's first six stacks; this makes it one command per lane.

Per batch (one run of one stack) that ran every task in the set on the client:

1. **Gate.** A pass rate of 90% or better, by the visible verdict with its
   guards, and a sum of per-task medians under 2x the leader's. A stack that
   misses either is "screened out" after one run, not ranked.
2. **Rank** the rest by sum of per-task medians, fastest first. Keep the
   fastest 3, then any stack within 25% of the third, up to 5 kept: one run
   cannot separate stacks that close. The rest are "cut".

The leader is the best pass rate, then the fastest -- the same choice as the
run-time early stop (``benchmarks/agent/screening.py``). A timeout counts at
``--timeout`` in the medians. Every threshold is a flag, so each lane can set
its own.

    uv run python scripts/screen_stacks.py --results hardware/<machine>/results.jsonl
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import statistics
import sys
import tomllib
from collections.abc import Iterable, Sequence
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "benchmarks" / "agent"))

import provenance
import results
import screening

logger = logging.getLogger(__name__)

Row = dict[str, Any]


@dataclasses.dataclass(frozen=True)
class Verdict:
    batch: str
    backend: str
    started: str
    passes: int
    rows: int
    sum_medians: float
    result: str  # "keep", "cut", or "screened out"
    reason: str
    runs: int = 1


def _batches(
    rows: Iterable[Row],
    tasks: Sequence[str],
    client: str,
    suites: dict[str, str] | None = None,
) -> dict[str, list[Row]]:
    """Runs (``screening.runs``) that ran every one of ``tasks`` on ``client``."""
    wanted = set(tasks)
    pool = [
        r
        for r in rows
        if r.get("client") == client
        and r.get("task") in wanted
        and not r.get("dry_run")
        and not results.is_excluded(r)
    ]
    return {
        b: rs
        for b, rs in screening.runs(pool, suites).items()
        if {r["task"] for r in rs} == wanted
    }


def _sum_medians(rows: Sequence[Row], timeout: float) -> float:
    by_task: dict[str, list[float]] = {}
    for r in rows:
        by_task.setdefault(r["task"], []).append(screening.seconds(r, timeout))
    return sum(statistics.median(v) for v in by_task.values())


def screen(
    rows: Iterable[Row],
    tasks: Sequence[str],
    client: str = "opencode",
    *,
    timeout: float = 1800.0,
    gate: float = 0.90,
    time_multiple: float = screening.TIME_MULTIPLE,
    keep_min: int = 3,
    keep_max: int = 5,
    band: float = 0.25,
    suites: dict[str, str] | None = None,
) -> list[Verdict]:
    """One verdict per stack (backend): kept first, fastest first.

    A stack's full runs on this task set are pooled: the pass rate over all
    their trials, and per-task medians over all their trials. Ranking runs
    instead lets one stack's repeat runs fill every kept place.
    """
    by_stack: dict[str, list[tuple[str, list[Row]]]] = {}
    for key, rs in _batches(rows, tasks, client, suites).items():
        by_stack.setdefault(rs[0].get("backend", "?"), []).append((key, rs))
    stats = []
    for backend, stack_runs in by_stack.items():
        rs = [r for _, run_rows in stack_runs for r in run_rows]
        label = stack_runs[0][0] if len(stack_runs) == 1 else f"{len(stack_runs)} runs"
        passes = sum(1 for r in rs if not screening.is_failure(r))
        stats.append(
            (
                label,
                backend,
                min(r.get("started", "") for r in rs),
                passes,
                len(rs),
                _sum_medians(rs, timeout),
                len(stack_runs),
            )
        )
    if not stats:
        return []
    leader = max(stats, key=lambda s: (s[3] / s[4], -s[5]))
    lead_time = leader[5]

    out: list[Verdict] = []
    ranked = []
    for batch, backend, started, passes, n, secs, nruns in stats:
        rate = passes / n
        if rate < gate:
            why = (
                f"{passes}/{n} ({100 * rate:.1f}%) is under the {100 * gate:.0f}% gate"
            )
        elif secs >= time_multiple * lead_time:
            why = (
                f"{secs:.1f} s is {100 * secs / lead_time:.1f}% of the leader "
                f"{leader[1]}'s {lead_time:.1f} s (limit {100 * time_multiple:.0f}%)"
            )
        else:
            ranked.append((batch, backend, started, passes, n, secs, nruns))
            continue
        out.append(
            Verdict(
                batch, backend, started, passes, n, secs, "screened out", why, nruns
            )
        )

    ranked.sort(key=lambda s: s[5])
    kept: list[Verdict] = []
    cut: list[Verdict] = []
    anchor = ranked[min(keep_min, len(ranked)) - 1][5] if ranked else 0.0
    for i, (batch, backend, started, passes, n, secs, nruns) in enumerate(ranked):
        behind = secs / anchor - 1 if anchor else 0.0
        if i < keep_min:
            kept.append(
                Verdict(
                    batch,
                    backend,
                    started,
                    passes,
                    n,
                    secs,
                    "keep",
                    f"rank {i + 1}",
                    nruns,
                )
            )
        elif len(kept) < keep_max and behind <= band:
            kept.append(
                Verdict(
                    batch,
                    backend,
                    started,
                    passes,
                    n,
                    secs,
                    "keep",
                    f"rank {i + 1}, {100 * behind:.1f}% behind rank {keep_min}: "
                    "one run cannot separate them",
                    nruns,
                )
            )
        else:
            cut.append(
                Verdict(
                    batch,
                    backend,
                    started,
                    passes,
                    n,
                    secs,
                    "cut",
                    f"rank {i + 1}, {100 * behind:.1f}% behind rank {keep_min}",
                    nruns,
                )
            )
    return kept + cut + out


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--results", type=pathlib.Path, default=results.default_path())
    p.add_argument("--client", default="opencode")
    p.add_argument(
        "--tasks",
        default="replay-",
        help="task-name prefix that defines the set (default replay-); '' for all",
    )
    p.add_argument(
        "--standard", action="store_true", help="the non-replay tasks instead"
    )
    p.add_argument("--timeout", type=float, default=1800.0)
    p.add_argument(
        "--tasks-file",
        type=pathlib.Path,
        default=HERE.parent / "benchmarks" / "agent" / "tasks.toml",
        help="where each task's suite (standard, replay, hard) is defined",
    )
    p.add_argument("--gate", type=float, default=0.90)
    p.add_argument("--time-multiple", type=float, default=screening.TIME_MULTIPLE)
    p.add_argument("--keep-min", type=int, default=3)
    p.add_argument("--keep-max", type=int, default=5)
    p.add_argument("--band", type=float, default=0.25)
    args = p.parse_args()

    provenance.configure()
    rows = results.trials(args.results)
    suites = screening.suite_map(tomllib.loads(args.tasks_file.read_text()))
    names = {
        r["task"] for r in rows if r.get("client") == args.client and r.get("task")
    }
    if args.standard:
        tasks = sorted(t for t in names if not t.startswith("replay-"))
    else:
        tasks = sorted(t for t in names if t.startswith(args.tasks))
    # One table per task set that two or more runs share. A lane's runs do not
    # all use one set: the DGX cluster ran 7 replay tasks, then 14.
    pool = [r for r in rows if r.get("client") == args.client and r["task"] in tasks]
    sets: dict[frozenset[str], int] = {}
    for rs in screening.runs(pool, suites).values():
        key = frozenset(r["task"] for r in rs)
        sets[key] = sets.get(key, 0) + 1
    shared = sorted((s for s, n in sets.items() if n >= 2), key=len, reverse=True)
    if not shared:
        logger.info("no two runs in %s share a task set", args.results)
        return 0
    for task_set in shared:
        verdicts = screen(
            rows,
            sorted(task_set),
            args.client,
            timeout=args.timeout,
            gate=args.gate,
            time_multiple=args.time_multiple,
            keep_min=args.keep_min,
            keep_max=args.keep_max,
            band=args.band,
            suites=suites,
        )
        logger.info(
            "%s: %d tasks, %d stacks ran all of them on %s",
            args.results,
            len(task_set),
            len(verdicts),
            args.client,
        )
        logger.info(
            "| result | backend | runs | first started | passed | sum of medians | why |"
        )
        logger.info("|---|---|---|---|---|---|---|")
        for v in verdicts:
            logger.info(
                "| %s | `%s` | %s | %s | %d/%d | %.1f s | %s |",
                v.result,
                v.backend,
                f"1 (`{v.batch}`)" if v.runs == 1 else str(v.runs),
                v.started[:10],
                v.passes,
                v.rows,
                v.sum_medians,
                v.reason,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
