"""`scripts/report.py`'s estimators for #353.

A wall-time ratio between two arms of the same model and engine is a composite
of an engine effect and a behavioural one, and wall time alone cannot say which
moved. These are the two components, already present on every row.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import report

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
