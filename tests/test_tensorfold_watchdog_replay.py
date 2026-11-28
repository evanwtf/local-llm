"""Tests for scripts/tensorfold_watchdog_replay.py (#1545).

Offline: trial selection from ledger-shaped rows, the join on the 15 s grid,
and the replay through the real IdleStallWatchdog. The series shapes are the
stored ones: the 2026-10-21 Qwen3.8 TensorFold stall (gauge 0, decode tokens
flat at 49337, the head at 10.7-12.1 W) and the 2026-11-28 Ling-3.1 decode
(gauge 1 for 12 min, the counter flat until the request finished).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import tensorfold_watchdog_replay as rp

BACKENDS = {
    "tf1": {"engine": "tensorfold", "tier": "gb10-spark"},
    "tf2": {"engine": "tensorfold", "tier": "gb10-spark-x2"},
}
T0 = 1_792_000_000 - 1_792_000_000 % 15


def _stamp(ts: int) -> str:
    import datetime as dt

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
        _row("tf2", T0, T0 + 984, timeout_reason="gpu-idle-stall"),  # to finished
        _row("tf1", T0 - 100, T0 + 900),  # before `since`
    ]
    got = rp.trials(rows, BACKENDS, since=T0)
    assert [(t.backend, t.seconds, t.timeout_reason) for t in got] == [
        ("tf1", 700, None),
        ("tf2", 984, "gpu-idle-stall"),
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


def _samples(watts: float, running: float | None, tokens: float, n: int = 45):
    return [rp.Sample(T0 + 15 * i, watts, running, tokens) for i in range(n)]


def test_replay_stops_the_stored_stall_under_both_rules() -> None:
    s = _samples(10.8, 0.0, 49337.0)
    assert rp.replay(s, power_floor=13.0, engine_floor=None) == T0 + 600
    assert rp.replay(s, power_floor=13.0, engine_floor=20.0) == T0 + 600


def test_replay_engine_rule_catches_a_quiet_window_above_the_tier_floor() -> None:
    s = _samples(14.0, 0.0, 49337.0)
    assert rp.replay(s, power_floor=13.0, engine_floor=None) is None
    assert rp.replay(s, power_floor=13.0, engine_floor=20.0) == T0 + 600


def test_replay_keeps_a_decode_whose_counter_is_flat() -> None:
    # The counter counts finished requests; the gauge carries the decode.
    s = _samples(5.0, 1.0, 173085.0, n=60)
    assert rp.replay(s, power_floor=13.0, engine_floor=20.0) is None
    assert rp.replay(s, power_floor=13.0, engine_floor=None) == T0 + 600


def test_replay_a_counter_move_resets_the_engine_rule() -> None:
    # 2026-10-21 03:03:30Z: +114 decode tokens with the gauge 0 at both polls.
    s = _samples(10.8, 0.0, 49337.0, n=40)
    s += _samples(10.8, 0.0, 49451.0, n=45)[40:]
    s = [rp.Sample(T0 + 15 * i, x.watts, x.running, x.tokens) for i, x in enumerate(s)]
    assert rp.replay(s, power_floor=13.0, engine_floor=None) == T0 + 600
    assert rp.replay(s, power_floor=13.0, engine_floor=20.0) is None


def test_replay_without_the_gauge_falls_back_to_power() -> None:
    s = _samples(14.0, None, 0.0)
    assert rp.replay(s, power_floor=13.0, engine_floor=20.0) is None
    s = _samples(10.0, None, 0.0)
    assert rp.replay(s, power_floor=13.0, engine_floor=20.0) == T0 + 600


def test_gauge_share() -> None:
    s = _samples(10.0, 0.0, 0.0, n=3) + _samples(10.0, None, 0.0, n=1)
    assert rp.gauge_share(s) == 0.75
    assert rp.gauge_share([]) == 0.0


def _matrix(points: dict[int, float]) -> str:
    return json.dumps(
        {
            "data": {
                "result": [
                    {"metric": {}, "values": [[ts, str(v)] for ts, v in points.items()]}
                ]
            }
        }
    )


def test_fetch_trial_chunks_and_fills_the_hosts(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_DGX_HEAD", "h")
    monkeypatch.setenv("LOCAL_LLM_DGX_WORKER", "w")
    asked: list[tuple[str, int, int, int]] = []

    def prom(q: str, a: int, b: int, step: int) -> str:
        asked.append((q, a, b, step))
        return _matrix({ts: 1.0 for ts in range(a, b + 1, step)})

    trial = rp.Trial("tf2", "t", T0, T0 + 1800, "gb10-spark-x2", None)
    samples = rp.fetch_trial(trial, prom)
    assert len(samples) == 121
    assert all(step == 15 for *_, step in asked)
    assert all(b - a <= rp.CHUNK_S for _, a, b, _ in asked)
    assert any('instance="w:9400"' in q for q, *_ in asked)


def test_fetch_trial_refuses_a_widened_step(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_DGX_HEAD", "h")

    def prom(q: str, a: int, b: int, step: int) -> str:
        return _matrix({a + 7: 1.0})

    trial = rp.Trial("tf1", "t", T0, T0 + 600, "gb10-spark", None)
    with pytest.raises(SystemExit, match="off the 15s grid"):
        rp.fetch_trial(trial, prom)


def test_summary_counts_each_rule() -> None:
    t = rp.Trial("tf1", "t", T0, T0 + 900, "gb10-spark", "gpu-idle-stall")
    lines = rp.summary([rp.Verdict(t, 1.0, T0 + 600, None)])
    assert "power13=+600s rule=-" in lines[0]
    assert lines[-1].startswith("total: 1 trials; stopped by power 1, by the rule 0")
