"""#112: a cohort's pass count must come from the oracle, not from a raw key."""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import cohort_split

MOMENT = dt.datetime.fromisoformat("2026-09-03T04:23:09-04:00")


def row(started: str, passed: bool = True, **extra):
    return {"started": started, "passed": passed, **extra}


def test_a_row_that_edited_the_tests_is_not_a_pass():
    """`passed: true` with a tripped guard is not a pass. Reading the raw key
    is how a 13/16 backend reached a published table as 13/13."""
    got = cohort_split.describe(
        "X", [row("2026-09-03T05:00:00", touched_tests=["tests/test_a.py"])]
    )
    assert "0/1" in got[0]


def test_a_clean_pass_counts():
    assert "1/1" in cohort_split.describe("X", [row("2026-09-03T05:00:00")])[0]


def test_rows_are_split_at_the_moment():
    before, after, undated = cohort_split.split(
        [row("2026-09-03T04:00:00"), row("2026-09-03T05:00:00")], MOMENT
    )
    assert len(before) == 1 and len(after) == 1 and undated == []


def test_a_row_with_no_readable_start_is_reported_not_assigned():
    """Silently filing an undated row on one side would invent evidence."""
    before, after, undated = cohort_split.split([{"passed": True}], MOMENT)
    assert (before, after) == ([], [])
    assert len(undated) == 1


def test_a_confound_that_varies_across_the_half_is_flagged():
    got = "\n".join(
        cohort_split.describe(
            "X",
            [
                row("2026-09-03T05:00:00", client_version="1.18.25"),
                row("2026-09-03T05:00:00", client_version="1.18.27"),
            ],
        )
    )
    assert "SPLIT" in got
