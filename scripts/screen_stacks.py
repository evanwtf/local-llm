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

``--since`` keeps the rows started at or after an instant, and ``--before`` the
rows started before one. The cluster needs them: the #968 client fix splits its
ledger at 2026-10-06 09:14 EDT, and runs on either side of that cut do not
compare.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
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
import sizing

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
    #: Median reasoning tokens a trial, over the rows that record them.
    reasoning_median: float | None = None
    #: Wilson 95% interval of the pass rate (#866).
    ci_low: float = 0.0
    ci_high: float = 1.0
    #: Whether that interval overlaps the leader's: if it does, one run cannot
    #: say this stack passes less often than the leader.
    overlaps_leader: bool = True
    #: Replay files restored byte-for-byte, of the files the replay reverted,
    #: over every trial. gmail-archive is public, so a pass may be partly
    #: recall; this is the caveat beside the pass rate (#866).
    verbatim_files: int = 0
    recall_files: int = 0


def _instant(stamp: str) -> datetime.datetime | None:
    try:
        t = datetime.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return t if t.tzinfo else None


def _split(rows: Iterable[Row], bound: str, flag: str) -> tuple[list[Row], list[Row]]:
    """(rows started before ``bound``, rows started at or after it), as instants.

    Rows record UTC and a bound is usually New York time, so a string compare
    is wrong for four hours either side of a cut. A row with no parseable start
    is dropped: it cannot be placed on either side.
    """
    cut = _instant(bound)
    if cut is None:
        raise ValueError(f"{flag} needs an ISO timestamp with an offset: {bound!r}")
    early: list[Row] = []
    late: list[Row] = []
    for r in rows:
        t = _instant(r.get("started") or "")
        if t is not None:
            (late if t >= cut else early).append(r)
    return early, late


def since(rows: Iterable[Row], bound: str) -> list[Row]:
    """Rows started at or after ``bound``."""
    return _split(rows, bound, "--since")[1]


def before(rows: Iterable[Row], bound: str) -> list[Row]:
    """Rows started before ``bound``: the complement of ``since`` at one cut."""
    return _split(rows, bound, "--before")[0]


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


def _reasoning_median(rows: Sequence[Row]) -> float | None:
    got = [
        float(r["reasoning_tokens"])
        for r in rows
        if isinstance(r.get("reasoning_tokens"), (int, float))
        and not isinstance(r.get("reasoning_tokens"), bool)
    ]
    return statistics.median(got) if got else None


def _recall(rows: Sequence[Row]) -> tuple[int, int]:
    """(files restored verbatim, files reverted), over every replay trial."""
    verbatim = total = 0
    for r in rows:
        replay = r.get("replay")
        recall = replay.get("recall") if isinstance(replay, dict) else None
        for f in recall or []:
            if isinstance(f, dict):
                total += 1
                verbatim += f.get("verbatim") is True
    return verbatim, total


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
    reasoning: dict[str, float | None] = {}
    recall: dict[str, tuple[int, int]] = {}
    for backend, stack_runs in by_stack.items():
        rs = [r for _, run_rows in stack_runs for r in run_rows]
        reasoning[backend] = _reasoning_median(rs)
        recall[backend] = _recall(rs)
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
    lead_low = sizing.wilson_lower(leader[3], leader[4])
    lead_high = sizing.wilson_upper(leader[3], leader[4])
    done = []
    for v in kept + cut + out:
        low = sizing.wilson_lower(v.passes, v.rows)
        high = sizing.wilson_upper(v.passes, v.rows)
        done.append(
            dataclasses.replace(
                v,
                reasoning_median=reasoning[v.backend],
                ci_low=low,
                ci_high=high,
                overlaps_leader=low <= lead_high and lead_low <= high,
                verbatim_files=recall[v.backend][0],
                recall_files=recall[v.backend][1],
            )
        )
    return done


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
    p.add_argument(
        "--since",
        help="ISO timestamp with an offset; only rows started at or after it",
    )
    p.add_argument(
        "--before",
        help="ISO timestamp with an offset; only rows started before it",
    )
    args = p.parse_args()

    provenance.configure()
    rows = results.trials(args.results)
    if args.since:
        rows = since(rows, args.since)
    if args.before:
        rows = before(rows, args.before)
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
            "| result | backend | runs | first started | passed | 95% CI "
            "| CI overlaps the leader's | sum of medians | median reasoning tokens "
            "| replay files restored verbatim | why |"
        )
        logger.info("|---|---|---|---|---|---|---|---|---|---|---|")
        for v in verdicts:
            logger.info(
                "| %s | `%s` | %s | %s | %d/%d | %.1f–%.1f%% | %s | %.1f s | %s | %s | %s |",
                v.result,
                v.backend,
                f"1 (`{v.batch}`)" if v.runs == 1 else str(v.runs),
                v.started[:10],
                v.passes,
                v.rows,
                100 * v.ci_low,
                100 * v.ci_high,
                "yes" if v.overlaps_leader else "no",
                v.sum_medians,
                "—" if v.reasoning_median is None else f"{v.reasoning_median:,.1f}",
                f"{v.verbatim_files}/{v.recall_files}" if v.recall_files else "—",
                v.reason,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
