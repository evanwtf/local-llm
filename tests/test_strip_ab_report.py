"""The #112 A/B read-out must not be able to flatter itself.

Every test here is a way the report could have produced a confident wrong
answer: a p-value overriding the pre-registered bar, a favourable result
reported in the wrong direction, or trials attributed to the arm that did not
run them.
"""

from __future__ import annotations

import datetime
import logging
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import strip_ab_report as report


def test_a_table_with_no_difference_is_p_one():
    assert report.fisher_exact(5, 5, 5, 5) == 1.0


def test_a_perfectly_separated_table_is_the_smallest_possible_p():
    # Only the two extreme arrangements are as unlikely as this one, and each
    # has probability 1/C(20,10).
    assert report.fisher_exact(10, 0, 0, 10) == 2 / math.comb(20, 10)


def test_the_test_does_not_care_which_arm_is_written_first():
    assert report.fisher_exact(3, 17, 11, 9) == report.fisher_exact(11, 9, 3, 17)


def test_the_pre_registered_bar_beats_a_tempting_p_value():
    """The whole point of pre-registering a bar is that it holds on the night
    the numbers look good. 29 failures with p=0.001 is still 'could not
    tell'."""
    assert report.verdict(on_f=29, off_f=40, p=0.001).startswith("COULD NOT TELL")


def test_a_result_in_the_wrong_direction_is_not_narrated_as_a_finding():
    assert report.verdict(on_f=40, off_f=40, p=0.001).startswith("UNEXPECTED")


def test_a_clear_result_reads_as_the_pre_registered_sentence():
    assert report.verdict(on_f=40, off_f=60, p=0.001).startswith("WORKS")


def test_no_difference_reads_as_measured_no_effect():
    assert report.verdict(on_f=40, off_f=40, p=0.9).startswith("DOES NOTHING")


MANIFEST = [
    {
        "run": 1,
        "arm": "on",
        "started": "2026-09-06T07:00:00Z",
        "ended": "2026-09-06T07:30:00Z",
        "dir": "/tmp/a",
    },
    {
        "run": 2,
        "arm": "off",
        "started": "2026-09-06T07:31:00Z",
        "ended": "2026-09-06T08:00:00Z",
        "dir": "/tmp/b",
    },
]


def test_a_trial_belongs_to_the_arm_that_was_running_when_it_started():
    assert report.arm_of("2026-09-06T07:10:00Z", MANIFEST) == "on"
    assert report.arm_of("2026-09-06T07:45:00Z", MANIFEST) == "off"


def test_the_two_clocks_in_this_batch_are_compared_as_instants():
    """The bug this caught on the first read-out.

    run.py stamps a row with naive local time and the driver stamps the
    manifest in UTC. Compared as strings, 118 of 120 rows fell outside every
    window and the outcome table read `2/2 = 100%`.
    """
    local = (
        datetime.datetime(2026, 9, 6, 7, 10, tzinfo=datetime.UTC)
        .astimezone()
        .replace(tzinfo=None)
        .isoformat()
    )
    assert report.arm_of(local, MANIFEST) == "on"


def test_an_unparseable_stamp_is_unmapped_rather_than_guessed():
    assert report.arm_of("yesterday", MANIFEST) is None


def test_a_trial_outside_every_run_window_is_not_guessed_at():
    assert report.arm_of("2026-09-06T09:99:00Z", MANIFEST) is None
    assert report.arm_of("", MANIFEST) is None


def test_a_short_run_does_not_shift_every_later_row_into_the_wrong_arm():
    """The reason arm_of reads the clock and not a row count.

    A run that dies after two trials writes two rows. Attributing rows to arms
    positionally -- fifteen at a time -- would put the rest of that arm's rows
    in the other arm, which reverses the result rather than weakening it.
    """
    rows = [
        {"started": "2026-09-06T07:05:00Z", "passed": True},
        {"started": "2026-09-06T07:06:00Z", "passed": False, "solution_empty": True},
        {"started": "2026-09-06T07:40:00Z", "passed": False, "solution_empty": True},
        {"started": "2026-09-06T07:41:00Z", "passed": True},
        {"started": "2026-09-06T07:42:00Z", "passed": True},
    ]
    out, unmapped = report.outcomes(rows, MANIFEST)
    assert out["on"] == {"trials": 2, "passed": 1, "empty": 1}
    assert out["off"] == {"trials": 3, "passed": 2, "empty": 1}
    assert unmapped == 0


def test_a_row_belonging_to_no_run_is_counted_rather_than_dropped():
    """A row here that no run produced means the results file has picked up
    something else. Silently skipping it is how a batch gets read as clean."""
    rows = [{"started": "2026-09-05T01:00:00Z", "passed": True}]
    out, unmapped = report.outcomes(rows, MANIFEST)
    assert out == {}
    assert unmapped == 1


def test_another_experiments_arms_are_not_renamed_on_and_off():
    """#146 runs the same shape with arms called legacy and sandbox."""
    assert report.arms_in({"sandbox": 1, "legacy": 2}) == ["legacy", "sandbox"]
    assert report.arms_in({"off": 1}, {"on": 2}) == ["on", "off"]


def test_the_112_verdict_is_not_printed_over_another_experiment():
    """Those sentences are #112's pre-registration, not a general rule."""
    per_arm = {"legacy": (2, 100, 1, 20), "sandbox": (3, 100, 4, 20)}
    outcome = {
        "legacy": {"trials": 15, "passed": 14, "empty": 1},
        "sandbox": {"trials": 15, "passed": 13, "empty": 2},
    }
    text = report.render(per_arm, outcome)
    assert "Fisher exact" in text
    assert "COULD NOT TELL" not in text
    assert "DOES NOTHING" not in text


def test_the_conditional_is_read_for_whatever_arms_the_manifest_names():
    """An empty table under a heading reads as "no failures", not as "wrong
    arm names". #146's first read-out printed exactly that."""
    assert report.arms_in({e["arm"]: None for e in MANIFEST}) == ["on", "off"]
    targets = [{"arm": "sandbox"}, {"arm": "legacy"}]
    assert report.arms_in({e["arm"]: None for e in targets}) == ["legacy", "sandbox"]


def test_void_is_never_an_arm_name():
    """VOID is the marker for a batch cut short, not an arm. A read-out that
    treated it as an arm would pool a partial batch."""
    assert report.arms_in({"VOID": 1, "legacy": 2}) == ["legacy"]
    assert report.arms_in({"VOID": 1}) == []


def test_a_voided_batch_refuses_the_read_out(tmp_path, caplog):
    """A VOID row must refuse the whole read-out, name the reason and run
    number, and render no per-arm table. A bare KeyError would read as a
    corrupt manifest instead of the reason the writer recorded."""
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        '{"run":1,"arm":"legacy","started":"2026-09-06T07:00:00Z",'
        '"ended":"2026-09-06T07:30:00Z","dir":"/tmp/a"}\n'
        '{"run":3,"arm":"VOID","reason":"past-until","until":"0000"}\n'
    )
    results = tmp_path / "results.jsonl"
    results.write_text("")
    with caplog.at_level(logging.INFO):
        rc = report.main(["--results", str(results), "--manifest", str(manifest)])
    assert rc != 0
    assert "past-until" in caplog.text
    assert "run 3" in caplog.text
    assert "A/B read-out" not in caplog.text
