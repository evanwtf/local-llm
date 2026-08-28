"""Tests for the trial-count maths.

The Wilson bound decides how many trials a backend gets, which decides how much
machine time this project spends. An off-by-one in the algebra would be
invisible and expensive, so the closed forms are pinned against values computed
by hand.
"""
from __future__ import annotations

import sizing


def test_a_perfect_run_has_the_closed_form_lower_bound():
    """For k == n the Wilson lower bound collapses to n / (n + z^2).

    Against the module's exact z, not a rounded 1.96 -- that rounding moves the
    bound by 5e-5, which is enough to change the answer at the boundary.
    """
    z2 = sizing.Z95**2
    for n in (5, 15, 35, 100):
        assert abs(sizing.wilson_lower(n, n) - n / (n + z2)) < 1e-12


def test_thirty_five_perfect_trials_is_the_threshold_for_ninety_percent():
    """The number quoted throughout RESULTS.md. Pin it."""
    assert sizing.wilson_lower(34, 34) < 0.90
    assert sizing.wilson_lower(35, 35) >= 0.90
    assert sizing.trials_for(0.90) == 35


def test_a_stricter_target_costs_far_more_trials():
    """The cost is not linear, which is the point worth making in the issue."""
    assert sizing.trials_for(0.80) == 16
    assert sizing.trials_for(0.95) == 73
    assert sizing.trials_for(0.99) == 381


def test_a_single_failure_undoes_a_lot_of_trials():
    """46/46 clears 90%; 46/47 does not. One failure is worth ~20 trials."""
    assert sizing.wilson_lower(46, 46) >= 0.90
    assert sizing.wilson_lower(46, 47) < 0.90


def test_the_bound_is_zero_for_no_trials_rather_than_dividing_by_zero():
    assert sizing.wilson_lower(0, 0) == 0.0


def test_a_total_failure_has_a_zero_lower_bound():
    assert sizing.wilson_lower(0, 10) == 0.0


# --- wall time ------------------------------------------------------------

def test_a_tight_distribution_needs_few_trials_to_pin_its_median():
    tight = [100.0, 101.0, 99.0, 100.5, 100.2] * 8
    got = sizing.median_precision(tight, n=5, draws=400, seed=1)
    assert got < 0.05, "a 2% spread should pin the median inside 5%"


def test_a_wide_distribution_needs_many():
    """The real case: 1.74x median spread with 7x tails (#26)."""
    wide = [100.0, 120.0, 95.0, 300.0, 105.0, 700.0, 110.0, 98.0] * 5
    at_3 = sizing.median_precision(wide, n=3, draws=400, seed=1)
    at_20 = sizing.median_precision(wide, n=20, draws=400, seed=1)
    assert at_3 > at_20, "more trials must not make the estimate worse"
    assert at_3 > 0.15, "three trials on this spread cannot be tight"


def test_precision_is_reported_relative_to_the_median_not_in_seconds():
    """Absolute seconds are not comparable across backends 10x apart in speed."""
    slow = [x * 10 for x in [100.0, 120.0, 95.0, 300.0, 105.0]] * 5
    fast = [100.0, 120.0, 95.0, 300.0, 105.0] * 5
    a = sizing.median_precision(slow, n=5, draws=400, seed=7)
    b = sizing.median_precision(fast, n=5, draws=400, seed=7)
    assert abs(a - b) < 1e-9


def test_too_few_samples_to_resample_gives_no_answer():
    assert sizing.median_precision([1.0, 2.0], n=5, draws=100, seed=1) is None


def test_a_suite_total_is_tighter_than_any_one_task_median():
    """It sums five independent medians, so error partly cancels."""
    wide = [100.0, 120.0, 95.0, 300.0, 105.0, 700.0, 110.0, 98.0] * 5
    one = sizing.median_precision(wide, n=3, draws=800, seed=3)
    suite = sizing.suite_precision(wide, tasks=5, n=3, draws=800, seed=3)
    assert suite < one, "summing independent medians must not add relative error"


def test_suite_precision_declines_with_more_tasks():
    wide = [100.0, 120.0, 95.0, 300.0, 105.0, 700.0] * 6
    assert (sizing.suite_precision(wide, tasks=10, n=3, draws=800, seed=5)
            < sizing.suite_precision(wide, tasks=2, n=3, draws=800, seed=5))
