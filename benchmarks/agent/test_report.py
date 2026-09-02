"""The reporting tool's arithmetic (#23's resolution rule).

Written after the same analysis was hand-rolled three times in one evening --
and got the direction of the comparison wrong. A "36% reduction" and a "56%
gap" are the same two numbers with different denominators, and only one of them
is what #23's rule is stated against.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import report


def test_the_gap_is_measured_against_the_smaller_median():
    """292.6 against 187.0 is a 56% gap, not a 36% reduction.

    Reading it the other way -- smaller/larger -- understates every comparison
    and wrongly reported three distinguishable differences as noise.
    """
    assert report.distinguishable(292.6, 187.0) is True
    assert report.distinguishable(235.2, 125.0) is True
    assert report.distinguishable(29.4, 31.5) is False


def test_order_does_not_change_the_answer():
    assert report.distinguishable(187.0, 292.6) == report.distinguishable(292.6, 187.0)


def test_a_missing_median_is_never_distinguishable():
    """Absence is not a small difference."""
    assert report.distinguishable(0, 100) is False
    assert report.distinguishable(None, 100) is False


def test_the_threshold_matches_the_issue():
    """#23: a 3-trial median carries +/-27.9%, so ~56% is the bar."""
    assert 0.55 <= report.RESOLUTION <= 0.57


def test_summarise_uses_verdict_not_the_passed_field():
    """results.verdict() is the accessor; row["passed"] is not.

    A timeout carries passed: None and is a failure, not an absence.
    """
    rows = [
        {"wall_seconds": 10.0, "passed": True},
        {"wall_seconds": 30.0, "passed": None, "error": "timeout"},
    ]
    passed, n, median, worst, spread = report.summarise(rows)
    assert (passed, n) == (1, 2)
    assert median == 20.0
    assert worst == 30.0
    assert spread == 3.0


def test_summarise_handles_an_empty_cell():
    assert report.summarise([]) is None
