"""Die temperature reading (#91).

A benchmark's numbers moved 8-11% across three sweeps and the only available
explanation was "the machine got hot", which was a guess. These pin the
arithmetic; the sensor read itself is exercised by a live smoke test.
"""

from __future__ import annotations

import json
import pathlib
import sys
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import thermals


def test_calibration_sensors_are_excluded():
    """`tcal` reads ~15 C above the dies and is a reference, not a die.

    Averaging it in would raise the mean by a constant and hide a real rise.
    """
    got = thermals.summarize(
        [("PMU tcal", 51.8), ("PMU tdie1", 36.0), ("PMU tdie2", 38.0)]
    )
    assert got["die_max_c"] == 38.0
    assert got["die_mean_c"] == 37.0
    assert got["sensors"] == 2


def test_it_falls_back_when_no_die_sensor_is_named():
    """Another Mac may name them differently; report something, not nothing."""
    got = thermals.summarize([("SOC MTR Temp Sensor", 40.0), ("gas gauge", 30.0)])
    assert got["die_max_c"] == 40.0
    assert got["sensors"] == 2


def test_an_empty_read_is_empty_not_zero():
    """Zero degrees would read as a very cold Mac rather than a failed read."""
    assert thermals.summarize([]) == {}


def test_a_reading_carries_the_system_clock():
    got = thermals.reading()
    assert got["utc"].endswith("Z") and "T" in got["utc"]
    assert got["local"]


@pytest.mark.skipif(
    not thermals.SUPPORTED,
    reason="IOKit thermal sensors are macOS-only; there is no Linux equivalent to read",
)
def test_the_machine_reports_plausible_die_temperatures():
    """Live check. A laptop that is on is between 10 C and 120 C.

    Skipped off macOS, and the skip is keyed on `thermals.SUPPORTED` rather than
    on a platform string in this file, so the day a Linux backend is added the
    test starts running instead of staying quietly skipped. A skipping test is
    not a passing test: on the machine that owns the sensors this still runs.
    """
    got = thermals.reading()
    assert got.get("sensors", 0) > 0, "no thermal sensors readable"
    assert 10.0 < got["die_max_c"] < 120.0
    assert got["die_mean_c"] <= got["die_max_c"]


def test_fan_rpm_is_absent_rather_than_wrong_when_fancontrol_fails(monkeypatch):
    """A missing tool must not put a fabricated speed next to a temperature."""
    monkeypatch.setattr(
        thermals.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="no"),
    )
    assert thermals.fan_rpm() == {}


def test_fan_rpm_survives_garbage_output(monkeypatch):
    monkeypatch.setattr(
        thermals.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="not json", stderr=""
        ),
    )
    assert thermals.fan_rpm() == {}


def test_fan_rpm_reports_each_fan_and_the_maximum(monkeypatch):
    payload = json.dumps(
        {
            "fans": [
                {"index": 0, "actual_rpm": 3456, "mode": "auto"},
                {"index": 1, "actual_rpm": 5777, "mode": "forced"},
            ]
        }
    )
    monkeypatch.setattr(
        thermals.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=payload, stderr=""),
    )
    got = thermals.fan_rpm()
    assert got["fan0_rpm"] == 3456
    assert got["fan1_mode"] == "forced"
    assert got["fan_rpm_max"] == 5777


def test_a_stopped_fan_is_zero_not_missing(monkeypatch):
    """Idle Apple Silicon genuinely stops its fans; 0 is a reading."""
    payload = json.dumps({"fans": [{"index": 0, "actual_rpm": 0, "mode": "auto"}]})
    monkeypatch.setattr(
        thermals.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=payload, stderr=""),
    )
    assert thermals.fan_rpm()["fan0_rpm"] == 0


# ---------------------------------------------------------------------------
# #326: the Nvidia machines throttle under a long sweep, and thermals.py read
# nothing there. nvidia-smi reports temperature, power, and clocks without root.


def test_parse_nvidia_smi_reads_one_gpu():
    got = thermals.parse_nvidia_smi("0, 45, 389.67, 1965, 9501, 98")
    assert got["gpu0_temp_c"] == 45.0
    assert got["gpu0_power_w"] == 389.67
    assert got["gpu0_clock_mhz"] == 1965
    assert got["gpu0_util_pct"] == 98
    assert got["gpu_temp_max_c"] == 45.0
    assert got["gpu_power_max_w"] == 389.67


def test_parse_nvidia_smi_reports_the_hottest_of_several():
    """A multi-GPU box's throttle is the hottest die, not the first one."""
    got = thermals.parse_nvidia_smi(
        "0, 61, 250.0, 1800, 9501, 100\n1, 72, 300.5, 1750, 9501, 100\n"
    )
    assert got["gpu1_temp_c"] == 72.0
    assert got["gpu_temp_max_c"] == 72.0
    assert got["gpu_power_max_w"] == 300.5


def test_parse_nvidia_smi_skips_na_fields_without_dropping_the_row():
    """nvidia-smi prints [N/A] for a field a GPU cannot report; keep the rest."""
    got = thermals.parse_nvidia_smi("0, 45, [N/A], 1965, 9501, 0")
    assert got["gpu0_temp_c"] == 45.0
    assert "gpu0_power_w" not in got
    assert "gpu_power_max_w" not in got
    assert got["gpu0_clock_mhz"] == 1965


def test_parse_nvidia_smi_is_empty_on_garbage():
    assert thermals.parse_nvidia_smi("no such output") == {}
    assert thermals.parse_nvidia_smi("") == {}


def test_gpu_thermals_absent_when_nvidia_smi_is_missing(monkeypatch):
    """No fabricated temperature when the tool is not installed."""
    monkeypatch.setattr(thermals, "NVIDIA", None)
    assert thermals.gpu_thermals() == {}


def test_gpu_thermals_absent_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(thermals, "NVIDIA", "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        thermals.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=9, stdout="", stderr="err"),
    )
    assert thermals.gpu_thermals() == {}


@pytest.mark.skipif(
    not thermals.NVIDIA,
    reason="nvidia-smi is not installed; there is no GPU thermal source to read",
)
def test_the_gpu_reports_a_plausible_temperature():
    """Live check on a machine with nvidia-smi. A GPU that is on is 10-110 C.

    Keyed on `thermals.NVIDIA`, not a platform string, so it runs on the DGX
    and the 3080 Ti and stays skipped only where there is genuinely no GPU.
    """
    got = thermals.reading()
    assert "gpu_temp_max_c" in got, "nvidia-smi returned no temperature"
    assert 10.0 < got["gpu_temp_max_c"] < 110.0


def test_thermals_never_sets_a_fan():
    """Fan state is an operator decision (#116). Reading only.

    Checks invocation, not mention: the docstring names `max` and `set` while
    explaining that they are never called, and an earlier version of this test
    banned the string and failed on its own documentation.
    """
    import ast

    tree = ast.parse(pathlib.Path(thermals.__file__).read_text())
    argv_lists = [
        [e.value for e in node.elts if isinstance(e, ast.Constant)]
        for node in ast.walk(tree)
        if isinstance(node, ast.List)
    ]
    fan_calls = [a for a in argv_lists if a and a[0] == "fancontrol"]
    assert fan_calls, "expected thermals to shell out to fancontrol"
    for argv in fan_calls:
        assert argv[1] == "status", f"thermals must only read: {argv}"
        assert "max" not in argv and "set" not in argv
