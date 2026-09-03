"""Die temperature reading (#91).

A benchmark's numbers moved 8-11% across three sweeps and the only available
explanation was "the machine got hot", which was a guess. These pin the
arithmetic; the sensor read itself is exercised by a live smoke test.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import thermals


def test_calibration_sensors_are_excluded():
    """`tcal` reads ~15 C above the dies and is a reference, not a die.

    Averaging it in would raise the mean by a constant and hide a real rise.
    """
    got = thermals.summarise(
        [("PMU tcal", 51.8), ("PMU tdie1", 36.0), ("PMU tdie2", 38.0)]
    )
    assert got["die_max_c"] == 38.0
    assert got["die_mean_c"] == 37.0
    assert got["sensors"] == 2


def test_it_falls_back_when_no_die_sensor_is_named():
    """Another Mac may name them differently; report something, not nothing."""
    got = thermals.summarise([("SOC MTR Temp Sensor", 40.0), ("gas gauge", 30.0)])
    assert got["die_max_c"] == 40.0
    assert got["sensors"] == 2


def test_an_empty_read_is_empty_not_zero():
    """Zero degrees would read as a very cold Mac rather than a failed read."""
    assert thermals.summarise([]) == {}


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
