from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import clock_cap_report as rep


def row(arm: str, rnd: int, c1: float, start: str = "2026-10-31T01:00:00Z") -> dict:
    return {
        "round": rnd,
        "arm": arm,
        "start_utc": start,
        "end_utc": "2026-10-31T01:25:00Z",
        "phases": [
            {"phase": "prefill", "wall_s": 240.0, "prompt_tps": 1900.0, "output_tps": 0.1},
            {"phase": "c1", "wall_s": 600.0, "prompt_tps": 4.0, "output_tps": c1},
            {"phase": "c2", "wall_s": 660.0, "prompt_tps": 5.0, "output_tps": 107.0},
        ],
    }  # fmt: skip


def test_window_is_the_run_in_seconds_and_its_end() -> None:
    w, t = rep.window(row("uncapped", 1, 87.0))
    assert w == 1500
    assert t == 1793409900  # 2026-10-31T01:25:00Z


def test_sensors_pin_every_prom_query_to_the_run_and_sum_the_outlets() -> None:
    seen: list[str] = []

    def prom(q: str) -> float | None:
        seen.append(q)
        return 2400.0

    def flux(q: str) -> float | None:
        if "pws_inside" in q:  # sparse: the last reading, from up to 2 h before
            assert "range(start: 2026-10-30T23:00:00Z, stop: 2026-10-31T01:25:00Z)" in q
            assert "last()" in q
            return 66.0
        assert "range(start: 2026-10-31T01:00:00Z, stop: 2026-10-31T01:25:00Z)" in q
        if "dgx_2_current" in q:
            return 100.0
        if "dgx_current" in q:
            return 150.0
        return 70.0

    out = rep.sensors(row("2200", 1, 80.0), prom, flux)
    assert all("[1500s] @ 1793409900" in q for q in seen)
    assert len(seen) == len(rep.PROM)
    assert (
        'max_over_time(DCGM_FI_DEV_SM_CLOCK{instance="dgx.internal:9400"}'
        "[1500s] @ 1793409900)"
    ) in seen
    assert out["inlet_f"] == 66.0
    assert out["pair_outlet_w_avg"] == 250.0
    assert out["pair_wh"] == 250.0 * 1500 / 3600  # 104.2 Wh, a known absolute


def test_a_missing_outlet_makes_the_pair_unknown_not_half() -> None:
    def flux(q: str) -> float | None:
        return None if "dgx_2_current" in q else 150.0

    out = rep.sensors(row("2200", 1, 80.0), lambda q: 1.0, flux)
    assert out["pair_outlet_w_avg"] is None
    assert out["pair_wh"] is None


def test_flatten_takes_prompt_rate_for_prefill_and_output_rate_otherwise() -> None:
    flat = rep.flatten(row("uncapped", 1, 87.0), {"x": 1.0})
    assert flat["prefill_tps"] == 1900.0
    assert flat["c1_tps"] == 87.0
    assert flat["c2_tps"] == 107.0
    assert flat["wall_s"] == 1500.0


def test_render_gives_absolute_mean_spread_and_share_of_uncapped() -> None:
    runs: list[dict[str, Any]] = [
        rep.flatten(row("uncapped", r, c), {}) for r, c in ((1, 86.0), (2, 88.0))
    ] + [rep.flatten(row("1900", r, c), {}) for r, c in ((1, 79.0), (2, 81.0))]
    text = rep.render(runs, ["uncapped", "1900"])
    line = next(ln for ln in text.splitlines() if ln.startswith("| 1-stream"))
    assert "87.0 (86.0–88.0)" in line
    assert "80.0 (79.0–81.0), 92.0%" in line  # 80 / 87
    assert "| n/a | n/a |" in next(
        ln for ln in text.splitlines() if ln.startswith("| head SM MHz")
    )


def test_flux_value_reads_mean_and_max_columns() -> None:
    mean = '{"columns": ["_value", "_field"], "rows": [[211.6, "value"]]}'
    peak = '{"columns": ["value", "_field"], "rows": [[216.5, "value"]]}'
    assert rep.flux_value(mean) == 211.6
    assert rep.flux_value(peak) == 216.5
    assert rep.flux_value('{"columns": ["_value"], "rows": []}') is None
    assert rep.flux_value("") is None
