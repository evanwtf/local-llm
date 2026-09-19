"""A RECOMMENDATIONS row's numbers, computed the way the generated tables are. #524"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import reco_rows


def _r(task, passed, wall, turns, backend="b"):
    return {
        "backend": backend,
        "task": task,
        "passed": passed,
        "wall_seconds": wall,
        "num_turns": turns,
    }


def test_timing_counts_only_passing_excision_trials():
    rows = [
        _r("mbox-scan", True, 40.0, 10),
        _r("parser-date", True, 60.0, 12),
        _r(
            "mbox-strip-envelope", False, 5.0, 2
        ),  # failed fast: must not lower the median
        _r("script-reverse", True, 10.0, 3),  # script task: excluded from timing
    ]
    got = reco_rows.row(rows, "b")
    assert got["passed"] == 3 and got["trials"] == 4
    assert got["median_s"] == 50.0
    assert got["worst_s"] == 60.0
    assert got["turns"] == 11


def test_other_backends_are_ignored():
    rows = [
        _r("mbox-scan", True, 40.0, 10),
        _r("mbox-scan", True, 999.0, 99, backend="other"),
    ]
    got = reco_rows.row(rows, "b")
    assert got["trials"] == 1 and got["worst_s"] == 40.0


def test_a_backend_with_no_timed_trial_reports_none():
    got = reco_rows.row([_r("mbox-scan", False, 30.0, 5)], "b")
    assert got["median_s"] is None and got["turns"] is None
    assert got["passed"] == 0 and got["trials"] == 1
