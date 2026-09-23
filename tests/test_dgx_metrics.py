"""Parse-and-format layer of scripts/dgx_metrics.py (#404 heartbeat metrics).

The gcx subprocess is the untested boundary; `scalar` and `format_line` are the
logic, exercised here against captured gcx JSON and fixed dicts so the heartbeat
line cannot silently change shape.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import dgx_metrics as dm

# A real gcx instant-vector result (value under "value").
INSTANT = '{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1789471680,"53.25"]}]}}'
# A range/matrix result (values under "values"); scalar takes the last point.
MATRIX = '{"status":"success","data":{"resultType":"matrix","result":[{"metric":{},"values":[[1,"8053.9"],[2,"10161.5"]]}]}}'
EMPTY = '{"status":"success","data":{"resultType":"vector","result":[]}}'


def test_scalar_reads_instant_value():
    assert dm.scalar(INSTANT) == 53.25


def test_scalar_reads_last_matrix_value():
    assert dm.scalar(MATRIX) == 10161.5


def test_scalar_empty_is_none():
    assert dm.scalar(EMPTY) is None


def test_scalar_bad_json_is_none():
    assert dm.scalar("not json") is None
    assert dm.scalar("") is None


def test_filter_selector():
    assert dm._filter(None) == ""
    assert dm._filter("nemotron-3.5-lightning-30b-a3b") == (
        '{model_name="nemotron-3.5-lightning-30b-a3b"}'
    )


def test_format_line_full():
    snap = {
        "gen_tps": 53.2,
        "prefill_peak_tps": 14458.4,
        "prefix_hit_pct": 92.1,
        "running": 1.0,
        "waiting": 0.0,
    }
    line = dm.format_line(snap)
    assert line == (
        "vLLM: decode 53 tok/s, prefix-hit 92%, running 1/waiting 0, "
        "prefill peak 14458 tok/s"
    )


def test_format_line_missing_values_show_na_not_crash():
    line = dm.format_line({"gen_tps": None})
    assert "decode n/a tok/s" in line
    assert "prefix-hit n/a%" in line


# A real gcx Influx table for the smart plug (#454): columns + rows.
OUTLET = (
    '{"columns":["Time","value","domain","entity_id"],'
    '"rows":[["2026-09-17T10:31:07Z",35.4,"sensor","dgx_current_consumption"]]}'
)


def test_outlet_value_reads_the_value_column():
    assert dm.outlet_value(OUTLET) == 35.4


def test_outlet_value_uses_the_column_name_not_position():
    reordered = '{"columns":["value","Time"],"rows":[[76.7,"2026-09-17T10:17:27Z"]]}'
    assert dm.outlet_value(reordered) == 76.7


def test_outlet_value_empty_or_bad_is_none():
    assert dm.outlet_value('{"columns":["Time","value"],"rows":[]}') is None
    assert dm.outlet_value('{"columns":["Time"],"rows":[["t"]]}') is None
    assert dm.outlet_value("") is None
    assert dm.outlet_value("[1, 2]") is None


def test_outlet_flux_targets_the_plug_entity():
    q = dm.outlet_flux("30m", "max")
    assert "range(start: -30m)" in q
    assert 'r["entity_id"] == "dgx_current_consumption"' in q
    assert q.endswith("|> max()")


def test_format_line_prefixes_wall_power_when_present():
    snap = {"wall_w": 34.7, "wall_peak_w": 76.7, "gen_tps": None}
    line = dm.format_line(snap, window="30m")
    assert line.startswith("wall 35 W (30m peak 77 W) | vLLM: decode n/a tok/s")


def test_outlet_flux_can_target_the_worker_plug():
    q = dm.outlet_flux("10m", "last", dm.WORKER_OUTLET_ENTITY)
    assert 'r["entity_id"] == "dgx_2_current_consumption"' in q
    assert 'r["entity_id"] == "dgx_current_consumption"' not in q


def test_format_line_gives_both_outlets_and_the_pair_sum():
    snap = {
        "wall_w": 53.4,
        "wall_peak_w": 96.2,
        "worker_wall_w": 45.1,
        "worker_wall_peak_w": 48.0,
        "gen_tps": None,
    }
    line = dm.format_line(snap, window="30m")
    assert line.startswith("outlet 53 W + 45 W = 98 W pair (30m peak 96 W + 48 W) | ")


def test_format_line_pair_sum_is_na_when_one_plug_is_silent():
    snap = {"wall_w": 53.4, "wall_peak_w": 96.2, "worker_wall_w": None}
    line = dm.format_line(snap)
    assert line.startswith("outlet 53 W + n/a W = n/a W pair (30m peak 96 W + n/a W)")


def test_format_line_wall_missing_reading_shows_na():
    line = dm.format_line({"wall_w": None, "wall_peak_w": None})
    assert line.startswith("wall n/a W (30m peak n/a W) | ")


def test_format_line_includes_ttft_only_when_present():
    assert "TTFT" not in dm.format_line({"gen_tps": 10.0})
    assert "TTFT p50 0.52s" in dm.format_line({"gen_tps": 10.0, "ttft_p50_s": 0.5172})
