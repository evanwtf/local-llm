"""The #191 tail must be countable by task and by backend (#191).

The tail -- a task that runs hundreds of turns instead of tens -- is a rare
stochastic degeneration. These tests pin the counting rules: a row is a tail
event when num_turns > 20, a missing or zero num_turns is not, and the rate is
tail over total so a busy task is not over-weighted by volume alone.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import tail_events as te


def _row(task: str, backend: str, num_turns: int | None) -> dict:
    return {"task": task, "backend": backend, "num_turns": num_turns}


def test_tail_events_counts_only_rows_over_the_threshold():
    rows = [
        _row("a", "x", 5),
        _row("a", "x", 21),  # tail
        _row("a", "x", 334),  # tail: the #191 magnitude
        _row("b", "y", 20),  # exactly at the threshold: not a tail
    ]
    tails = te.tail_events(rows)
    assert [r["num_turns"] for r in tails] == [21, 334]


def test_a_missing_or_zero_num_turns_is_not_a_tail():
    rows = [_row("a", "x", None), _row("a", "x", 0), _row("a", "x", 25)]
    assert len(te.tail_events(rows)) == 1


def test_table_groups_by_key_and_reports_rate():
    rows = [
        _row("a", "x", 5),
        _row("a", "x", 30),  # tail
        _row("b", "x", 40),  # tail
        _row("b", "x", 10),
    ]
    by_task = te._table(rows, "task")
    assert by_task == [("a", 1, 2, 0.5), ("b", 1, 2, 0.5)]


def test_table_accepts_a_tuple_key_for_a_cross_tab():
    rows = [
        _row("a", "x", 30),  # tail
        _row("a", "y", 5),
        _row("b", "x", 5),
    ]
    by_pair = te._table(rows, ("task", "backend"))
    assert by_pair == [("a / x", 1, 1, 1.0), ("a / y", 0, 1, 0.0), ("b / x", 0, 1, 0.0)]


def test_table_sorts_by_tail_count_then_rate():
    rows = [
        _row("a", "x", 30),  # a: 1 tail / 1
        _row("b", "x", 40),  # b: 1 tail / 2
        _row("b", "x", 5),
        _row("c", "x", 5),  # c: 0 tails
    ]
    by_task = te._table(rows, "task")
    assert [t[0] for t in by_task] == ["a", "b", "c"]
    assert by_task[0][1] == 1 and by_task[0][3] == 1.0  # a first: higher rate
