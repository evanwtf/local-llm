"""Tests for the #67 before/after split.

The split decides which numbers this project is allowed to publish about
OpenCode, so its failure modes matter more than its happy path. It has already
failed once by classifying every row as "before" when `git log` ran in the
wrong directory and returned nothing.
"""

from __future__ import annotations

import pathlib

import pytest

import dirfix


def row(**kw):
    base = {"client": "opencode", "backend": "ds4", "task": "mbox-scan", "passed": True}
    env = {"harness_head": kw.pop("head")} if "head" in kw else {}
    return {**base, **kw, "env": env}


def test_a_row_from_after_the_fix_is_counted_as_after() -> None:
    assert dirfix.era(row(head="28b1da6"), {"28b1da6"}) == "after"


def test_a_row_with_no_provenance_is_before() -> None:
    """Rows predating the env block have no harness_head. That is not unknown
    -- the field was added after the fix, so its absence dates the row."""
    assert dirfix.era(row(), {"28b1da6"}) == "before"


def test_a_full_length_hash_matches_the_short_one() -> None:
    assert dirfix.era(row(head="28b1da6ffffffff"), {"28b1da6"}) == "after"


def test_an_empty_commit_set_refuses_rather_than_reporting_zeroes(tmp_path) -> None:
    """The original bug: `git log` in a non-repo exits cleanly with no output,
    every row lands in "before", and the table looks plausible and is wrong."""
    with pytest.raises(SystemExit, match="no commits"):
        dirfix.fixed_commits(tmp_path)


def test_the_real_repo_resolves_the_fix() -> None:
    heads = dirfix.fixed_commits(pathlib.Path(__file__).resolve().parent)
    assert dirfix.FIX in heads


def test_script_and_excision_are_counted_apart() -> None:
    """A script task has no repo, so it cannot leak an answer or fail excision.
    Averaging the two classes hides which one a client is failing."""
    assert dirfix.task_class(row(task="script-reverse")) == "script"
    assert dirfix.task_class(row(task="mbox-scan")) == "excision"


def test_excluded_rows_never_reach_a_column() -> None:
    counts = dirfix.tally([row(head="x", excluded=True, passed=False)], {"x"})
    assert counts == {}


def test_other_clients_are_ignored() -> None:
    assert dirfix.tally([row(head="x", client="claude")], {"x"}) == {}


def test_wins_and_totals_accumulate_per_cell() -> None:
    rows = [row(head="x"), row(head="x", passed=False), row()]
    counts = dirfix.tally(rows, {"x"})
    assert counts[("ds4", "excision", "after")] == [1, 2]
    assert counts[("ds4", "excision", "before")] == [1, 1]


def test_a_cell_missing_one_era_prints_a_dash_not_a_zero() -> None:
    """'0/0' would read as a measured failure. It is an absence."""
    lines = dirfix.report({("ds4", "script", "after"): [3, 3]})
    assert lines[1].split() == ["ds4", "script", "-", "3/3"]
