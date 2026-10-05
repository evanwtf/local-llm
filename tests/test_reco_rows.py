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


def test_replay_trials_stay_out_of_the_excision_figures():
    """Review of b7a366b, finding 4. A replay trial (#714) runs 5-20x as long
    as an excision; gen_tables gives replays their own table and keeps them out
    of the ordinary one. Pooled here, one 1,000 s replay moved the median from
    40 s to 520 s and the turns from 4 to 22."""
    replay = _r("replay-gmail-archive-01", True, 1000.0, 40)
    rows = [_r("mbox-scan", True, 40.0, 4), replay]
    got = reco_rows.row(rows, "b")
    assert got["median_s"] == 40.0
    assert got["worst_s"] == 40.0
    assert got["turns"] == 4
    assert got["passed"] == 1 and got["trials"] == 1


def test_a_replay_is_known_by_its_task_kind_too():
    replay = dict(_r("rebuild-x", True, 1000.0, 40), task_kind="replay")
    got = reco_rows.row([_r("mbox-scan", True, 40.0, 4), replay], "b")
    assert got["trials"] == 1 and got["median_s"] == 40.0


def test_a_guard_failure_is_not_a_pass():
    """reco_rows quotes what gen_tables prints, so it judges the same way:
    results.verdict(), never the raw `passed` (review)."""
    rows = [
        _r("mbox-scan", True, 40.0, 10),
        _r("parser-date", True, 5.0, 2) | {"touched_tests": True},
    ]
    got = reco_rows.row(rows, "b")
    assert got["passed"] == 1 and got["trials"] == 2
    assert got["median_s"] == 40.0 and got["worst_s"] == 40.0


# --- Replay and hard-replay rows, labeled apart (owner decision on #949) ---
# A backend measured only on replay tasks printed nothing at all once replays
# left the excision row. It now gets its own labeled row, split the way
# gen_tables splits its tables, by gen_tables' own predicates.


def _replay(task, passed, wall, turns, hard=False, backend="b"):
    row = _r(task, passed, wall, turns, backend=backend)
    row["task_kind"] = "replay"
    if hard:
        row["replay"] = {"suite": "hard"}
    return row


def _cells(lines):
    """Table body rows as lists of stripped cells, header and rule dropped."""
    return [[c.strip() for c in x.strip("|").split("|")] for x in lines[2:]]


def test_a_replay_only_backend_gets_a_labeled_replay_row():
    rows = [
        _replay("replay-a", True, 300.0, 20),
        _replay("replay-b", True, 500.0, 30),
    ]
    lines = reco_rows.render(rows, ["b"])
    assert lines[0] == "| backend | tasks | pass | median | worst | turns |"
    assert _cells(lines) == [["b", "replay", "2/2", "400.0 s", "500.0 s", "25.0"]]


def test_a_backend_with_both_gets_an_ordinary_row_and_a_replay_row():
    rows = [
        _r("mbox-scan", True, 40.0, 4),
        _replay("replay-a", True, 1000.0, 40),
    ]
    assert _cells(reco_rows.render(rows, ["b"])) == [
        ["b", "excision", "1/1", "40.0 s", "40.0 s", "4"],
        ["b", "replay", "1/1", "1000.0 s", "1000.0 s", "40"],
    ]


def test_hard_replay_is_its_own_row():
    rows = [
        _replay("replay-a", True, 300.0, 20),
        _replay("replay-hard-a", False, 900.0, 60, hard=True),
        _replay("replay-hard-b", True, 700.0, 50, hard=True),
    ]
    assert _cells(reco_rows.render(rows, ["b"])) == [
        ["b", "replay", "1/1", "300.0 s", "300.0 s", "20"],
        ["b", "hard replay", "1/2", "700.0 s", "700.0 s", "50"],
    ]


def test_the_split_is_gen_tables_own():
    """One classifier, so reco_rows and the generated tables cannot disagree."""
    import gen_tables

    for row in (
        _r("mbox-scan", True, 1.0, 1),
        _replay("replay-a", True, 1.0, 1),
        _replay("replay-h", True, 1.0, 1, hard=True),
        dict(_r("replay-by-name", True, 1.0, 1)),
    ):
        want = (
            "hard replay"
            if gen_tables.is_hard_replay(row)
            else "replay"
            if gen_tables.is_replay(row)
            else "excision"
        )
        assert reco_rows.task_set(row) == want


def test_an_excision_only_ledger_prints_what_it_printed_before():
    """No replay rows, no tasks column: pasted rows keep their shape."""
    lines = reco_rows.render([_r("mbox-scan", True, 40.0, 4)], ["b"])
    assert lines[0] == "| backend | pass | median | worst | turns |"
    assert _cells(lines) == [["b", "1/1", "40.0 s", "40.0 s", "4"]]
