"""Pass rate by trial index -- does a session degrade with use (#120)."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import session_decay as decay


def rows_file(tmp_path, rows):
    p = tmp_path / "results.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_only_rows_with_a_verdict_count(tmp_path):
    """A dry run or a crashed trial is not evidence about a session."""
    p = rows_file(
        tmp_path,
        [
            {"trial": 1, "passed": True},
            {"trial": 1, "dry_run": True},
            {"trial": 1},
            {"trial": 1, "passed": "yes"},
        ],
    )
    assert len(decay.load_rows(p)) == 1


def test_excluded_rows_are_not_evidence(tmp_path):
    """results.py marks a contaminated row excluded; it must not vote."""
    p = rows_file(
        tmp_path,
        [
            {"trial": 1, "passed": False, "excluded": True},
            {"trial": 1, "passed": True},
        ],
    )
    assert len(decay.load_rows(p)) == 1


def test_a_row_without_a_trial_index_is_skipped(tmp_path):
    p = rows_file(tmp_path, [{"passed": True}, {"trial": 2, "passed": True}])
    assert [r["trial"] for r in decay.load_rows(p)] == [2]


def test_by_trial_counts_passes_and_totals():
    rows = [
        {"trial": 1, "passed": True},
        {"trial": 1, "passed": False},
        {"trial": 2, "passed": True},
    ]
    assert decay.by_trial(rows) == {1: (1, 2), 2: (1, 1)}


def test_a_thin_trial_is_flagged_not_quoted_as_a_rate():
    """A rate over three rows is a shape. #120's whole problem is six
    pass-rate points hiding in a variable nobody isolated."""
    text = decay.report({1: (10, 10), 2: (1, 2)})
    assert "fewer than" in text
    assert "[2]" in text


def test_a_well_populated_table_carries_no_warning():
    text = decay.report({1: (9, 10), 2: (8, 10)})
    assert "fewer than" not in text


def test_no_rows_says_so():
    assert "no rows" in decay.report({})


def test_malformed_lines_do_not_stop_the_read(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('not json\n{"trial": 1, "passed": true}\n')
    assert len(decay.load_rows(p)) == 1
