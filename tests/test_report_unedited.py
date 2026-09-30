"""#770 (#55 A/2): a cell whose failures all left the source unedited.

If every failed trial left the source as the harness gave it, the failures say
the agent's edits never reached the disk -- a plumbing verdict -- not that the
model wrote wrong code.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import report


def row(passed: bool, edited: bool | None) -> dict:
    r = {"passed": passed, "pytest": "1 failed" if not passed else "3 passed"}
    if edited is not None:
        r["edited_source"] = edited
    return r


def test_failures_that_edited_nothing_are_flagged():
    cells = {("b", "t"): [row(False, False), row(False, False), row(True, True)]}
    assert report.unedited_cells(cells) == [("b", "t", 2, 3)]


def test_a_failure_that_edited_the_source_is_a_model_failure():
    cells = {("b", "t"): [row(False, False), row(False, True), row(False, False)]}
    assert report.unedited_cells(cells) == []


def test_a_cell_with_no_failures_is_not_flagged():
    """passed == edited on every trial of an all-pass cell is not plumbing."""
    cells = {("b", "t"): [row(True, True), row(True, True)]}
    assert report.unedited_cells(cells) == []


def test_rows_before_the_field_are_not_judged():
    cells = {("b", "t"): [row(False, None), row(False, None)]}
    assert report.unedited_cells(cells) == []


def test_one_row_without_the_field_does_not_hide_the_rest():
    cells = {("b", "t"): [row(False, None), row(False, False), row(False, False)]}
    assert report.unedited_cells(cells) == [("b", "t", 2, 2)]


def test_an_excluded_row_is_not_counted():
    bad = row(False, True) | {"excluded": True, "exclusion_reason": "void"}
    cells = {("b", "t"): [row(False, False), bad]}
    assert report.unedited_cells(cells) == [("b", "t", 1, 1)]
