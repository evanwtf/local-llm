"""#55 A3: report.py flags cells where every trial failed with identical output.

An excision is applied and every trial gives the same failure message -> the
tree was never touched. A model that wrote wrong code produces a *different*
failure; a virgin excision produces the same one every time.
"""

from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
sys.path.insert(0, str(HERE / "benchmarks" / "agent"))

import report


def row(backend, task, trial, passed, pytest):
    return {
        "backend": backend,
        "task": task,
        "client": "opencode",
        "trial": trial,
        "passed": passed,
        "pytest": pytest,
        "wall_seconds": 10.0,
    }


def test_a_three_trial_untouched_cell_is_flagged() -> None:
    """The real observed case: excision fatal error identical across trials."""
    cells = {
        ("b", "swift-downsample-buckets"): [
            row(
                "b",
                "swift-downsample-buckets",
                1,
                False,
                "MonitorCore/Downsample.swift:44: Fatal error: removed for benchmark",
            ),
            row(
                "b",
                "swift-downsample-buckets",
                2,
                False,
                "MonitorCore/Downsample.swift:44: Fatal error: removed for benchmark",
            ),
            row(
                "b",
                "swift-downsample-buckets",
                3,
                False,
                "MonitorCore/Downsample.swift:44: Fatal error: removed for benchmark",
            ),
        ],
    }
    got = report.untouched_cells(cells)
    assert len(got) == 1
    assert got[0][0] == "b"
    assert got[0][1] == "swift-downsample-buckets"


def test_a_cell_with_a_pass_is_not_flagged() -> None:
    """A single pass proves the tree was reachable, so identical fails may be
    a real model failure mode -- not the untouched signal."""
    cells = {
        ("b", "t"): [
            row("b", "t", 1, True, "1 passed in 0.1s"),
            row("b", "t", 2, False, "1 failed, 0 passed"),
            row("b", "t", 3, False, "1 failed, 0 passed"),
        ],
    }
    assert report.untouched_cells(cells) == []


def test_different_failure_messages_are_not_flagged() -> None:
    """A model that wrote wrong code produces different failures each time."""
    cells = {
        ("b", "t"): [
            row("b", "t", 1, False, "5 failed, 2 passed"),
            row("b", "t", 2, False, "3 failed, 4 passed"),
            row("b", "t", 3, False, "6 failed, 1 passed"),
        ],
    }
    assert report.untouched_cells(cells) == []


def test_single_trial_never_flags() -> None:
    """One row cannot be identical `across` anything."""
    cells = {("b", "t"): [row("b", "t", 1, False, "1 failed")]}
    assert report.untouched_cells(cells) == []


def test_empty_pytest_output_is_not_flagged() -> None:
    """An empty summary is what a crashed harness looks like, not an untouched
    tree. Blaming the agent for the harness's silence would be the wrong
    diagnosis."""
    cells = {
        ("b", "t"): [
            row("b", "t", 1, False, ""),
            row("b", "t", 2, False, ""),
        ],
    }
    assert report.untouched_cells(cells) == []


def test_a_two_trial_untouched_cell_is_flagged() -> None:
    """n>=2 is enough; the strength grows with n."""
    cells = {
        ("b", "t"): [
            row("b", "t", 1, False, "same failure"),
            row("b", "t", 2, False, "same failure"),
        ],
    }
    got = report.untouched_cells(cells)
    assert got and got[0][2] == "same failure"


def test_a_script_task_all_pass_identical_is_not_flagged() -> None:
    """`script-reverse` emits a terse fixed pass string; not the signal here."""
    cells = {
        ("b", "script-reverse"): [
            row("b", "script-reverse", 1, True, "3/3 checks passed"),
            row("b", "script-reverse", 2, True, "3/3 checks passed"),
            row("b", "script-reverse", 3, True, "3/3 checks passed"),
        ],
    }
    assert report.untouched_cells(cells) == []


# --- saturation (#55 A4) ------------------------------------------------


def test_a_100_percent_cell_is_saturated_at_n3() -> None:
    cells = {
        ("b", "t"): [
            row("b", "t", 1, True, "1 passed"),
            row("b", "t", 2, True, "1 passed"),
            row("b", "t", 3, True, "1 passed"),
        ],
    }
    got = report.saturated_cells(cells)
    assert got == [("b", "t", 3)]


def test_below_min_trials_does_not_flag_saturation() -> None:
    """3/3 clears >37% by exact binomial, not 90%. Below n=3 is even weaker,
    and #23's whole point is that small denominators mislead."""
    cells = {("b", "t"): [row("b", "t", 1, True, "1 passed")]}
    assert report.saturated_cells(cells) == []


def test_a_single_failure_disqualifies_saturation() -> None:
    cells = {
        ("b", "t"): [
            row("b", "t", 1, True, "1 passed"),
            row("b", "t", 2, True, "1 passed"),
            row("b", "t", 3, False, "1 failed"),
        ],
    }
    assert report.saturated_cells(cells) == []


def test_saturation_lists_multiple_cells() -> None:
    cells = {
        ("b", "t1"): [row("b", "t1", i, True, "1 passed") for i in range(1, 4)],
        ("b", "t2"): [row("b", "t2", i, True, "1 passed") for i in range(1, 4)],
    }
    got = report.saturated_cells(cells)
    assert len(got) == 2
    assert {t for _, t, _ in got} == {"t1", "t2"}
