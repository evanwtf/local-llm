"""#714: the replay-task checker reads pytest's summary into the right counts."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import verify_replay_tasks as verify


def test_counts_reads_the_closing_summary():
    out = "tests/test_x.py ....F\n1 failed, 4 passed, 2 skipped in 0.31s\n"
    assert verify.counts(out) == {"passed": 4, "failed": 1, "skipped": 2, "errors": 0}


def test_counts_reads_a_collection_error():
    """A test file that imports a module the revert removed errors at
    collection; that is a failing control, and it must read as one."""
    assert verify.counts("ERROR tests/test_x.py\n1 error in 0.20s\n")["errors"] == 1
    assert verify.counts("2 errors in 0.2s")["errors"] == 2


def test_counts_ignores_an_earlier_line_that_looks_like_a_summary():
    out = "the fixture says 3 passed\n5 passed in 0.1s\n"
    assert verify.counts(out)["passed"] == 5


def test_a_row_shows_run_and_skip_counts_and_the_verdict():
    got = {
        "task": "replay-x",
        "commit": "0ce03e6",
        "added": 31,
        "removed": 6,
        "reverted": 1,
        "deleted": 0,
        "at_commit": {"passed": 19, "failed": 0, "skipped": 2, "errors": 0},
        "after": {"passed": 16, "failed": 3, "skipped": 2, "errors": 0},
        "valid": True,
        "why": "",
    }
    line = verify.row(got)
    assert "| 19 run / 2 skip |" in line
    assert "16 passed, 3 failed, 0 errors" in line
    assert line.endswith("| ok |")


def test_an_invalid_definition_is_reported_not_raised():
    line = verify.row({"task": "replay-x", "valid": False, "why": "no revert"})
    assert "INVALID: no revert" in line
