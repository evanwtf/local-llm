"""Tests for the variance decomposition.

The #26 conclusion -- that wall-time spread is the agent emitting more tokens,
not the machine running cold -- rests on two pieces of logic that would fail
quietly if wrong: splitting trials into batches, and the correlation. An
off-by-one in the batch split would move the "first trial" ratio without
raising anything.
"""

from __future__ import annotations

import datetime as dt

import pytest
from variance import BATCH_GAP_SECONDS, batches, cells, pearson


def row(minutes_from_zero: float, wall: float = 100.0, **over):
    r = {
        "backend": "ds4",
        "client": "claude",
        "task": "mbox-scan",
        "wall_seconds": wall,
        "num_turns": 10,
        "output_tokens": 2000,
        # Naive on purpose: results.jsonl stores local wall-clock stamps with no
        # offset, and variance.py parses them with a bare fromisoformat.
        "_t": dt.datetime(2026, 8, 20) + dt.timedelta(minutes=minutes_from_zero),  # noqa: DTZ001
    }
    r.update(over)
    return r


def test_trials_minutes_apart_are_one_batch():
    got = batches([row(0), row(5), row(11)])
    assert [len(b) for b in got] == [3]


def test_a_gap_longer_than_the_threshold_starts_a_new_batch():
    gap = BATCH_GAP_SECONDS / 60 + 1
    got = batches([row(0), row(5), row(5 + gap), row(5 + gap + 4)])
    assert [len(b) for b in got] == [2, 2]


def test_a_gap_exactly_at_the_threshold_does_not_split():
    """The boundary is >, not >=. A long trial must not be cut from its batch."""
    got = batches([row(0), row(BATCH_GAP_SECONDS / 60)])
    assert [len(b) for b in got] == [2]


def test_batches_are_ordered_by_time_even_if_the_input_is_not():
    got = batches([row(11), row(0), row(5)])
    assert [r["_t"].minute for r in got[0]] == [0, 5, 11]


def test_a_single_trial_is_a_batch():
    assert [len(b) for b in batches([row(0)])] == [1]


def test_cells_separate_client_and_task_but_not_trial_number():
    grouped = cells(
        [
            row(0, task="mbox-scan", trial=1),
            row(1, task="mbox-scan", trial=2),
            row(2, task="parser-date"),
            row(3, client="codex"),
        ]
    )
    assert len(grouped) == 3
    assert len(grouped[("ds4", "claude", "mbox-scan")]) == 2


def test_pearson_is_one_for_a_perfect_line():
    assert pearson([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_pearson_is_minus_one_when_inverted():
    assert pearson([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_pearson_is_nan_when_a_series_never_moves():
    """A constant series has no spread to correlate; it must not divide by zero."""
    assert pearson([1, 2, 3], [5, 5, 5]) != pearson([1, 2, 3], [5, 5, 5])
