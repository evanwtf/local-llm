"""The #112 A/B read-out must not be able to flatter itself.

Every test here is a way the report could have produced a confident wrong
answer: a p-value overriding the pre-registered bar, a favourable result
reported in the wrong direction, or trials attributed to the arm that did not
run them.
"""

from __future__ import annotations

import datetime
import json
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


# --- #175: the batch id -----------------------------------------------------
#
# The manifest can hold more than one batch, and a read-out that does not say
# which one it wants pools them -- this morning's rows into tonight's totals,
# with no error. The guard must live in the tool, not in whoever runs it
# remembering to pass a flag. The fixture is tonight's real manifest, which
# spans two batches (0906-0743 this morning, 0906-1716 tonight).

TWO_BATCH_MANIFEST = [
    {
        "run": 1,
        "arm": "legacy",
        "started": "2026-09-06T11:43:33Z",
        "ended": "2026-09-06T12:15:52Z",
        "dir": "/tmp/146-targets-legacy-0906-0743-run1",
    },
    {
        "run": 2,
        "arm": "sandbox",
        "started": "2026-09-06T12:16:00Z",
        "ended": "2026-09-06T13:21:10Z",
        "dir": "/tmp/146-targets-sandbox-0906-0743-run2",
    },
    {
        "run": 1,
        "arm": "legacy",
        "started": "2026-09-06T21:16:36Z",
        "ended": "2026-09-06T22:02:36Z",
        "dir": "/tmp/146-targets-legacy-0906-1716-run1",
    },
    {
        "run": 2,
        "arm": "sandbox",
        "started": "2026-09-06T22:02:51Z",
        "ended": "2026-09-06T22:58:39Z",
        "dir": "/tmp/146-targets-sandbox-0906-1716-run2",
    },
]


def test_batch_of_reads_the_batch_out_of_the_dir():
    assert report.batch_of(TWO_BATCH_MANIFEST[0]) == "0906-0743"
    assert report.batch_of(TWO_BATCH_MANIFEST[2]) == "0906-1716"
    assert report.batch_of({"dir": "/tmp/a"}) is None


def test_a_two_batch_manifest_refuses_without_a_batch(tmp_path, caplog):
    """A manifest spanning two batches must refuse, naming both, rather than
    pool them. This morning's contaminated rows would otherwise pool into
    tonight's totals with no error."""
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(e) for e in TWO_BATCH_MANIFEST) + "\n")
    results = tmp_path / "results.jsonl"
    results.write_text("")
    with caplog.at_level(logging.INFO):
        rc = report.main(["--results", str(results), "--manifest", str(manifest)])
    assert rc != 0
    assert "0906-0743" in caplog.text
    assert "0906-1716" in caplog.text
    assert "A/B read-out" not in caplog.text


def test_a_batch_argument_reads_out_only_that_batch():
    """With --batch, only that batch's rows are counted; the other batch's
    rows are not pooled."""
    rows = [
        {"started": "2026-09-06T21:30:00Z", "passed": True, "batch": "0906-1716"},
        {"started": "2026-09-06T22:30:00Z", "passed": True, "batch": "0906-1716"},
        {"started": "2026-09-06T12:00:00Z", "passed": True, "batch": "0906-0743"},
    ]
    filtered = [e for e in TWO_BATCH_MANIFEST if report.batch_of(e) == "0906-1716"]
    out, unmapped = report.outcomes(rows, filtered, batch="0906-1716")
    assert out["legacy"]["trials"] == 1
    assert out["sandbox"]["trials"] == 1
    assert unmapped == 0


def test_a_row_without_a_batch_field_maps_by_window():
    """Old rows carry no batch field. They must still map by time window
    against the batch's manifest, so old data keeps working."""
    rows = [{"started": "2026-09-06T21:30:00Z", "passed": True}]
    filtered = [e for e in TWO_BATCH_MANIFEST if report.batch_of(e) == "0906-1716"]
    out, unmapped = report.outcomes(rows, filtered, batch="0906-1716")
    assert out["legacy"]["trials"] == 1
    assert unmapped == 0


def test_a_row_from_another_batch_without_a_batch_field_is_not_pooled():
    """A row from this morning with no batch field must not pool into
    tonight's totals: against the filtered manifest it falls outside every
    window and is counted unmapped, not attributed to an arm."""
    rows = [{"started": "2026-09-06T12:00:00Z", "passed": True}]
    filtered = [e for e in TWO_BATCH_MANIFEST if report.batch_of(e) == "0906-1716"]
    out, unmapped = report.outcomes(rows, filtered, batch="0906-1716")
    assert out == {}
    assert unmapped == 1


def test_a_batch_that_matches_nothing_is_refused(tmp_path, caplog):
    """A --batch that matches no run must refuse, not read out empty. An
    empty read-out reads as 'no failures', which is the wrong kind of
    silence."""
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(e) for e in TWO_BATCH_MANIFEST) + "\n")
    results = tmp_path / "results.jsonl"
    results.write_text("")
    with caplog.at_level(logging.INFO):
        rc = report.main(
            [
                "--results",
                str(results),
                "--manifest",
                str(manifest),
                "--batch",
                "9999-9999",
            ]
        )
    assert rc != 0
    assert "9999-9999" in caplog.text
    assert "A/B read-out" not in caplog.text


def test_an_unparseable_entry_counts_as_its_own_batch(tmp_path, caplog):
    """A manifest with one known batch plus an entry with no batch id must
    refuse without --batch: 'one known batch plus something unidentifiable'
    is exactly a case where pooling is possible and nobody is warned."""
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        '{"run":1,"arm":"legacy","started":"2026-09-06T21:16:36Z",'
        '"ended":"2026-09-06T22:02:36Z",'
        '"dir":"/tmp/146-targets-legacy-0906-1716-run1"}\n'
        '{"run":2,"arm":"sandbox","started":"2026-09-06T22:02:51Z",'
        '"ended":"2026-09-06T22:58:39Z","dir":"/tmp/old-no-batch"}\n'
    )
    results = tmp_path / "results.jsonl"
    results.write_text("")
    with caplog.at_level(logging.INFO):
        rc = report.main(["--results", str(results), "--manifest", str(manifest)])
    assert rc != 0
    assert "0906-1716" in caplog.text
    assert "A/B read-out" not in caplog.text


def test_a_batch_argument_excludes_entries_with_no_batch_id(tmp_path, caplog):
    """With --batch, a manifest entry carrying no batch id is excluded, and
    the exclusion is logged rather than silent."""
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        '{"run":1,"arm":"legacy","started":"2026-09-06T21:16:36Z",'
        '"ended":"2026-09-06T22:02:36Z",'
        '"dir":"/tmp/146-targets-legacy-0906-1716-run1"}\n'
        '{"run":2,"arm":"sandbox","started":"2026-09-06T22:02:51Z",'
        '"ended":"2026-09-06T22:58:39Z","dir":"/tmp/old-no-batch"}\n'
    )
    results = tmp_path / "results.jsonl"
    results.write_text("")
    with caplog.at_level(logging.INFO):
        rc = report.main(
            [
                "--results",
                str(results),
                "--manifest",
                str(manifest),
                "--batch",
                "0906-1716",
            ]
        )
    assert rc == 0
    assert "no batch id" in caplog.text


# --- per-sweep read-out (#146) -------------------------------------------
#
# #146 pre-registered its decision per sweep, not per arm: "within 1 task per
# sweep of 15, across 2 sweeps per arm", with a "no call" branch when the arms
# differ by more than that BUT IN OPPOSITE DIRECTIONS across sweeps.
#
# The batch on 2026-09-07 is why this matters. Arm totals read legacy 29/30
# against sandbox 26/30 -- a clean 3-task deficit, which on the arm view alone
# reads as "do not cut over". Per sweep it was 14/15, 15/15, 11/15, 15/15: the
# arms disagree in direction (-1, +4) and the rule's own answer is "no call".
# An arm total cannot express that, and reading one would have produced a
# pre-registered verdict the pre-registration did not support.

SWEEP_MANIFEST = [
    {
        "run": 1,
        "arm": "legacy",
        "started": "2026-09-06T22:07:15",
        "ended": "2026-09-06T22:45:27",
    },
    {
        "run": 2,
        "arm": "sandbox",
        "started": "2026-09-06T22:45:41",
        "ended": "2026-09-06T23:43:47",
    },
]


def _row(started: str, passed: bool) -> dict:
    return {"started": started, "passed": passed, "batch": "b1"}


def test_per_sweep_splits_by_run_not_only_by_arm():
    rows = [
        _row("2026-09-06T22:10:00", True),
        _row("2026-09-06T22:20:00", False),
        _row("2026-09-06T22:50:00", True),
    ]
    got = report.per_sweep(rows, SWEEP_MANIFEST, batch="b1")
    assert got == [(1, "legacy", 1, 2), (2, "sandbox", 1, 1)]


def test_per_sweep_is_ordered_by_run():
    """The rule pairs sweeps by position, so order is load-bearing."""
    rows = [_row("2026-09-06T22:50:00", True), _row("2026-09-06T22:10:00", True)]
    assert [r[0] for r in report.per_sweep(rows, SWEEP_MANIFEST, batch="b1")] == [1, 2]


def test_per_sweep_skips_another_batch():
    rows = [_row("2026-09-06T22:10:00", True) | {"batch": "other"}]
    assert report.per_sweep(rows, SWEEP_MANIFEST, batch="b1") == []


def test_per_sweep_ignores_a_row_outside_every_window():
    rows = [_row("2026-09-06T21:00:00", True)]
    assert report.per_sweep(rows, SWEEP_MANIFEST, batch="b1") == []


def test_render_names_the_no_call_branch_when_sweeps_disagree():
    """Disagreeing sweeps must be called out, or an arm total gets read instead."""
    sweeps = [
        (1, "legacy", 14, 15),
        (2, "sandbox", 15, 15),
        (3, "sandbox", 11, 15),
        (4, "legacy", 15, 15),
    ]
    out = report.render({}, {}, sweeps)
    assert "DISAGREE IN DIRECTION" in out
    assert "no call" in out


def test_render_is_quiet_when_the_sweeps_agree():
    """A guard that always fires is not a guard."""
    sweeps = [
        (1, "legacy", 15, 15),
        (2, "sandbox", 12, 15),
        (3, "sandbox", 11, 15),
        (4, "legacy", 14, 15),
    ]
    out = report.render({}, {}, sweeps)
    assert "DISAGREE IN DIRECTION" not in out


def test_the_primary_label_is_not_claimed_for_other_experiments():
    """ "the pre-registered primary" is #112's claim, for its on/off arms."""
    per_arm = {"legacy": (1, 10, 1, 10), "sandbox": (1, 10, 1, 10)}
    assert "pre-registered primary" not in report.render(per_arm, {})
    on_off = {"on": (1, 10, 1, 10), "off": (1, 10, 1, 10)}
    assert "pre-registered primary" in report.render(on_off, {})


def test_render_refuses_to_pair_sweeps_when_the_counts_differ():
    """The shape the real data actually hits, and it printed nothing.

    Batch 0906-1716 has legacy 1 sweep and sandbox 2 -- one legacy run was
    voided. `len(av) == len(bv)` was false, so the whole pairing block was
    skipped: no difference line, no no-call line, no warning. A reader saw the
    per-sweep table, saw no comparison under it, and had nothing to fall back
    on but the arm totals -- the pooled statistic #146 pre-registered against.
    Silence there is the same bug the per-sweep read-out exists to fix.
    """
    sweeps = [(1, "legacy", 14, 15), (2, "sandbox", 13, 15), (3, "sandbox", 12, 15)]
    out = report.render({}, {}, sweeps)
    assert "NOT COMPUTED" in out
    assert "legacy has 1 sweep(s), sandbox has 2" in out
    assert "Do NOT read the arm totals" in out


def test_render_refuses_to_pair_when_only_one_arm_ran():
    """One arm and no counterpart is not a comparison either."""
    sweeps = [(1, "legacy", 14, 15), (2, "legacy", 15, 15)]
    out = report.render({}, {}, sweeps)
    assert "NOT COMPUTED" in out
    assert "nothing to pair against" in out


def test_equal_sweep_counts_still_compute_a_difference():
    """The refusal must not swallow the case it was added beside."""
    sweeps = [(1, "legacy", 14, 15), (2, "sandbox", 15, 15)]
    out = report.render({}, {}, sweeps)
    assert "NOT COMPUTED" not in out
    assert "paired by position: -1" in out
