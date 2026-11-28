"""Replay TensorFold trials through the GPU-idle-stall watchdog, old rule and new (#1545).

The watchdog (benchmarks/agent/timeout_policy.py) kills a trial whose GPU
stays idle for 600 s. Until #1545 TensorFold was judged by power alone, at
the GB10 tier floor (13 W). #1545 judges native TensorFold by its own
running-request gauge (`tensorfold:requests_running`), as #1453 did for
vLLM, SGLang and llama.cpp. This script asks, for every TensorFold trial of
600 s or more in the ledgers: would either rule have stopped it, and when?

For each trial it reads Prometheus through Grafana with gcx (read-only), at
the watchdog's 15 s poll, over the client phase (`started` to `started +
wall_seconds`; a row with no `wall_seconds`, such as a timeout, to
`finished`):

* GPU power: the head's DCGM reading on a single-Spark tier; the larger of
  the head's and the worker's on the two-Spark tier, as the watchdog reads;
* the running gauge: the exporter's `llme_requests_running{engine=
  "tensorfold"}`, else the engine's own `tensorfold:requests_running`
  (native 1.0.x served on a port only Prometheus scraped);
* the token counter: `llme_tokens_total{engine="tensorfold",phase="decode"}`,
  else `tensorfold:generation_tokens_total`.

Then it feeds those samples to the real `IdleStallWatchdog`, with an
injected clock, twice: by power alone at the tier floor, and with the engine
signal at the engine-rule floor. A sample with no gauge falls back to the
power rule, as in the live watchdog. It also reports, per backend, the share
of samples that carried the gauge: a build that never exports it stays on
the power rule.

    uv run python scripts/tensorfold_watchdog_replay.py --days 60

The node names come from `dgx_hosts` (never from the repo, #1153). The gcx
call is the only untested part; tests/test_tensorfold_watchdog_replay.py
covers the trial selection, the join and the replay.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import sys
import time
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import dgx_hosts
import idle_floor_evidence
import timeout_policy

import logs

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parents[1]
TASKS = REPO / "benchmarks" / "agent" / "tasks.toml"
LEDGER_GLOB = "hardware/*GB10*/results.jsonl"

#: The watchdog's poll (timeout_policy.DEFAULT_POLL_SECS), and the step.
STEP_S = int(timeout_policy.DEFAULT_POLL_SECS)
#: Grafana widens the step of a range query past about 100 points (a 1,800 s
#: query at 15 s came back at a 20 s step, 2026-11-27). Ask for 90 at a time;
#: `fetch_trial` refuses a point off the grid.
CHUNK_S = 90 * STEP_S
#: Trials shorter than the window cannot stall.
MIN_TRIAL_S = timeout_policy.DEFAULT_IDLE_STALL_SECS

HEAD_W = 'max(DCGM_FI_DEV_POWER_USAGE{instance="$head:9400"})'
WORKER_W = 'max(DCGM_FI_DEV_POWER_USAGE{instance="$worker:9400"})'
RUNNING = (
    'sum(llme_requests_running{engine="tensorfold"})'
    " or sum(tensorfold:requests_running)"
)
TOKENS = (
    'sum(llme_tokens_total{engine="tensorfold",phase="decode"})'
    " or sum(tensorfold:generation_tokens_total)"
)
TWO_NODE_TIERS = frozenset({"gb10-spark-x2"})


@dataclass(frozen=True)
class Trial:
    """One TensorFold trial's client phase, in unix seconds."""

    backend: str
    task: str
    start: int
    end: int
    tier: str
    timeout_reason: str | None

    @property
    def seconds(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Sample:
    """One 15 s poll. None: the series had no point there."""

    ts: int
    watts: float | None
    running: float | None
    tokens: float | None


def _unix(stamp: str) -> int:
    return int(dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z").timestamp())


def tensorfold_backends(tasks: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    """The tasks.toml backends whose engine is TensorFold, by name."""
    backends = tasks.get("backend", {})
    if not isinstance(backends, Mapping):
        return {}
    return {
        name: b
        for name, b in backends.items()
        if isinstance(b, Mapping) and b.get("engine") == "tensorfold"
    }


def trials(
    rows: Iterable[Mapping[str, object]],
    backends: Mapping[str, Mapping[str, object]],
    since: int,
    min_s: float = MIN_TRIAL_S,
) -> list[Trial]:
    """TensorFold trials that started at or after `since` and ran >= `min_s`.

    The client phase is `started` + `wall_seconds`. A row without
    `wall_seconds` (a timeout, a kill) runs to `finished`.
    """
    out: list[Trial] = []
    for r in rows:
        name = r.get("backend")
        started, finished = r.get("started"), r.get("finished")
        if not isinstance(name, str) or name not in backends:
            continue
        if not isinstance(started, str) or not isinstance(finished, str):
            continue
        start = _unix(started)
        if start < since:
            continue
        wall = r.get("wall_seconds")
        if isinstance(wall, int | float) and not isinstance(wall, bool):
            end = start + int(wall)
        else:
            end = _unix(finished)
        if end - start < min_s:
            continue
        reason = r.get("timeout_reason")
        out.append(
            Trial(
                backend=name,
                task=str(r.get("task", "")),
                start=start,
                end=end,
                tier=str(backends[name].get("tier", "")),
                timeout_reason=reason if isinstance(reason, str) else None,
            )
        )
    return sorted(out, key=lambda t: t.start)


def join(
    series: Mapping[str, Mapping[int, float]], start: int, end: int, two_node: bool
) -> list[Sample]:
    """One sample per step from start to end. Power: the busier node if two."""
    head = series.get("head", {})
    worker = series.get("worker", {})
    out: list[Sample] = []
    for ts in range(start - start % STEP_S, end + 1, STEP_S):
        nodes = [head.get(ts)] + ([worker.get(ts)] if two_node else [])
        readings = [w for w in nodes if w is not None]
        out.append(
            Sample(
                ts=ts,
                watts=max(readings) if readings else None,
                running=series.get("running", {}).get(ts),
                tokens=series.get("tokens", {}).get(ts),
            )
        )
    return out


def replay(
    samples: Sequence[Sample],
    *,
    power_floor: float,
    engine_floor: float | None,
    window_s: float = timeout_policy.DEFAULT_IDLE_STALL_SECS,
) -> int | None:
    """The timestamp the watchdog would stop the trial at, or None.

    `engine_floor` None: the power rule alone. Otherwise the engine rule,
    with a sample whose gauge is missing judged by power, as live.
    """
    i = 0
    now = 0.0

    def watts() -> float | None:
        return samples[i].watts

    def engine() -> timeout_policy.EngineReading | None:
        s = samples[i]
        if s.running is None:
            return None
        return timeout_policy.EngineReading(s.running, s.tokens)

    wd = timeout_policy.IdleStallWatchdog(
        watts,
        idle_floor_watts=power_floor,
        idle_stall_secs=window_s,
        monotonic=lambda: now,
        engine=engine if engine_floor is not None else None,
        engine_floor_watts=engine_floor if engine_floor is not None else power_floor,
    )
    # watts() and engine() read i: the loop moves both samplers on.
    for i, s in enumerate(samples):
        now = float(s.ts)
        wd.sample()
        if wd.stalled():
            return s.ts
    return None


def gauge_share(samples: Sequence[Sample]) -> float:
    """The share of samples that carried the running gauge."""
    return (
        sum(s.running is not None for s in samples) / len(samples) if samples else 0.0
    )


@dataclass(frozen=True)
class Verdict:
    trial: Trial
    gauge: float
    power_stop: int | None
    rule_stop: int | None


def summary(verdicts: Sequence[Verdict]) -> list[str]:
    """Report lines: per trial that either rule stops, then per backend, then totals."""
    lines: list[str] = []
    for v in verdicts:
        if v.power_stop is None and v.rule_stop is None and not v.trial.timeout_reason:
            continue
        t = v.trial

        def at(stop: int | None, start: int = t.start) -> str:
            return "-" if stop is None else f"+{stop - start}s"

        lines.append(
            f"{dt.datetime.fromtimestamp(t.start, dt.UTC):%Y-%m-%dT%H:%M:%S}+0000"
            f" {t.backend} {t.task} {t.seconds}s outcome={t.timeout_reason or 'ok'}"
            f" gauge={v.gauge:.0%} power13={at(v.power_stop)}"
            f" rule={at(v.rule_stop)}"
        )
    by: dict[str, list[Verdict]] = {}
    for v in verdicts:
        by.setdefault(v.trial.backend, []).append(v)
    for name, vs in sorted(by.items()):
        share = sum(v.gauge for v in vs) / len(vs)
        lines.append(
            f"backend {name}: {len(vs)} trials, gauge in {share:.0%} of samples,"
            f" stopped by power {sum(v.power_stop is not None for v in vs)},"
            f" by the rule {sum(v.rule_stop is not None for v in vs)}"
        )
    lines.append(
        f"total: {len(verdicts)} trials;"
        f" stopped by power {sum(v.power_stop is not None for v in verdicts)},"
        f" by the rule {sum(v.rule_stop is not None for v in verdicts)};"
        f" stalled in the ledger"
        f" {sum(v.trial.timeout_reason == 'gpu-idle-stall' for v in verdicts)}"
    )
    return lines


# ------------------------------------------------------------------ gcx ----


def fetch_trial(
    trial: Trial,
    prom: Callable[[str, int, int, int], str] = idle_floor_evidence.range_query,
) -> list[Sample]:
    """The trial's samples, from Prometheus. Read-only."""
    two_node = trial.tier in TWO_NODE_TIERS
    queries = {"head": HEAD_W, "running": RUNNING, "tokens": TOKENS}
    if two_node:
        queries["worker"] = WORKER_W
    start = trial.start - trial.start % STEP_S
    series: dict[str, dict[int, float]] = {}
    for name, template in queries.items():
        promql = dgx_hosts.fill(template)
        got: dict[int, float] = {}
        for a, b in idle_floor_evidence.spans(start, trial.end, CHUNK_S):
            got.update(idle_floor_evidence.merge_points(prom(promql, a, b, STEP_S)))
        if bad := idle_floor_evidence.off_grid(list(got), STEP_S):
            raise SystemExit(f"{name}: {len(bad)} points off the {STEP_S}s grid")
        series[name] = got
    return join(series, trial.start, trial.end, two_node)


def load_rows(paths: Iterable[pathlib.Path]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in paths:
        with path.open() as f:
            rows.extend(json.loads(line) for line in f if line.strip())
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--days", type=float, default=60.0)
    p.add_argument("--min-seconds", type=float, default=MIN_TRIAL_S)
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    backends = tensorfold_backends(tomllib.loads(TASKS.read_text()))
    rows = load_rows(sorted(REPO.glob(LEDGER_GLOB)))
    since = int(time.time() - args.days * 86400)
    todo = trials(rows, backends, since, args.min_seconds)
    logger.info("%d TensorFold trials of %gs or more", len(todo), args.min_seconds)
    verdicts: list[Verdict] = []
    for t in todo:
        backend = backends[t.backend]
        floor, _ = timeout_policy.resolve_idle_floor(None, backend)
        # The rule the watchdog picks for this backend today: a build on
        # POWER_RULE_BACKENDS replays by power alone under both.
        signal, _ = timeout_policy.engine_signal_for(backend, t.backend)
        engine_floor, _ = timeout_policy.resolve_engine_idle_floor(None, backend)
        samples = fetch_trial(t)
        verdicts.append(
            Verdict(
                trial=t,
                gauge=gauge_share(samples),
                power_stop=replay(samples, power_floor=floor, engine_floor=None),
                rule_stop=replay(
                    samples,
                    power_floor=floor,
                    engine_floor=engine_floor if signal is not None else None,
                ),
            )
        )
    for line in summary(verdicts):
        logger.info("%s", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
