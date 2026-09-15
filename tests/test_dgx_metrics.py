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


def test_format_line_includes_ttft_only_when_present():
    assert "TTFT" not in dm.format_line({"gen_tps": 10.0})
    assert "TTFT p50 0.52s" in dm.format_line({"gen_tps": 10.0, "ttft_p50_s": 0.5172})
