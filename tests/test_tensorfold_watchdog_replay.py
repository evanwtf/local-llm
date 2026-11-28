"""Tests for scripts/tensorfold_watchdog_replay.py (#1545).

Offline: trial selection from ledger-shaped rows, the gcx reply checks, the
join on the 15 s grid, and the replay through the real IdleStallWatchdog.
STALL is the stored series of the 2026-10-21 Qwen3.8 TensorFold kill, from
2026-10-20T22:54:00-0400 to 23:04:00-0400. The other shapes are synthetic
and say so.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import tensorfold_watchdog_replay as rp

BACKENDS = {
    "tf1": {"engine": "tensorfold", "tier": "gb10-spark", "base_url": "http://s:1"},
    "tf2": {"engine": "tensorfold", "tier": "gb10-spark-x2", "base_url": "http://s:1"},
}
T0 = 1_792_000_000 - 1_792_000_000 % 15
#: 2026-10-20T22:54:00-0400 (02:54:00Z), the first idle poll of the stall.
STALL_T0 = int(dt.datetime(2026, 10, 21, 2, 54, tzinfo=dt.UTC).timestamp())
#: The head's DCGM power (scraped every 30 s, so each value shows twice), the
#: gauge (0 throughout) and the decode counter, which moved by 114 tokens at
#: 23:03:30-0400 when a request that arrived at 23:03:23.9-0400 finished.
_W = (
    12.121, 11.609, 11.094, 10.956, 10.844, 10.759, 10.692, 10.737, 10.765,
    10.789, 10.793, 10.789, 10.771, 10.765, 10.776, 10.759, 10.758, 10.748,
    10.746, 10.752,
)  # fmt: skip
STALL = [
    rp.Sample(STALL_T0 + 15 * i, w, 0.0, 49337.0 if i < 38 else 49451.0)
    for i, w in enumerate([w for pair in _W for w in (pair, pair)] + [10.806])
]


def _stamp(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S%z")


def _row(backend: str, start: int, end: int, **kw: object) -> dict[str, object]:
    return {
        "backend": backend,
        "task": "t",
        "started": _stamp(start),
        "finished": _stamp(end),
        **kw,
    }


def test_trials_keep_tensorfold_rows_of_600_s_or_more() -> None:
    rows = [
        _row("tf1", T0, T0 + 900, wall_seconds=700.0),  # kept: client 700 s
        _row("tf1", T0, T0 + 900, wall_seconds=500.0),  # client phase too short
        _row("vllm1", T0, T0 + 900),  # not TensorFold
        _row("tf2", T0, T0 + 984, timeout_reason="gpu-idle-stall"),
        _row("tf1", T0 - 100, T0 + 900),  # before `since`
    ]
    got = rp.trials(rows, BACKENDS, since=T0)
    # A killed row runs to `finished` plus one poll.
    assert [(t.backend, t.seconds, t.timeout_reason) for t in got] == [
        ("tf1", 700, None),
        ("tf2", 999, "gpu-idle-stall"),
    ]
    assert got[1].tier == "gb10-spark-x2"


def test_tensorfold_backends_come_from_the_engine_key() -> None:
    tasks = {"backend": {**BACKENDS, "v": {"engine": "vllm"}}}
    assert set(rp.tensorfold_backends(tasks)) == {"tf1", "tf2"}


def test_join_takes_the_busier_node_on_two_sparks_only() -> None:
    series = {
        "head": {T0: 10.5, T0 + 15: 10.6},
        "worker": {T0: 12.1},
        "running": {T0: 0.0},
        "tokens": {T0: 5.0},
    }
    two = rp.join(series, T0, T0 + 15, two_node=True)
    one = rp.join(series, T0, T0 + 15, two_node=False)
    assert [s.watts for s in two] == [12.1, 10.6]
    assert [s.watts for s in one] == [10.5, 10.6]
    assert two[1].running is None and two[1].tokens is None


def _flat(watts: float, running: float | None, tokens: float, n: int = 45):
    """A synthetic flat stretch, not stored data."""
    return [rp.Sample(T0 + 15 * i, watts, running, tokens) for i in range(n)]


def test_the_stored_stall_is_stopped_by_power_and_not_by_the_engine_rule() -> None:
    """Power at 13 W stops it at 23:04:00-0400, 600 s in. The engine rule
    resets at 23:03:30-0400, when a 114-token request had just finished."""
    power = rp.replay(STALL, power_floor=13.0, engine_floor=None)
    assert power.stop == STALL_T0 + 600
    engine = rp.replay(STALL, power_floor=13.0, engine_floor=20.0)
    assert engine.stop is None
    assert engine.idle_s == 15.0


def test_the_stored_stall_needs_the_poll_after_the_kill() -> None:
    """Ending at the kill (03:03:50Z) drops the 03:04:00Z poll: 585 s counted."""
    short = [s for s in STALL if s.ts <= STALL_T0 + 590]
    got = rp.replay(short, power_floor=13.0, engine_floor=None)
    assert got.stop is None
    assert got.idle_s == 585.0


def test_replay_engine_rule_catches_a_quiet_window_above_the_tier_floor() -> None:
    s = _flat(14.0, 0.0, 49337.0)
    assert rp.replay(s, power_floor=13.0, engine_floor=None).stop is None
    assert rp.replay(s, power_floor=13.0, engine_floor=20.0).stop == T0 + 600


def test_replay_keeps_a_decode_whose_counter_is_flat() -> None:
    # The counter counts finished requests; the gauge carries the decode.
    s = _flat(5.0, 1.0, 173085.0, n=60)
    assert rp.replay(s, power_floor=13.0, engine_floor=20.0).stop is None
    assert rp.replay(s, power_floor=13.0, engine_floor=None).stop == T0 + 600


def test_replay_without_the_gauge_falls_back_to_power() -> None:
    s = _flat(14.0, None, 0.0)
    assert rp.replay(s, power_floor=13.0, engine_floor=20.0).stop is None
    s = _flat(10.0, None, 0.0)
    assert rp.replay(s, power_floor=13.0, engine_floor=20.0).stop == T0 + 600


def test_gauge_share_and_power_missing() -> None:
    s = _flat(10.0, 0.0, 0.0, n=3) + _flat(10.0, None, 0.0, n=1)
    assert rp.gauge_share(s) == 0.75
    assert rp.gauge_share([]) == 0.0
    s[1] = rp.Sample(s[1].ts, None, 0.0, 0.0)
    assert rp.power_missing(s) == 1


def _matrix(pts: dict[int, float]) -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "result": [
                    {"metric": {}, "values": [[ts, str(v)] for ts, v in pts.items()]}
                ]
            },
        }
    )


@pytest.fixture
def hosts(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_DGX_HEAD", "h")
    monkeypatch.setenv("LOCAL_LLM_DGX_WORKER", "w")


@pytest.mark.usefixtures("hosts")
def test_fetch_trial_chunks_and_fills_the_hosts() -> None:
    asked: list[tuple[str, int, int, int]] = []

    def prom(q: str, a: int, b: int, step: int) -> str:
        asked.append((q, a, b, step))
        return _matrix({ts: 1.0 for ts in range(a, b + 1, step)})

    trial = rp.Trial("tf2", "t", T0, T0 + 1800, "gb10-spark-x2", None)
    samples = rp.fetch_trial(trial, prom)
    assert len(samples) == 121
    assert rp.power_missing(samples) == 0
    assert all(step == 15 for *_, step in asked)
    assert all(b - a <= rp.CHUNK_S for _, a, b, _ in asked)
    assert any('instance="w:9400"' in q for q, *_ in asked)


@pytest.mark.usefixtures("hosts")
@pytest.mark.parametrize(
    "reply",
    [
        "",  # range_query's answer when gcx cannot run
        '{"type":"gcx.error","error":{"summary":"Invalid Prometheus query"}}',
        "not json",
    ],
)
def test_a_failed_gcx_call_stops_the_script(reply: str) -> None:
    trial = rp.Trial("tf1", "t", T0, T0 + 600, "gb10-spark", None)
    with pytest.raises(rp.QueryFailed, match="no usable reply"):
        rp.fetch_trial(trial, lambda *_: reply)


@pytest.mark.usefixtures("hosts")
def test_an_empty_result_is_an_absent_series_not_a_failure() -> None:
    empty = json.dumps({"status": "success", "data": {"result": []}})

    def prom(q: str, a: int, b: int, step: int) -> str:
        if "DCGM" in q:
            return _matrix({ts: 10.0 for ts in range(a, b + 1, step)})
        return empty  # no gauge, no counter: a patch 0150 build

    trial = rp.Trial("tf1", "t", T0, T0 + 600, "gb10-spark", None)
    samples = rp.fetch_trial(trial, prom)
    assert rp.power_missing(samples) == 0
    assert rp.gauge_share(samples) == 0.0


@pytest.mark.usefixtures("hosts")
def test_a_step_widened_to_30_s_leaves_the_trial_incomplete() -> None:
    def prom(q: str, a: int, b: int, step: int) -> str:
        return _matrix({ts: 10.0 for ts in range(a, b + 1, 30)})

    trial = rp.Trial("tf1", "t", T0, T0 + 600, "gb10-spark", None)
    samples = rp.fetch_trial(trial, prom)
    assert rp.power_missing(samples) == 20
    v = rp.judge(trial, BACKENDS["tf1"], samples)
    assert not v.complete


@pytest.mark.usefixtures("hosts")
def test_a_step_off_the_grid_stops_the_script() -> None:
    trial = rp.Trial("tf1", "t", T0, T0 + 600, "gb10-spark", None)
    with pytest.raises(rp.QueryFailed, match="off the 15s grid"):
        rp.fetch_trial(trial, lambda q, a, b, s: _matrix({a + 7: 1.0}))


def test_a_partial_series_is_counted_out_of_the_totals() -> None:
    """Power missing for half the trial: the watchdog would hold, and the
    trial would read "not stopped". It is listed and left out instead."""
    trial = rp.Trial("tf1", "t", T0, T0 + 900, "gb10-spark", None)
    samples = _flat(10.0, 0.0, 0.0, n=61)
    samples[30:] = [rp.Sample(s.ts, None, 0.0, 0.0) for s in samples[30:]]
    v = rp.judge(trial, BACKENDS["tf1"], samples)
    assert not v.complete
    lines = rp.summary([v])
    assert "INCOMPLETE: 31 of 61" in lines[0]
    assert any(line.startswith("power coverage: 0 of 1 trials") for line in lines)
    assert lines[-1].startswith("total: 0 complete trials")


def test_summary_counts_each_rule_and_the_split() -> None:
    t = rp.Trial("tf1", "t", STALL_T0, STALL_T0 + 615, "gb10-spark", "gpu-idle-stall")
    v = rp.judge(t, BACKENDS["tf1"], STALL)
    lines = rp.summary([v])
    assert "power13=+600s (2026-10-20T23:04:00-0400)" in lines[0]
    assert "new=- (idle 15s at the end)" in lines[0]
    assert any("engine rule with the gauge in every sample 1" in x for x in lines)
    assert lines[-1].startswith("total: 1 complete trials; stopped by power 1")
