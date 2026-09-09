"""The slope test behind the #276 cooldown gate.

This is the arithmetic a benchmark's phase boundaries rest on. A gate that
says "settled" while the die is still falling biases every phase after it, and
nothing downstream can detect that from the rows.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import thermal_settle as ts


def ramp(rate_c_per_min: float, n: int = 40, step: float = 5.0, start: float = 50.0):
    return [(i * step, start + rate_c_per_min * (i * step) / 60.0) for i in range(n)]


def test_a_flat_series_has_zero_slope() -> None:
    assert ts.slope_c_per_min([(i * 5.0, 37.0) for i in range(20)]) == 0.0


def test_the_slope_is_per_minute_not_per_sample() -> None:
    """A unit error here is invisible: every number stays plausible.

    -1 C/min over 5-second samples is -0.0833 C per sample. Reporting the
    per-sample figure would make a die falling a degree a minute look settled
    against any bound expressed in C/min.
    """
    got = ts.slope_c_per_min(ramp(-1.0))
    assert got is not None
    assert abs(got - (-1.0)) < 1e-9


def test_too_few_points_is_none_not_zero() -> None:
    """Zero is the settled answer. Inventing it from no data ends the wait."""
    assert ts.slope_c_per_min([(0.0, 40.0), (5.0, 40.0)]) is None
    assert ts.slope_c_per_min([]) is None


def test_samples_all_at_one_instant_are_none_not_zero() -> None:
    """A degenerate fit has no slope. It must not report a flat one."""
    assert ts.slope_c_per_min([(3.0, 40.0 + i) for i in range(8)]) is None


def test_a_slow_steady_fall_is_refused_where_a_difference_of_means_passes() -> None:
    """20 C/hour: 0.167 C between consecutive 30 s means, inside a 0.3 C bar.

    The whole reason this module exists. The slope sees it; a difference of
    means does not.
    """
    samples = ramp(-20.0 / 60.0)
    done, slope = ts.settled(
        samples, samples[-1][0], width_s=180.0, max_slope_c_per_min=0.3
    )
    assert slope is not None and abs(slope - (-1 / 3)) < 1e-6
    assert not done, "0.333 C/min exceeds the 0.3 C/min bound"


def test_noise_without_a_trend_settles() -> None:
    """A sawtooth has no slope, so a wait must not hang on it forever."""
    samples = [(i * 5.0, 37.0 + (1.0 if i % 2 else -1.0)) for i in range(40)]
    done, slope = ts.settled(
        samples, samples[-1][0], width_s=180.0, max_slope_c_per_min=0.3
    )
    assert slope is not None and abs(slope) < 0.3
    assert done


def test_the_window_is_trailing_so_old_cooling_stops_counting() -> None:
    """A wait that began hot must settle on what the die is doing NOW.

    Without a trailing window the early plunge dominates the fit forever and
    the gate can never end, whatever the die is currently doing.
    """
    hot = [(i * 5.0, 90.0 - i * 2.0) for i in range(20)]  # steep, then:
    flat = [(100.0 + i * 5.0, 50.0) for i in range(40)]
    samples = hot + flat
    done, _ = ts.settled(
        samples, samples[-1][0], width_s=180.0, max_slope_c_per_min=0.3
    )
    assert done, "the trailing window must exclude the early plunge"
    whole, _ = ts.settled(
        samples, samples[-1][0], width_s=10_000.0, max_slope_c_per_min=0.3
    )
    assert not whole, "and including it must be what changes the answer"


def test_a_rise_is_refused_as_firmly_as_a_fall() -> None:
    """Absolute slope. A die warming is not settled either."""
    samples = ramp(+1.0)
    done, slope = ts.settled(
        samples, samples[-1][0], width_s=180.0, max_slope_c_per_min=0.3
    )
    assert slope is not None and slope > 0
    assert not done
