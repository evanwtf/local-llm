"""Tests for the gate that lets the harness disbelieve itself (#55).

The failure it exists to catch: a widely-used client collapsing on a backend
that works fine under another client. That is what --dir produced, and it was
published twice.
"""

from __future__ import annotations

import plausibility


def rows(n, passed, client="opencode", backend="ds4"):
    return [
        {"backend": backend, "client": client, "passed": i < passed} for i in range(n)
    ]


def test_a_collapse_against_a_strong_prior_halts() -> None:
    """The --dir shape: ds4 x claude 46/46, ds4 x opencode 1/15."""
    why = plausibility.implausible(
        rows(15, 1), rows(46, 46, client="claude"), "ds4", "opencode"
    )
    assert why and "harness bug" in why


def test_the_message_names_both_cells_and_the_escape_hatch() -> None:
    why = plausibility.implausible(
        rows(15, 1), rows(46, 46, client="claude"), "ds4", "opencode"
    )
    assert "ds4 x opencode" in why and "ds4 x claude" in why
    assert "--allow-implausible" in why


def test_three_trials_never_halt() -> None:
    """#23 puts a 3-trial median at +/-27.9%. Three is a screening run."""
    assert (
        plausibility.implausible(
            rows(3, 0), rows(46, 46, client="claude"), "ds4", "opencode"
        )
        is None
    )


def test_no_prior_record_says_nothing() -> None:
    """Absence is not evidence. A new backend has no history, and a check that
    fires on every first run is one that gets switched off."""
    assert plausibility.implausible(rows(15, 0), [], "newthing", "opencode") is None


def test_a_thin_prior_says_nothing() -> None:
    """Two passes elsewhere cannot convict a cell of being broken."""
    assert (
        plausibility.implausible(
            rows(15, 0), rows(2, 2, client="claude"), "ds4", "opencode"
        )
        is None
    )


def test_a_weak_prior_says_nothing() -> None:
    """If the backend is mediocre under every client, a low score here is a
    model result, not a contradiction."""
    assert (
        plausibility.implausible(
            rows(15, 0), rows(20, 6, client="claude"), "ds4", "opencode"
        )
        is None
    )


def test_a_healthy_cell_passes() -> None:
    assert (
        plausibility.implausible(
            rows(15, 14), rows(46, 46, client="claude"), "ds4", "opencode"
        )
        is None
    )


def test_a_cell_at_a_third_of_a_perfect_prior_halts() -> None:
    """4/12 against 46/46 on the same tasks. An earlier version of this gate
    used a fixed 25% floor and let 4/14 = 28.6% through -- the exact archived
    cell it was written to catch. The signal is the contrast, not a floor."""
    why = plausibility.implausible(
        rows(12, 4), rows(46, 46, client="claude"), "ds4", "opencode"
    )
    assert why


def test_a_cell_above_half_the_prior_passes() -> None:
    """8/12 against a perfect prior is a weaker result, not a broken one."""
    assert (
        plausibility.implausible(
            rows(12, 8), rows(46, 46, client="claude"), "ds4", "opencode"
        )
        is None
    )


def test_only_matching_tasks_are_compared(tmp_path=None) -> None:
    """Arms must run the same tasks. ds4 x claude is 50% on the five tasks the
    archived opencode cell ran and much higher overall; comparing against the
    wrong denominator is how the first version failed to halt."""
    current = [
        {"backend": "ds4", "client": "opencode", "task": "a", "passed": False}
        for _ in range(6)
    ]
    other_task = [
        {"backend": "ds4", "client": "claude", "task": "b", "passed": True}
        for _ in range(20)
    ]
    assert plausibility.implausible(current, other_task, "ds4", "opencode") is None
    same_task = [
        {"backend": "ds4", "client": "claude", "task": "a", "passed": True}
        for _ in range(20)
    ]
    assert plausibility.implausible(current, same_task, "ds4", "opencode")


def test_the_same_client_is_not_its_own_control() -> None:
    """Comparing a cell to earlier runs of itself would suppress the alarm
    exactly when the bug is longstanding -- which it was, for two weeks."""
    assert (
        plausibility.implausible(
            rows(15, 0), rows(20, 1, client="opencode"), "ds4", "opencode"
        )
        is None
    )


def test_another_backend_is_not_a_control() -> None:
    """The comparison must hold the weights fixed."""
    assert (
        plausibility.implausible(
            rows(15, 0),
            rows(46, 46, client="claude", backend="somethingelse"),
            "ds4",
            "opencode",
        )
        is None
    )


def test_rows_without_a_client_are_ignored() -> None:
    """Pre-client-axis rows cannot support a client comparison."""
    history = [{"backend": "ds4", "client": None, "passed": True} for _ in range(40)]
    assert plausibility.implausible(rows(15, 0), history, "ds4", "opencode") is None


def test_rate_of_nothing_is_zero_not_a_crash() -> None:
    assert plausibility.rate([]) == 0.0
