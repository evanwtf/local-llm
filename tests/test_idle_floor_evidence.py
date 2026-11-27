"""Tests for scripts/idle_floor_evidence.py (#1450): parsing and counting.

Offline: every test feeds captured-shape gcx JSON or hand-built windows. The
live query is one `integration` test that skips without gcx and the host
names.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import idle_floor_evidence as ev

dgx_hosts = ev.dgx_hosts


def _matrix(*series: tuple[dict[str, str], list[tuple[int, str]]]) -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {"metric": labels, "values": [[ts, v] for ts, v in points]}
                    for labels, points in series
                ],
            },
        }
    )


def _w(peak: float | None, **kw: object) -> ev.Window:
    base: dict[str, object] = {
        "ts": 0,
        "peak_w": peak,
        "models": ("glm",),
        "running_max": 0.0,
        "running_min": 0.0,
        "tokens": 0.0,
        "requests": 0.0,
    }
    base.update(kw)
    return ev.Window(**base)  # type: ignore[arg-type]


def test_merge_points_reads_one_series() -> None:
    got = ev.merge_points(_matrix(({}, [(600, "12.5"), (1200, "NaN?")])))
    assert got == {600: 12.5}


def test_merge_points_survives_a_failed_call() -> None:
    assert ev.merge_points("") == {}
    assert ev.merge_points("not json") == {}


def test_resident_models_collects_each_model_up() -> None:
    raw = _matrix(
        ({"model": "b"}, [(600, "1"), (1200, "1")]),
        ({"model": "a"}, [(600, "1")]),
    )
    assert ev.resident_models(raw) == {600: ("a", "b"), 1200: ("b",)}


def test_windows_take_the_busier_node() -> None:
    series = {"head_peak_w": {600: 10.5}, "worker_peak_w": {600: 12.1}}
    (w,) = ev.windows(series, {600: ("m",)})
    assert w.peak_w == 12.1
    assert w.models == ("m",)


def test_windows_keep_a_head_only_reading() -> None:
    (w,) = ev.windows({"head_peak_w": {600: 9.0}, "worker_peak_w": {}}, {})
    assert w.peak_w == 9.0
    assert not w.resident


def test_quiet_needs_a_resident_model_and_the_token_counter() -> None:
    assert _w(11.0).quiet
    assert not _w(11.0, models=()).quiet
    assert not _w(11.0, tokens=None).quiet
    assert not _w(11.0, tokens=5.0).quiet
    assert not _w(11.0, running_max=1.0).quiet
    assert not _w(11.0, requests=1.0).quiet
    # Absent request counters are not activity.
    assert _w(11.0, running_max=None, requests=None).quiet


def test_generating_and_live() -> None:
    assert _w(16.0, tokens=40.0).generating
    assert _w(16.0, running_max=1.0).generating
    assert not _w(16.0, tokens=40.0).live
    assert _w(16.0, running_min=1.0).live
    assert not _w(16.0, running_min=1.0, models=()).live


def test_count_misses_quiet_windows_at_or_above_the_floor() -> None:
    wins = [_w(11.8), _w(12.0), _w(12.2), _w(13.5)]
    rows = ev.count(wins, [12.0, 13.0, 14.0])
    assert [(r.missed, r.quiet) for r in rows] == [(3, 4), (1, 4), (0, 4)]


def test_count_kills_working_windows_under_the_floor() -> None:
    wins = [
        _w(15.8, tokens=10.0, models=("inkling-2-mini",)),
        _w(17.3, tokens=10.0, models=("inkling-2-mini",)),
        _w(30.0, running_min=1.0, running_max=1.0),
        _w(12.5, tokens=3.0),
    ]
    rows = ev.count(wins, [13.0, 20.0])
    at13, at20 = rows
    assert (at13.gen_killed, at13.generating) == (1, 4)
    assert (at13.light_killed, at13.light) == (0, 2)
    assert (at20.gen_killed, at20.light_killed) == (3, 2)
    assert (at20.live_killed, at20.live) == (0, 1)


def test_count_skips_windows_without_power() -> None:
    (row,) = ev.count([_w(None), _w(None, tokens=4.0)], [13.0])
    assert (row.quiet, row.generating) == (0, 0)


def test_table_has_one_line_per_floor() -> None:
    rows = ev.count([_w(11.0), _w(14.0)], [12.0, 13.0])
    lines = ev.table(rows)
    assert len(lines) == 2 + 2
    assert lines[2].startswith("12 W | 1 of 2 (50%)")
    assert "0 of 0" in lines[2]


def test_spans_cover_the_range_back_to_back() -> None:
    assert ev.spans(0, 2500, 1000) == [(0, 1000), (1000, 2000), (2000, 2500)]


def test_off_grid_flags_a_widened_step() -> None:
    assert ev.off_grid([600, 1200], 600) == []
    assert ev.off_grid([600, 1500], 600) == [1500]


def test_fetch_refuses_points_off_the_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_DGX_HEAD", "h")
    monkeypatch.setenv("LOCAL_LLM_DGX_WORKER", "w")

    def prom(_q: str, _a: int, _b: int, _s: int) -> str:
        return _matrix(({}, [(900, "10")]))

    with pytest.raises(SystemExit, match="off the"):
        ev.fetch(0, 1200, prom)


def test_fetch_joins_every_series(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_DGX_HEAD", "h")
    monkeypatch.setenv("LOCAL_LLM_DGX_WORKER", "w")
    asked: list[str] = []

    def prom(q: str, _a: int, _b: int, _s: int) -> str:
        asked.append(q)
        if "engine_up" in q:
            return _matrix(({"model": "m"}, [(600, "1")]))
        if 'instance="w:9400"' in q:
            return _matrix(({}, [(600, "12.1")]))
        if "POWER" in q:
            return _matrix(({}, [(600, "10.6")]))
        return _matrix(({}, [(600, "0")]))

    (w,) = ev.fetch(0, 600, prom)
    assert w.peak_w == 12.1
    assert w.quiet
    assert any('instance="h:9400"' in q for q in asked)


def _live_ready() -> bool:
    if shutil.which("gcx") is None:
        return False
    try:
        dgx_hosts.lookup("head")
        dgx_hosts.lookup("worker")
    except dgx_hosts.HostUnknown:
        return False
    return True


@pytest.mark.integration
@pytest.mark.skipif(not _live_ready(), reason="needs gcx and the DGX host names")
def test_a_live_day_has_power_on_the_grid() -> None:
    import time

    end = int(time.time()) // ev.WINDOW_S * ev.WINDOW_S
    wins = ev.fetch(end - 86400, end)
    if not any(w.peak_w is not None for w in wins):
        pytest.skip("gcx returned no power data (not authenticated?)")
    assert all(w.ts % ev.WINDOW_S == 0 for w in wins)


# --- #1453: the engine rule ------------------------------------------------


def test_resident_engines_collects_each_engine_up() -> None:
    raw = _matrix(
        ({"engine": "vllm", "model": "m"}, [(600, "1")]),
        ({"engine": "tensorfold", "model": "t"}, [(600, "1"), (1200, "1")]),
    )
    assert ev.resident_engines(raw) == {
        600: ("tensorfold", "vllm"),
        1200: ("tensorfold",),
    }


def test_the_engine_rule_covers_the_watchdogs_engines() -> None:
    # The exporter writes llama.cpp as "llamacpp"; tasks.toml as "llama.cpp".
    assert ev.has_engine_signal(("vllm",))
    assert ev.has_engine_signal(("llamacpp",))
    assert ev.has_engine_signal(("sglang",))
    assert not ev.has_engine_signal(("tensorfold",))
    assert not ev.has_engine_signal(())


def test_rule_count_judges_engine_windows_at_the_engine_floor() -> None:
    wins = [
        _w(14.0, engines=("vllm",)),  # quiet, 13 <= 14 < 20: power misses it
        _w(14.0, engines=("tensorfold",)),  # quiet, power rule: missed
        _w(21.0, engines=("vllm",)),  # quiet, above the engine floor: missed
        _w(10.0, engines=("vllm",)),  # quiet, caught by both
    ]
    row = ev.count_rule(wins, power_floor=13.0, engine_floor=20.0)
    assert (row.quiet, row.missed) == (4, 2)
    (old,) = ev.count(wins, [13.0])
    assert old.missed == 3


def test_rule_count_never_kills_a_working_engine_window() -> None:
    wins = [
        _w(5.0, engines=("vllm",), tokens=100.0, running_max=1.0),
        _w(5.0, engines=("tensorfold",), tokens=100.0, running_max=1.0),
    ]
    row = ev.count_rule(wins, power_floor=13.0, engine_floor=20.0)
    # The vLLM window has the engine's word; the TensorFold one is judged by
    # power alone, as the watchdog does.
    assert (row.generating, row.gen_killed) == (2, 1)


def test_a_saved_window_without_engines_loads() -> None:
    # --save files written before #1453 have no `engines` key.
    w = ev.Window(
        ts=0,
        peak_w=1.0,
        models=(),
        running_max=None,
        running_min=None,
        tokens=None,
        requests=None,
    )
    assert w.engines == ()


def test_missed_by_engine_splits_the_gain() -> None:
    wins = [
        _w(14.0, engines=("vllm",)),
        _w(14.0, engines=("tensorfold",)),
        _w(10.0, engines=("tensorfold",)),
        _w(14.0, engines=("vllm",), tokens=5.0),  # not quiet: left out
    ]
    got = ev.missed_by_engine(wins, power_floor=13.0, engine_floor=20.0)
    assert got == {"tensorfold": (2, 1, 1), "vllm": (1, 1, 0)}
