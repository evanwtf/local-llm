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


# --- #353: what moved, the agent or the engine ------------------------------


def _row(task, turns, wall, passed=True):
    return {
        "task": task,
        "num_turns": turns,
        "wall_seconds": wall,
        "passed": passed,
        "client": "opencode",
    }


def test_turns_needs_no_precondition_and_ignores_timing():
    """A count of agent actions. It is the half of #353 that always applies."""
    rows = [_row("a", 8, 40.0), _row("a", 8, 400.0), _row("a", 9, 50.0)]
    assert report.turns(rows) == 8
    assert report.turns([{"task": "a", "wall_seconds": 9.0}]) is None


def test_seconds_per_turn_divides_out_the_turn_count():
    """Wall time within a backend is mostly turn count (r=0.932 over 90 rows).

    Two trials of the same work at the same per-turn cost differ 10x in wall
    time and not at all here.
    """
    rows = [_row("a", 4, 40.0), _row("a", 40, 400.0)]
    assert report.seconds_per_turn(rows) == 10.0


def test_a_homogeneous_cell_lets_seconds_per_turn_be_read():
    """qwen38fnq3nothinkdgx spans 1.6x across ten tasks, so the quotient is
    isolating the engine."""
    by_task = {
        "a": [_row("a", 5, 30.0)],  # 6.0 s/turn
        "b": [_row("b", 5, 40.0)],  # 8.0 s/turn
    }
    ok, spread = report.homogeneous(by_task)
    assert ok and spread is not None and spread < report.HOMOGENEITY_LIMIT


def test_a_heterogeneous_cell_does_not():
    """qwen36nvfp4v1dgx spans 6.9x and qwen36nvfp4specdgx 8.4x: a turn costs
    6.90s on one task and 47.93s on another, so dividing by turns removes the
    count but not the per-turn size."""
    by_task = {
        "cheap": [_row("cheap", 10, 69.0)],  # 6.9 s/turn
        "dear": [_row("dear", 10, 479.3)],  # 47.9 s/turn
    }
    ok, spread = report.homogeneous(by_task)
    assert not ok
    assert spread is not None and spread > 6.0


def test_one_task_cannot_show_a_spread_so_it_is_not_called_unreadable():
    """Absence of evidence. A single task says nothing either way, and
    refusing to read the estimator there would be a different error."""
    ok, spread = report.homogeneous({"only": [_row("only", 5, 30.0)]})
    assert ok and spread is None
