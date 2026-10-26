"""report.window() brackets rows by `started`, so one build of a force-pushed
backend can be isolated (#328).

A ds4 fork branch is force-pushed, so the built commit changes under a fixed
backend name. The two cohorts land at different times, and `--since`/`--until`
are how report.py separates them. `--since` alone (a floor) could not exclude a
later cohort; these pin the window's two edges and their inclusivity.
"""

from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
sys.path.insert(0, str(HERE / "benchmarks" / "agent"))

import report


def _rows():
    return [
        {"started": "2026-09-11T20:02:40-0400", "tag": "old"},
        {"started": "2026-09-11T21:16:56-0400", "tag": "old"},
        {"started": "2026-09-14T01:14:01-0400", "tag": "new"},
        {"started": "2026-09-14T02:22:49-0400", "tag": "new"},
    ]


def test_no_bounds_keeps_everything() -> None:
    assert len(report.window(_rows())) == 4


def test_since_is_exclusive_floor() -> None:
    kept = report.window(_rows(), since="2026-09-14T00:00:00-0400")
    assert [r["tag"] for r in kept] == ["new", "new"]


def test_until_is_inclusive_ceiling() -> None:
    """The last old row starts exactly at the bound and must be kept."""
    kept = report.window(_rows(), until="2026-09-11T21:16:56-0400")
    assert [r["tag"] for r in kept] == ["old", "old"]


def test_since_and_until_bracket_one_cohort() -> None:
    kept = report.window(
        _rows(),
        since="2026-09-11T19:00:00-0400",
        until="2026-09-11T21:30:00-0400",
    )
    assert [r["tag"] for r in kept] == ["old", "old"]


def test_row_without_started_sorts_as_empty_string() -> None:
    """A malformed row (no `started`, sorts as "") is dropped by --since but
    kept by --until; real rows always carry `started`."""
    rows = [{"tag": "nostart"}, {"started": "2026-09-14T01:14:01-0400", "tag": "new"}]
    since = report.window(rows, since="2026-09-01T00:00:00-0400")
    assert [r["tag"] for r in since] == ["new"]
    until = report.window(rows, until="2026-09-30T00:00:00-0400")
    assert [r["tag"] for r in until] == ["nostart", "new"]


def test_bounds_compare_instants_not_strings() -> None:
    """#1259: the i9 client writes `started` in UTC. 23:00:24+0000 is 19:00
    EDT, before a 22:10 EDT floor, though it sorts after it as a string."""
    rows = [
        {"started": "2026-10-25T23:00:24+0000", "tag": "before"},
        {"started": "2026-10-26T02:11:34+0000", "tag": "after"},
    ]
    since = report.window(rows, since="2026-10-25T22:10:00-04:00")
    assert [r["tag"] for r in since] == ["after"]
    until = report.window(rows, until="2026-10-25T22:10:00-0400")
    assert [r["tag"] for r in until] == ["before"]
