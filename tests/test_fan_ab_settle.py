"""The cooldown gate in `fan_ab.py` (#276).

The gate decides when one phase's thermal state has stopped carrying into the
next, so a defect here does not crash anything -- it silently shifts every
phase's starting temperature and biases the A/B it was added to protect. That
puts it in the class the repo tests first.

The clock and the sensor are both faked. A real run of these cases would take
half an hour and would not be deterministic.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import fan_ab


class FakeClock:
    """A monotonic clock that only advances when something sleeps."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeClock]:
    c = FakeClock()
    monkeypatch.setattr(fan_ab.time, "monotonic", c.monotonic)
    monkeypatch.setattr(fan_ab.time, "sleep", c.sleep)
    yield c


def temps(monkeypatch: pytest.MonkeyPatch, clock: FakeClock, fn) -> None:
    """Drive `die_c` from the fake clock, so a curve is a function of time."""
    monkeypatch.setattr(fan_ab, "die_c", lambda: fn(clock.t))


def test_a_flat_die_settles_at_the_floor_not_before(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """A machine already cold still waits `min_s`.

    Settling instantly would let a phase start while the previous phase's heat
    is still in the chassis but not yet in the sensor.
    """
    temps(monkeypatch, clock, lambda t: 36.5)
    got = fan_ab.cool_to_plateau("t", min_s=60, timeout_s=420)
    assert got["outcome"] == "plateau"
    assert got["waited_s"] >= 60


def test_a_falling_die_does_not_settle_while_it_is_still_falling(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """1 C every 10 s is 3 C per window -- ten times the threshold."""
    temps(monkeypatch, clock, lambda t: 90.0 - t / 10.0)
    got = fan_ab.cool_to_plateau("t", min_s=60, timeout_s=200)
    assert got["outcome"] == "timeout"
    assert got["waited_s"] >= 200


def test_a_decay_settles_once_the_curve_flattens(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """The real shape: exponential decay towards a floor.

    It must settle somewhere in the middle -- not at `min_s` (still falling
    fast) and not at the timeout (it did flatten).
    """
    temps(monkeypatch, clock, lambda t: 36.0 + 50.0 * (0.99**t))
    got = fan_ab.cool_to_plateau("t", min_s=60, timeout_s=600)
    assert got["outcome"] == "plateau"
    assert 60 < got["waited_s"] < 600
    assert got["last_delta_c"] is not None
    assert got["last_delta_c"] <= 0.3


def test_jitter_at_the_measured_amplitude_does_not_defeat_the_gate(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """The case that killed the 1 C margin.

    Idle readings swing a median of 1.77 C peak-to-peak inside 60 s. A gate on
    consecutive samples never fires against that; a gate on 30 s medians must.
    A deterministic sawtooth of +/-1.0 C stands in for the noise.
    """
    temps(monkeypatch, clock, lambda t: 36.5 + (1.0 if int(t // 5) % 2 else -1.0))
    got = fan_ab.cool_to_plateau("t", min_s=60, timeout_s=420)
    assert got["outcome"] == "plateau", "medians should see through +/-1 C jitter"


def test_an_unreadable_sensor_falls_back_instead_of_spinning(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """No sensor is not a plateau, and must not be reported as one."""
    monkeypatch.setattr(fan_ab, "die_c", lambda: None)
    monkeypatch.setattr(fan_ab, "FALLBACK_COOLDOWN_S", 180)
    got = fan_ab.cool_to_plateau("t", min_s=60, timeout_s=420)
    assert got["outcome"] == "no_sensor"
    assert got["waited_s"] < 420, "must not burn the whole timeout reading nothing"
    assert got["samples"] == 0


def test_the_record_always_says_which_way_it_ended(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """Every outcome is one of three known values, with the wait recorded.

    A phase preceded by a timeout is not comparable to one preceded by a
    plateau, and the manifest is the only place a later reader can tell.
    """
    temps(monkeypatch, clock, lambda t: 90.0 - t / 10.0)
    got = fan_ab.cool_to_plateau("t", min_s=60, timeout_s=120)
    assert got["outcome"] in {"plateau", "timeout", "no_sensor"}
    for key in ("waited_s", "started_iso", "ended_iso", "start_die_c", "settle"):
        assert key in got
