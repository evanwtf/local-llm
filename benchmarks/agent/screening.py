"""Stop a run once it can no longer earn a place: the #762 early stop.

**Why this exists.** Compute time is finite, and not spending it on bad models
is a primary goal (operator, 2026-09-27). #762's thresholds were settled on
2026-09-25, but nothing enforced them: two Sushi runs on the M5 Max ran their
full 8-9 hours, and the failure rule, applied afterwards, would have fired at
trial 34 of 42 and at trial 40 of 41.

Two stops, both against the **leader**: the best stack on the same task set and
client in this machine's ledger (highest pass rate, then the smallest sum of
per-task medians).

- **Failure stop**, after every trial: ``ceil(0.30 x leader passes)``, with the
  leader's pass rate scaled to this run's planned trials. The operator's
  example: a 20/20 leader stops a 20-trial run at its 6th failure.
- **Time stop**, once every task has run its first trial: at 2x the leader's
  time on the same (task, trial) pairs. Waiting for the first round keeps one
  slow first task from ending a run.

A failure is the visible verdict's failure (``results.verdict``, with its
guards): a timeout, a failed suite, or a pass that edited the tests. Hidden
(#726) verdicts do not drive the stop -- every stack fails some held-out tests,
so they would stop everything. Excluded rows never count.

A timeout counts at the full per-trial limit. Rows do not record their limit,
so the leader's timeouts count at this run's ``--timeout`` too.

An abort on a first run means "check the configuration" before it means
"veto" (operator, 2026-09-25): the chat template, the parsers, the reasoning
effort, the context length, the sampler. The abort record says so.
"""

from __future__ import annotations

import json
import math
import pathlib
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import results

#: The operator's thresholds (#762, 2026-09-25).
FAILURE_FRACTION = 0.30
TIME_MULTIPLE = 2.0

Row = dict[str, Any]


def is_failure(row: Row) -> bool:
    """A trial that did not earn a pass: timeout, failed suite, or a guard."""
    return not results.verdict(row)


def seconds(row: Row, timeout: float) -> float:
    """Wall seconds, with a timeout at the full limit."""
    if row.get("error") == "timeout" or row.get("wall_seconds") is None:
        return float(timeout)
    return float(row["wall_seconds"])


def failure_threshold(leader_passes: int, leader_rows: int, planned: int) -> int:
    """Failures that stop a run of ``planned`` trials against this leader."""
    expected = leader_passes / leader_rows * planned
    return math.ceil(round(FAILURE_FRACTION * expected, 9))


@dataclass(frozen=True)
class Leader:
    batch: str
    passes: int
    rows: int
    #: (task, trial) -> seconds, timeouts at the limit
    times: dict[tuple[str, int], float] = field(default_factory=dict)
    #: task -> median seconds, for a (task, trial) the leader never ran
    medians: dict[str, float] = field(default_factory=dict)

    def time_for(self, task: str, trial: int) -> float:
        return self.times.get((task, trial), self.medians[task])

    @property
    def total(self) -> float:
        return sum(self.medians.values())


def leader_from_rows(
    rows: Sequence[Row], batch: str, timeout: float = 1800.0
) -> Leader:
    times = {(r["task"], int(r["trial"])): seconds(r, timeout) for r in rows}
    by_task: dict[str, list[float]] = {}
    for (task, _), secs in times.items():
        by_task.setdefault(task, []).append(secs)
    return Leader(
        batch=batch,
        passes=sum(1 for r in rows if not is_failure(r)),
        rows=len(rows),
        times=times,
        medians={t: statistics.median(v) for t, v in by_task.items()},
    )


def pick_leader(
    history: Iterable[Row],
    tasks: Sequence[str],
    client: str,
    exclude_batch: str | None,
    timeout: float = 1800.0,
) -> Leader | None:
    """The best earlier batch that ran every one of ``tasks`` on ``client``.

    None when no batch qualifies; the caller runs without an early stop.
    """
    wanted = set(tasks)
    batches: dict[str, list[Row]] = {}
    for r in history:
        if (
            r.get("batch")
            and r.get("batch") != exclude_batch
            and r.get("client") == client
            and r.get("task") in wanted
            and not r.get("dry_run")
            and not results.is_excluded(r)
        ):
            batches.setdefault(r["batch"], []).append(r)
    candidates = [
        leader_from_rows(rows, batch, timeout)
        for batch, rows in batches.items()
        if {r["task"] for r in rows} == wanted
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c.passes / c.rows, -c.total))


def should_stop(
    done: Sequence[Row],
    leader: Leader,
    tasks: Sequence[str],
    planned: int,
    timeout: float,
) -> tuple[bool, str]:
    """Should this run stop now? Returns (stop, the reason with its numbers)."""
    counted = [r for r in done if not results.is_excluded(r)]
    failures = sum(1 for r in counted if is_failure(r))
    threshold = failure_threshold(leader.passes, leader.rows, planned)
    if failures >= threshold:
        return True, (
            f"failure stop: {failures} failures in {len(counted)} trials, "
            f"threshold {threshold} = ceil({FAILURE_FRACTION} x leader "
            f"{leader.batch} {leader.passes}/{leader.rows} scaled to {planned})"
        )
    if not {t for t in tasks} <= {r["task"] for r in counted if int(r["trial"]) == 1}:
        return False, f"{failures} failures, threshold {threshold}; round 1 not done"
    ours = sum(seconds(r, timeout) for r in counted)
    theirs = sum(leader.time_for(r["task"], int(r["trial"])) for r in counted)
    ratio = ours / theirs
    if ratio >= TIME_MULTIPLE:
        return True, (
            f"time stop: {ours:.1f} s against leader {leader.batch}'s "
            f"{theirs:.1f} s on the same trials ({100 * ratio:.1f}%, "
            f"limit {100 * TIME_MULTIPLE:.0f}%)"
        )
    return False, (
        f"{failures} failures, threshold {threshold}; time {100 * ratio:.1f}% "
        f"of the leader's"
    )


def write_abort(
    path: pathlib.Path,
    *,
    batch: str | None,
    backend: str,
    client: str,
    tasks: Sequence[str],
    done: Sequence[Row],
    leader: Leader,
    reason: str,
    first_run: bool,
    when: str,
) -> dict[str, Any]:
    """Append one abort record beside the ledger. The rows stay in the ledger."""
    counted = [r for r in done if not results.is_excluded(r)]
    kinds: dict[str, int] = {}
    for r in counted:
        if not is_failure(r):
            continue
        if r.get("error") == "timeout":
            kind = "timeout"
        elif r.get("passed") and r.get("touched_tests"):
            kind = "touched tests"
        elif r.get("passed"):
            kind = "guard"
        else:
            kind = "test failure"
        kinds[kind] = kinds.get(kind, 0) + 1
    record = {
        "when": when,
        "batch": batch,
        "backend": backend,
        "client": client,
        "tasks": list(tasks),
        "trials_done": len(counted),
        "failure_kinds": kinds,
        "leader": {"batch": leader.batch, "passes": leader.passes, "rows": leader.rows},
        "reason": reason,
        "state": (
            "check the configuration before a veto (first run)"
            if first_run
            else "screened out"
        ),
    }
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return record
