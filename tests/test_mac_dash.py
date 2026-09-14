"""Tests for the #228-era gcx snapshot helper.

The subprocess call to gcx is the untested boundary; everything that turns its
JSON into numbers and lines is a pure function and is tested here against
captured gcx output. The failure that matters is a silently empty result (a
wrong instance label, an auth miss) reading as 0 rather than "no data" -- so the
None path is asserted, not only the happy path.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mac_dash

# One instant-query result, as gcx -o json returns it.
SCALAR = '{"status":"success","data":{"resultType":"vector","result":[{"metric":{"__name__":"macos_gpu_utilization_ratio","instance":"192.168.1.112:9650","job":"macmonitor_exporter"},"value":[1789398389.085,"0.97"]}]}}'

# Several series under one metric, keyed by a label.
LABELED = '{"data":{"result":[{"metric":{"sensor":"gpu"},"value":[1,"57.9"]},{"metric":{"sensor":"cpu"},"value":[1,"62.3"]},{"metric":{"sensor":"battery"},"value":[1,"31.9"]}]}}'

EMPTY = '{"status":"success","data":{"resultType":"vector","result":[]}}'


def test_scalar_reads_the_single_value() -> None:
    assert mac_dash.scalar(SCALAR) == 0.97


def test_scalar_on_an_empty_result_is_none_not_zero() -> None:
    """A wrong instance label returns an empty vector. That must read as no
    data -- returning 0.0 would report a 0% GPU or a 0 C sensor as if measured."""
    assert mac_dash.scalar(EMPTY) is None


def test_scalar_on_garbage_is_none() -> None:
    assert mac_dash.scalar("") is None
    assert mac_dash.scalar("not json") is None


def test_labeled_maps_each_series_by_its_label() -> None:
    temp = mac_dash.labeled(LABELED, "sensor")
    assert temp == {"gpu": 57.9, "cpu": 62.3, "battery": 31.9}


def test_labeled_on_empty_is_an_empty_map() -> None:
    assert mac_dash.labeled(EMPTY, "sensor") == {}


def test_render_snapshot_formats_a_heartbeat_line_and_block() -> None:
    snap = {
        "gpu_util": 0.95,
        "vram_bytes": 79.7 * 1024**3,
        "temp": {"gpu": 73.0, "cpu": 64.7, "enclosure": 32.0},
        "fan": {"1": 3109.0, "2": 3353.0},
        "power": {"input": 100.8, "soc": 63.5},
    }
    lines = mac_dash.render_snapshot(snap)
    assert "GPU 95% util, VRAM 79.7 GiB" in lines[0]
    assert "gpu 73.0 C" in lines[0]
    assert "input 100.8 W" in lines[0]
    # the issue block carries the enclosure sensor the one-liner omits
    assert any("enclosure 32.0 C" in ln for ln in lines)


def test_render_snapshot_shows_na_for_a_missing_metric() -> None:
    """When gcx returns nothing for GPU util, the line says n/a, not 0%."""
    snap = {
        "gpu_util": None,
        "vram_bytes": None,
        "temp": {},
        "fan": {},
        "power": {},
    }
    line = mac_dash.render_snapshot(snap)[0]
    assert "GPU n/a util" in line
    assert "0%" not in line


def test_render_envelope_reports_peaks_and_the_util_floor() -> None:
    env = {
        "gpu_temp_max": 98.5,
        "cpu_temp_max": 98.9,
        "power_max": 134.8,
        "power_p90": 115.5,
        "gpu_util_min": 0.0,
    }
    lines = mac_dash.render_envelope(env, "210m")
    body = "\n".join(lines)
    assert "GPU temp peak 98.5 C" in body
    assert "Power peak 134.8 W, p90 115.5 W" in body
    assert "GPU-util floor: 0%" in body
