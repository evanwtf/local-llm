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

import datetime
import json
import math
import pathlib
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import replay
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


#: Rows without a batch start a new run after a gap this long (seconds).
RUN_GAP_SECONDS = 3 * 3600


def runs(
    rows: Iterable[Row], suites: dict[str, str] | None = None
) -> dict[str, list[Row]]:
    """Group rows into runs: one launch of one stack.

    A row with a ``batch`` belongs to that batch. The DGX cluster's rows carry
    no batch (``batch: null``), and one backend there has several runs over
    several days, some on half the task set. Those rows split per backend,
    client, and suite, then in start order into a new run whenever a (task,
    trial) pair repeats or ``RUN_GAP_SECONDS`` pass between two rows. The key
    is then ``backend@<first started, to the minute>``.

    The suite split comes first because a standard run and a replay run
    launched back to back share no task names and sit under the gap: without
    it, the cluster's runs fused into 17-task groups that matched neither set.
    "hard" groups with "replay", though: one launch runs both (#900).
    ``suites`` maps task name to suite (``suite_map``); a task missing from it
    falls back to its name: "replay" for ``replay-*``, else "standard".
    """
    out: dict[str, list[Row]] = {}
    batched: dict[str, dict[Any, list[Row]]] = {}
    loose: dict[tuple[str, str, str], list[Row]] = {}
    for r in rows:
        if r.get("batch"):
            batched.setdefault(r["batch"], {}).setdefault(r.get("backend"), []).append(
                r
            )
        else:
            key = (r.get("backend", "?"), r.get("client", "?"), _family(r, suites))
            loose.setdefault(key, []).append(r)
    # One launch can name two backends on one engine, and both stamp the same
    # batch. A run is one stack, so they split; a single-backend batch keeps
    # its plain name, which is what the log prints and --early-stop-leader
    # selects by (code review, 2026-10-05).
    for batch, by_backend in batched.items():
        if len(by_backend) == 1:
            out[batch] = next(iter(by_backend.values()))
            continue
        for backend, rs in by_backend.items():
            out[f"{batch}/{backend}"] = rs
    for (backend, _client, _set), rs in loose.items():
        rs.sort(key=lambda r: _when(r))
        current: list[Row] = []
        seen: set[tuple[str, int]] = set()
        for r in rs:
            pair = (r["task"], int(r["trial"]))
            gap = (_when(r) - _when(current[-1])).total_seconds() if current else 0.0
            if current and (pair in seen or gap > RUN_GAP_SECONDS):
                out[f"{backend}@{current[0]['started'][:16]}"] = current
                current, seen = [], set()
            current.append(r)
            seen.add(pair)
        if current:
            out[f"{backend}@{current[0]['started'][:16]}"] = current
    return out


def suite_map(cfg: dict[str, Any]) -> dict[str, str]:
    """Task name -> suite from tasks.toml: "standard", "replay" (#714), or "hard"."""
    out = {}
    for t in cfg.get("task", []):
        if not replay.is_replay(t):
            out[t["name"]] = "standard"
        else:
            out[t["name"]] = "hard" if replay.suite(t) == "hard" else "replay"
    return out


def _suite(row: Row, suites: dict[str, str] | None) -> str:
    task = row.get("task", "")
    if suites and task in suites:
        return suites[task]
    return "replay" if task.startswith("replay-") else "standard"


def _family(row: Row, suites: dict[str, str] | None) -> str:
    """The suite for grouping runs: "hard" counts as "replay" (#900).

    One launch runs both with ``--replay --replay-hard``. Split apart, a
    14-task launch became two 7-task runs, so no run held all 14 tasks and
    the early stop never found a leader.
    """
    suite = _suite(row, suites)
    return "replay" if suite == "hard" else suite


def _when(row: Row) -> datetime.datetime:
    return datetime.datetime.fromisoformat(row["started"])


def image_tools_broken(image: str | None) -> bool:
    """Whether a client image ran OpenCode with grep and glob failing. #968

    The images before the fix carried no ripgrep, and every grep and glob call
    failed; the fixed images declare ``ripgrep=``. A row with no image ran on
    bare metal, where OpenCode's own ripgrep worked.
    """
    return bool(image) and "ripgrep=" not in str(image)


def _row_image(row: Row) -> str | None:
    env = row.get("env")
    machine = env.get("client_machine") if isinstance(env, dict) else None
    image = machine.get("client_image") if isinstance(machine, dict) else None
    return image if isinstance(image, str) else None


def pick_leader(
    history: Iterable[Row],
    tasks: Sequence[str],
    client: str,
    exclude_batch: str | None,
    timeout: float = 1800.0,
    suites: dict[str, str] | None = None,
    tools_broken: bool | None = None,
) -> Leader | None:
    """The best earlier run that ran every one of ``tasks`` on ``client``.

    ``tools_broken``, when given, keeps only runs whose client's tools worked
    the same way as this run's (``image_tools_broken``). Without it, the
    cluster judged fixed-client runs against a broken-client leader (#992).

    None when no run qualifies; the caller runs without an early stop.
    """
    wanted = set(tasks)
    pool = [
        r
        for r in history
        if (not exclude_batch or r.get("batch") != exclude_batch)
        and r.get("client") == client
        and r.get("task") in wanted
        and not r.get("dry_run")
        and not results.is_excluded(r)
        and (tools_broken is None or image_tools_broken(_row_image(r)) == tools_broken)
    ]
    candidates = [
        leader_from_rows(rows, key, timeout)
        for key, rows in runs(pool, suites).items()
        if {r["task"] for r in rows} == wanted
    ]
    # A leader that never passed sets a failure threshold of ceil(0.30 x 0) =
    # 0, which stops a new run after its first trial, a pass included. It is
    # no bar to clear, so it is no leader (code review, 2026-10-05).
    candidates = [c for c in candidates if c.passes > 0]
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
