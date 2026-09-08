"""Synthetic read-outs for the #138 stack A/B report (scripts/stack_agent_report.py).

Every fixture is built here -- none of them read tonight's ledger, which
does not exist at test time and must never become a fixture.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import stack_agent_report as sar

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "stack_agent_ab.sh"


def producer_fmt(pattern: str) -> str:
    """The date format stack_agent_ab.sh itself writes, pulled out of the
    script. Fixtures are built from this so the reader can never drift from
    the producer again -- the first fixture hand-wrote a space where the
    script writes a T, and the report then exited 2 on the real run dir."""
    found = re.search(pattern, SCRIPT.read_text())
    assert found, f"stack_agent_ab.sh no longer matches {pattern!r}"
    return found.group(1)


def producer_started_line(when: dt.datetime) -> str:
    return f"# stack agent A/B, started {when.strftime(producer_fmt(r"started \$\(date '([^']+)'\)"))}".rstrip()


def producer_sweep_line(
    tag: str, start: dt.datetime, finish: dt.datetime | None = None
) -> str:
    """`tag start finish`, the shape stack_agent_ab.sh writes since 2026-09-05.

    It wrote one time before that, appended AFTER the sweep, which the reader
    took for the sweep's START -- so every window held the next sweep's rows.
    Use producer_legacy_sweep_line to build a pre-change directory.
    """
    fmt = producer_fmt(r"\$tag \$started \$\(date '([^']+)'\)\" >> ")
    end = finish if finish is not None else start + dt.timedelta(minutes=40)
    return f"{tag} {start.strftime(fmt)} {end.strftime(fmt)}"


def producer_legacy_sweep_line(tag: str, finish: dt.datetime) -> str:
    """The pre-2026-09-05 shape: one time, written when the sweep ENDED."""
    return f"{tag} {finish.strftime('%H:%M:%S')}"


TASKS = [f"task-{i:02d}" for i in range(15)]
#: Sweep order as stack_agent_ab.sh writes it: new, old, new, old.
ORDER = [
    ("new-sweep1", "20:58:00"),
    ("old-sweep1", "21:40:00"),
    ("new-sweep2", "22:22:00"),
    ("old-sweep2", "23:00:00"),
]
RUN_DATE = "2026-09-04"


def row(backend: str, task: str, started: str, **extra) -> dict:
    row = {
        "backend": backend,
        "task": task,
        "started": started,
        "passed": True,
        "solution_empty": False,
        "num_turns": 8,
        "wall_seconds": 120,
        "client_version": "1.18.27",
        "env": {"harness_head": "abc1234", "harness_dirty": False},
    }
    row.update(extra)
    return row


STARTS = [
    dt.datetime(2026, 9, 4, 20, 58, 0),
    dt.datetime(2026, 9, 4, 21, 40, 0),
    dt.datetime(2026, 9, 4, 22, 22, 0),
    dt.datetime(2026, 9, 4, 23, 0, 0),
]


def full_rows(offset: dt.timedelta = dt.timedelta(0)) -> list[dict]:
    """60 rows: 15 tasks x 4 sweeps, every trial a clean pass. `offset`
    shifts every sweep (and its rows) as a relaunch would."""
    rows = []
    for (tag, _), base in zip(ORDER, STARTS):
        backend = "qwen38fnds4kimat" if arm_of(tag) == "new" else "qwen38fnds4shim"
        for i, task in enumerate(TASKS):
            when = base + offset + dt.timedelta(seconds=i)
            rows.append(row(backend, task, f"2026-09-04T{when:%H:%M:%S}-04:00"))
    return rows


def arm_of(tag: str) -> str:
    return tag.rsplit("-sweep", 1)[0]


def sweep_order_lines(offset: dt.timedelta = dt.timedelta(0)) -> list[str]:
    """Each sweep's finish comes from the next sweep's start, minus a minute.

    The producer appends a sweep's finish when it completes, then restarts the
    server, then starts the next sweep -- so finish_i < start_{i+1} is an
    invariant of a real run. Deriving the finish from the next start keeps the
    fixture inside that invariant when STARTS is edited; a magic constant would
    drift back into an impossible overlap. The last sweep has no next start, so
    it gets a fixed duration.
    """
    lines = []
    for i, ((tag, _), base) in enumerate(zip(ORDER, STARTS)):
        start = base + offset
        if i + 1 < len(STARTS):
            finish = STARTS[i + 1] + offset - dt.timedelta(minutes=1)
        else:
            finish = start + dt.timedelta(minutes=40)
        lines.append(producer_sweep_line(tag, start, finish))
    return lines


def write_run_dir(
    tmp_path: pathlib.Path,
    started_at: dt.datetime | None = None,
    offset: dt.timedelta = dt.timedelta(0),
) -> pathlib.Path:
    run_dir = tmp_path / "138-stack-ab"
    run_dir.mkdir()
    if started_at is None:
        started_at = dt.datetime(2026, 9, 4, 20, 57, 17)
    (run_dir / "run-record.txt").write_text(
        producer_started_line(started_at) + "\n"
        "NEW backend=qwen38fnds4kimat engine=~/git/ds4-ivan-qwen38fn @ bd9cfbc\n"
        "OLD backend=qwen38fnds4shim engine=~/git/ds4-metal @ ba01f5d\n"
    )
    (run_dir / "sweep-order.txt").write_text(
        "".join(line + "\n" for line in sweep_order_lines(offset))
    )
    for tag, _ in ORDER:
        (run_dir / f"server-{tag}.log").write_text("ready\n")
    return run_dir


def write_ledger(tmp_path: pathlib.Path, rows: list[dict]) -> pathlib.Path:
    ledger = tmp_path / "results.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return ledger


def run_report(
    tmp_path: pathlib.Path,
    rows: list[dict],
    caplog,
    started_at: dt.datetime | None = None,
    offset: dt.timedelta = dt.timedelta(0),
):
    ledger = write_ledger(tmp_path, rows)
    run_dir = write_run_dir(tmp_path, started_at, offset)
    caplog.set_level(logging.INFO, logger="stack_agent_report")
    code = sar.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
    return code, caplog.text


def test_the_raw_line_prints_before_any_filter(tmp_path, caplog):
    """Raw counts first: 60 rows, 30 per backend, exclusions visible."""
    code, out = run_report(tmp_path, full_rows(), caplog)
    assert code == 0
    assert "raw rows: 60 (new 30, old 30); excluded 0; dry 0" in out


def test_an_excluded_row_is_a_visible_hole(tmp_path, caplog):
    """An excluded row is a hole in n, not a pass or fail: it shows in the
    raw line and shorts its sweep cell, which is VOID, not a smaller n."""
    rows = full_rows()
    rows[0]["excluded"] = True
    rows[0]["exclusion_reason"] = "smoke"
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "excluded 1" in out
    assert "has 14 rows" in out


def test_a_timeout_row_is_a_fail_not_an_absence(tmp_path, caplog):
    """A timeout writes error and no `passed` key. Reading verdicts from the
    key's presence drops the row; verdict() counts it as a failure."""
    rows = full_rows()
    del rows[0]["passed"]
    rows[0]["error"] = "timeout"
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 0
    assert "'passes': 29" in out  # new arm


def test_a_guard_flip_is_a_harness_reject_not_a_model_failure(tmp_path, caplog):
    rows = full_rows()
    rows[0]["touched_tests"] = ["tests/test_oracle.py"]
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 0
    assert "'guard_flips': 1" in out
    assert "'passes': 29" in out


def test_sweep_windows_come_from_the_order_file_not_from_gaps(tmp_path):
    """Two rows 40 minutes apart stay in the same sweep; the next sweep owns
    the window after its start line, however close a row lands to it."""
    rows = full_rows()
    run_dir = write_run_dir(tmp_path)
    sweeps = sar.sweep_windows(run_dir)
    leftover = sar.assign(rows, sweeps)
    assert leftover == []
    counts = {s.tag: len(s.rows) for s in sweeps}
    assert counts == {
        "new-sweep1": 15,
        "old-sweep1": 15,
        "new-sweep2": 15,
        "old-sweep2": 15,
    }


def test_an_overlapping_sweep_window_is_void(tmp_path, caplog):
    """A sweep finishing at or after the next one's start is impossible for
    the producer and corrupts the tally under [start, own finish]: a row in the
    overlap matches the earlier sweep first and, on a backend mismatch, is
    dropped. sweep_windows must refuse, not guess. This is the permanent record
    of the fixture that used to encode exactly this impossible state."""
    run_dir = tmp_path / "138-stack-ab"
    run_dir.mkdir()
    (run_dir / "run-record.txt").write_text(
        producer_started_line(dt.datetime(2026, 9, 4, 20, 57, 17)) + "\n"
        "NEW backend=qwen38fnds4kimat engine=x @ bd9cfbc\n"
        "OLD backend=qwen38fnds4shim engine=y @ ba01f5d\n"
    )
    # new-sweep2 finishes 23:02, old-sweep2 starts 23:00 -- the old overlap.
    (run_dir / "sweep-order.txt").write_text(
        "new-sweep1 20:58:00 21:38:00\n"
        "old-sweep1 21:40:00 22:20:00\n"
        "new-sweep2 22:22:00 23:02:00\n"
        "old-sweep2 23:00:00 23:40:00\n"
    )
    caplog.set_level(logging.INFO, logger="stack_agent_report")
    assert sar.sweep_windows(run_dir) is None
    assert (
        "new-sweep2 finishes 23:02:00 at or after old-sweep2 starts 23:00:00"
        in caplog.text
    )


def test_the_fixtures_are_built_in_the_producers_formats():
    """The first fixture hand-wrote a space where the producer writes a T,
    and the reader then exited 2 on the live run dir. From here the fixtures
    are built from stack_agent_ab.sh's own date formats, so a change to the
    producer's line shapes fails this suite and the reader is updated in the
    same commit."""
    when = dt.datetime(2026, 9, 4, 20, 57, 17)
    assert "T" in producer_started_line(when)
    assert "T" not in producer_sweep_line("new-sweep1", when)


def test_the_live_run_record_line_parses(tmp_path):
    """The exact first line the live run wrote at 20:57:17."""
    run_dir = tmp_path / "138-stack-ab"
    run_dir.mkdir()
    (run_dir / "run-record.txt").write_text(
        "# stack agent A/B, started 2026-09-04T20:57:17 EDT\n"
    )
    assert sar.run_date(run_dir) == dt.date(2026, 9, 4)


def test_the_cut_defaults_to_the_run_records_started_line(tmp_path, caplog):
    """No --cut: the cut is the record's started line, printed so the reader
    sees which scope the counts carry."""
    code, out = run_report(tmp_path, full_rows(), caplog)
    assert code == 0
    assert "cut: 2026-09-04 20:57:17 (run-record.txt)" in out


def test_rows_from_the_dead_first_run_are_out_of_scope(tmp_path, caplog):
    """The 20:57 launch died 18 minutes in; its 8 rows are excluded in the
    ledger and started 20:57-21:16. The relaunch's record line is the cut,
    so they fall out of scope: the raw line still counts one run, 60 rows,
    and the dead run's exclusions do not appear."""
    relaunch = dt.timedelta(minutes=31)  # the relaunch re-ran every sweep
    rows = full_rows(relaunch)
    for when in ("2026-09-04T20:58:00-04:00", "2026-09-04T21:10:00-04:00"):
        dead = row("qwen38fnds4kimat", "task-00", when)
        dead["excluded"] = True
        dead["exclusion_reason"] = "first launch died 18 minutes in"
        rows.append(dead)
    code, out = run_report(
        tmp_path,
        rows,
        caplog,
        started_at=dt.datetime(2026, 9, 4, 21, 27, 43),
        offset=relaunch,
    )
    assert code == 0
    assert "cut: 2026-09-04 21:27:43 (run-record.txt)" in out
    assert "raw rows: 60 (new 30, old 30); excluded 0; dry 0" in out


def test_no_run_record_and_no_cut_refuses(tmp_path, caplog):
    """Without a record line and without --cut the script refuses to guess
    which rows are tonight's."""
    run_dir = write_run_dir(tmp_path)
    (run_dir / "run-record.txt").unlink()
    rows = full_rows()
    ledger = write_ledger(tmp_path, rows)
    caplog.set_level(logging.INFO, logger="stack_agent_report")
    code = sar.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
    assert code == 2
    assert "no parseable started line" in caplog.text


def test_an_explicit_cut_override_is_honoured(tmp_path, caplog):
    rows = full_rows()
    ledger = write_ledger(tmp_path, rows)
    run_dir = write_run_dir(tmp_path)
    caplog.set_level(logging.INFO, logger="stack_agent_report")
    code = sar.main(
        [
            "--ledger",
            str(ledger),
            "--run-dir",
            str(run_dir),
            "--cut",
            "2026-09-04T22:50:00-04:00",
        ]
    )
    assert code == 2
    assert "cut: 2026-09-04T22:50:00-04:00 (--cut)" in caplog.text
    assert "raw rows: 15" in caplog.text  # only the last sweep is in scope


def test_a_short_cell_refuses_to_compute(tmp_path, caplog):
    rows = full_rows()
    rows.pop()
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "has 14 rows" in out


def test_death_pairing_end_to_end(tmp_path, caplog):
    rows = full_rows()
    for r in rows:
        if r["backend"] == "qwen38fnds4kimat" and r["task"] == "task-00":
            r.update(solution_empty=True, num_turns=1, wall_seconds=6.4, passed=False)
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 0
    assert "'deaths': 2" in out
    assert "wall: n_pairs 14" in out
    assert "ratio 1.00" in out


def test_below_ten_pairs_says_could_not_tell(tmp_path, caplog):
    rows = full_rows()
    for r in rows:
        if r["backend"] == "qwen38fnds4kimat" and r["task"] in set(TASKS[:6]):
            r.update(solution_empty=True, num_turns=1, wall_seconds=6.4, passed=False)
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 0
    assert "COULD NOT TELL" in out
    assert "verdict rests on pass and death bars" in out


def test_a_five_pass_gap_fails_the_screen():
    """Drive the statistics directly: a 5-pass gap is fail-side at any wall."""
    rows = full_rows()
    # Each task appears in both sweeps of an arm; flip five distinct tasks in
    # the first new sweep only, so the gap is exactly 5.
    for r in rows:
        if (
            r["backend"] == "qwen38fnds4kimat"
            and r["task"] in set(TASKS[:5])
            and r["started"] < "2026-09-04T21:00"
        ):
            r["passed"] = False
    new_rows = [r for r in rows if r["backend"] == "qwen38fnds4kimat"]
    old_rows = [r for r in rows if r["backend"] == "qwen38fnds4shim"]
    new, old = sar.tally(new_rows), sar.tally(old_rows)
    assert old["passes"] - new["passes"] == 5
    wall = sar.wall_report([("t", 100.0, 100.0)] * 15)
    lines = sar.screen_verdict(new, old, wall)
    assert any("FAIL-SIDE: pass gap <= 4" in ln for ln in lines)
    assert any("SCREEN FAIL" in ln for ln in lines)


def test_the_new_arm_passing_more_is_not_a_gap():
    """#153: the pass bar is one-directional and abs() made it symmetric.

    Observed on the #138 paired run, 2026-09-05: new 60/60, old 52/60, paired
    wall 0.56 with a CI excluding 1.0 -- and the reporter printed
    "closes as a regression at screen resolution". The bug can only fire when
    the new stack WINS, so no failing screen could ever expose it.
    """
    rows = full_rows()
    for r in rows:
        if r["backend"] == "qwen38fnds4shim" and r["task"] in set(TASKS[:4]):
            r["passed"] = False
    new = sar.tally([r for r in rows if r["backend"] == "qwen38fnds4kimat"])
    old = sar.tally([r for r in rows if r["backend"] == "qwen38fnds4shim"])
    assert new["passes"] - old["passes"] >= 5, "fixture must put new well ahead"
    wall = sar.wall_report([("t", 60.0, 100.0)] * 15)
    lines = sar.screen_verdict(new, old, wall)
    assert any("PASS-SIDE: pass gap <= 4" in ln for ln in lines), lines
    assert not any("SCREEN FAIL" in ln for ln in lines), lines
    assert any("shortfall 0" in ln for ln in lines), lines


def test_the_summary_line_shows_both_pass_counts():
    """A lead floored to zero must still be visible as two numbers."""
    rows = full_rows()
    new = sar.tally([r for r in rows if r["backend"] == "qwen38fnds4kimat"])
    old = sar.tally([r for r in rows if r["backend"] == "qwen38fnds4shim"])
    lines = sar.screen_verdict(new, old, sar.wall_report([("t", 90.0, 100.0)] * 15))
    assert any("passes new" in ln and "old" in ln for ln in lines), lines


def test_a_two_fold_wall_slower_fails_the_screen():
    rows = full_rows()
    new = sar.tally([r for r in rows if r["backend"] == "qwen38fnds4kimat"])
    old = sar.tally([r for r in rows if r["backend"] == "qwen38fnds4shim"])
    paired = [("t", 200.0, 100.0)] * 15
    wall = sar.wall_report(paired)
    assert wall["ratio"] == pytest.approx(2.0)
    lines = sar.screen_verdict(new, old, wall)
    assert any("FAIL-SIDE: wall ratio <= 1.25" in ln for ln in lines)


def test_the_old_arm_control_floor_is_a_void(tmp_path, caplog):
    rows = full_rows()
    # Three tasks fail in both old sweeps: 24/30, one pass below the floor.
    for r in rows:
        if r["backend"] == "qwen38fnds4shim" and r["task"] in set(TASKS[:3]):
            r["passed"] = False
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "old-arm control 24/30 below floor 25" in out


def test_a_client_version_split_is_void(tmp_path, caplog):
    rows = full_rows()
    rows[0]["client_version"] = "1.18.26"
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "client_version" in out


def test_a_dirty_harness_row_is_void(tmp_path, caplog):
    rows = full_rows()
    rows[0]["env"]["harness_dirty"] = True
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "harness_dirty" in out


def test_wall_pairing_uses_geometric_mean_of_eligible_trials():
    """A task with walls 100 and 400 in one arm pairs at 200, not 250."""
    rows = [
        {"task": "t", "solution_empty": False, "wall_seconds": 100},
        {"task": "t", "solution_empty": False, "wall_seconds": 400},
        {"task": "t", "solution_empty": True, "wall_seconds": 6},  # excluded
    ]
    got = sar.task_wall(rows)
    assert got == pytest.approx(200.0)


def test_a_legacy_one_time_sweep_order_is_read_as_finish_times(tmp_path, caplog):
    """Pre-2026-09-05 run directories carry one time per sweep, written when
    the sweep ENDED. Reading it as the START gave every sweep the NEXT sweep's
    rows: on the re-run that left 45 of 60 rows in no window at all and
    reported the old-arm control as 14/30 when it was 27/30.

    Those directories still have to read correctly -- they are the only copy of
    those runs.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    began = dt.datetime(2026, 9, 5, 3, 13, 35)
    (run_dir / "run-record.txt").write_text(producer_started_line(began) + "\n")
    finishes = [
        ("new-sweep1", dt.datetime(2026, 9, 5, 3, 42, 22)),
        ("old-sweep1", dt.datetime(2026, 9, 5, 4, 31, 27)),
        ("old-sweep2", dt.datetime(2026, 9, 5, 5, 24, 40)),
        ("new-sweep2", dt.datetime(2026, 9, 5, 5, 57, 59)),
    ]
    (run_dir / "sweep-order.txt").write_text(
        "".join(producer_legacy_sweep_line(t, f) + "\n" for t, f in finishes)
    )
    sweeps = sar.sweep_windows(run_dir)
    assert sweeps is not None
    # The first sweep starts when the RUN did, and each later one starts when
    # its predecessor finished.
    assert [s.tag for s in sweeps] == [t for t, _ in finishes]
    assert [s.start for s in sweeps] == [began] + [f for _, f in finishes[:-1]]


def test_a_sweep_order_mixing_both_shapes_is_refused(tmp_path, caplog):
    """One file cannot be half start-times and half finish-times; guessing
    per line would silently shift only some windows."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    began = dt.datetime(2026, 9, 5, 3, 13, 35)
    (run_dir / "run-record.txt").write_text(producer_started_line(began) + "\n")
    (run_dir / "sweep-order.txt").write_text(
        producer_sweep_line("new-sweep1", began)
        + "\n"
        + producer_legacy_sweep_line("old-sweep1", began + dt.timedelta(hours=1))
        + "\n"
    )
    with caplog.at_level("ERROR", logger="stack_agent_report"):
        assert sar.sweep_windows(run_dir) is None
    assert "mixes one-time and two-time lines" in caplog.text


def test_a_uniform_run_on_a_newer_client_is_not_void(tmp_path, caplog):
    """The check asserts uniformity, not a particular version.

    It used to also require the literal "1.18.27". OpenCode shipped 1.18.28 and
    1.18.29 on 2026-09-05, so every later run -- both arms on the same new
    client, nothing wrong with it -- would have voided on a condition that has
    nothing to do with the comparison. A check guaranteed to fire is not a
    check, which the #138 screen established three separate ways.
    """
    rows = full_rows()
    for r in rows:
        r["client_version"] = "1.18.29"
    code, out = run_report(tmp_path, rows, caplog)
    assert "client_version" not in out, out
    assert code == 0, out


def test_arms_on_different_clients_are_still_void(tmp_path, caplog):
    """The half of the old check that was doing real work."""
    rows = full_rows()
    rows[0]["client_version"] = "1.18.29"
    code, out = run_report(tmp_path, rows, caplog)
    assert "client_version varies across rows" in out, out
    assert code == 2, out


def _pair(*ratios: float) -> list[tuple[str, float, float]]:
    return [(f"t{i}", r, 1.0) for i, r in enumerate(ratios)]


def test_every_pair_agreeing_is_reported_as_agreement():
    got = sar.direction_agrees({1: _pair(0.5, 0.6, 0.7), 2: _pair(0.55, 0.65)})
    assert got["agree"] is True
    assert got["direction"] == "new-faster"


def test_one_contradicting_pair_breaks_agreement():
    """The reason the check exists. A pooled 0.58 is equally consistent with
    four pairs at 0.58 and with three at 0.4 plus one at 1.6; only the second
    is a reason to hesitate, and pooling cannot tell them apart."""
    got = sar.direction_agrees({1: _pair(0.4, 0.4), 2: _pair(1.6, 1.7)})
    assert got["agree"] is False
    assert got["direction"] == "mixed"


def test_a_pair_with_median_exactly_one_is_not_agreement():
    """A null pair has no direction; counting it as agreement would let it pass
    a check built to refuse ambiguity."""
    got = sar.direction_agrees({1: _pair(0.5, 0.5), 2: _pair(1.0, 1.0)})
    assert got["agree"] is False


def test_old_faster_is_named_not_swallowed():
    got = sar.direction_agrees({1: _pair(1.5, 1.6), 2: _pair(1.4, 1.7)})
    assert got["agree"] is True
    assert got["direction"] == "old-faster"


# --- The arms and the verdict sentence belong to the run, not to #138 ------
#
# On 2026-09-07 the #39 A/B (MTP flags on against off, one stack) ran with
# NEW_BACKEND=qwen38fnds4mtp7shim. `stack_agent_ab.sh` honoured it; this
# reporter did not, and the read-out came back VOID with 30 good rows on
# disk. These tests hold both halves: the environment must reach the module,
# and an unset environment must still reproduce #138 exactly.


@pytest.fixture
def reloaded(monkeypatch):
    """Re-import the reporter under a given environment, then put it back.

    The arm names are module constants read at import, so `monkeypatch.setenv`
    alone changes nothing -- the module must be reloaded to see them. The
    teardown reloads once more with the variables cleared, because every other
    test in this file reads `sar` at its #138 defaults.
    """
    names = ("NEW_BACKEND", "OLD_BACKEND", "REFERENCE_ARM", "QUESTION")
    import importlib

    def load(**env: str):
        for name in names:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(sar)

    yield load
    for name in names:
        monkeypatch.delenv(name, raising=False)
    importlib.reload(sar)


def test_an_unset_environment_is_138(reloaded):
    mod = reloaded()
    assert mod.NEW_BACKEND == "qwen38fnds4kimat"
    assert mod.OLD_BACKEND == "qwen38fnds4shim"
    assert mod.BACKENDS == {"qwen38fnds4kimat": "new", "qwen38fnds4shim": "old"}
    assert mod.check_arms() is None


def test_the_environment_names_the_arms(reloaded):
    mod = reloaded(NEW_BACKEND="qwen38fnds4mtp7shim", OLD_BACKEND="qwen38fnds4shim")
    assert mod.BACKENDS == {"qwen38fnds4mtp7shim": "new", "qwen38fnds4shim": "old"}
    assert mod.check_arms() is None


def test_a_one_sided_override_still_has_two_arms(reloaded):
    mod = reloaded(NEW_BACKEND="qwen38fnds4mtp7shim")
    assert set(mod.BACKENDS.values()) == {"new", "old"}
    assert mod.check_arms() is None


def test_the_same_name_on_both_arms_is_void(reloaded):
    """One name cannot be both arms: BACKENDS would hold a single entry and
    every row would score as `old`, leaving the new arm empty."""
    mod = reloaded(NEW_BACKEND="qwen38fnds4shim", OLD_BACKEND="qwen38fnds4shim")
    assert len(mod.BACKENDS) == 1, "the collapse this guard exists to catch"
    void = mod.check_arms()
    assert void and void.startswith("VOID:"), void
    assert "qwen38fnds4shim" in void


def test_the_void_stops_the_read_out(reloaded, tmp_path, caplog):
    mod = reloaded(NEW_BACKEND="qwen38fnds4shim", OLD_BACKEND="qwen38fnds4shim")
    with caplog.at_level(logging.INFO):
        rc = mod.main(["--ledger", str(tmp_path / "nothing.jsonl")])
    assert rc == 2
    assert any("VOID: both arms are named" in r.message for r in caplog.records)


def test_the_verdict_names_the_run_not_138(reloaded):
    """The three bars are general. The closing sentence is not, and #39's arms
    are the same stack twice -- "hold the Q4_0 stack" would name both of them,
    which reads as a finding about a stack that was never on trial."""
    mod = reloaded(
        REFERENCE_ARM="the same stack without the MTP flags",
        QUESTION="#39's MTP question",
    )
    new = {"n": 30, "passes": 21, "deaths": 9, "deaths_turn1": 0}
    old = {"n": 30, "passes": 28, "deaths": 2, "deaths_turn1": 0}
    lines = mod.screen_verdict(new, old, mod.wall_report([("t", 100.0, 100.0)] * 15))
    fail = [ln for ln in lines if ln.startswith("SCREEN FAIL")]
    assert fail, lines
    assert "the same stack without the MTP flags" in fail[0]
    assert "#39's MTP question" in fail[0]
    assert "#138" not in fail[0] and "Q4_0" not in fail[0]


def test_the_default_verdict_still_reads_as_138(reloaded):
    mod = reloaded()
    new = {"n": 30, "passes": 21, "deaths": 9, "deaths_turn1": 0}
    old = {"n": 30, "passes": 28, "deaths": 2, "deaths_turn1": 0}
    lines = mod.screen_verdict(new, old, mod.wall_report([("t", 100.0, 100.0)] * 15))
    fail = [ln for ln in lines if ln.startswith("SCREEN FAIL")]
    assert fail and "the Q4_0 stack" in fail[0] and "#138" in fail[0]


# --- The pass column is paired too ----------------------------------------
#
# The screen's pass bar compares 30 rows against 30. They are fifteen tasks
# run twice on each arm, so a task the new arm cannot do at all contributes
# two failures and a pooled test counts the second as fresh evidence.


def test_a_coin_is_not_surprising():
    assert sar.sign_test(0, 0) == 1.0
    assert sar.sign_test(3, 3) == 1.0
    assert sar.sign_test(4, 3) == 1.0


def test_a_clean_sweep_of_seven_is():
    """Seven tasks down, none up: p = 2/2^7."""
    assert sar.sign_test(7, 0) == pytest.approx(2 / 128)


def test_the_test_is_two_sided():
    assert sar.sign_test(7, 0) == sar.sign_test(0, 7)


def test_two_tasks_carrying_a_seven_row_shortfall_are_not_seven_facts():
    """The whole reason to count tasks. Both fixtures lose the same number of
    ROWS; one loses them on two tasks and the other on seven, and a pooled
    count cannot tell them apart."""
    concentrated = [("a", 0, 4, 4, 4), ("b", 1, 4, 4, 4)] + [
        (f"t{i}", 2, 2, 2, 2) for i in range(7)
    ]
    spread = [(f"t{i}", 1, 2, 2, 2) for i in range(7)] + [
        (f"u{i}", 2, 2, 2, 2) for i in range(2)
    ]

    def rows_lost(pairs):
        return sum(po - pn for _t, pn, _tn, po, _to in pairs)

    assert rows_lost(concentrated) == rows_lost(spread) == 7
    assert sar.pass_report(concentrated)["p"] > sar.pass_report(spread)["p"]


def test_ties_are_not_evidence():
    pairs = [(f"t{i}", 2, 2, 2, 2) for i in range(13)] + [("x", 0, 2, 2, 2)]
    got = sar.pass_report(pairs)
    assert got["ties"] == 13 and got["down"] == ["x"] and got["up"] == []
    assert got["p"] == 1.0, "one task against nothing is a coin flipped once"


def test_pass_pairs_needs_the_task_on_both_arms():
    """A task only one arm ran is not a pair. It must be dropped, not scored
    as a loss -- that is how a crashed sweep becomes a regression."""
    new = sar.Sweep("new-sweep1", dt.datetime(2026, 9, 7, 8, 43))
    new.rows = [
        {"task": "shared", "passed": True},
        {"task": "new-only", "passed": False},
    ]
    old = sar.Sweep("old-sweep1", dt.datetime(2026, 9, 7, 9, 28))
    old.rows = [{"task": "shared", "passed": True}]
    sweeps = [new, old]
    got = sar.pass_pairs(sweeps)
    assert [t for t, *_ in got] == ["shared"]


def test_the_direction_is_recorded_not_just_the_count():
    """`down` and `up` name the tasks, so a reader can go look at them."""
    pairs = [("regressed", 0, 2, 2, 2), ("improved", 2, 2, 0, 2)]
    got = sar.pass_report(pairs)
    assert got["down"] == ["regressed"] and got["up"] == ["improved"]


# --- Pooling across harness heads is refused, and overridable with a reason --
#
# The #212 slot-2 A/B came back VOID because commits landed mid-run and split
# `harness_head` across sweeps. The refusal is right by default -- two harness
# heads are normally two harnesses -- but that run's diff was docs and one
# script the benchmark never calls, so the rows were pooled under a stated
# justification rather than by deleting the check.


def rows_at_heads(*heads: str) -> list[dict]:
    return [{"env": {"harness_head": h}} for h in heads]


def test_one_harness_head_is_never_a_split():
    """Assert on the check under test, not on an empty list -- a bare fixture
    also trips the row-count check, which is a different fact."""
    got = sar.void_checks(rows_at_heads("abc1234", "abc1234"), [], [])
    assert not any("harness_head varies" in f for f in got), got


def test_two_harness_heads_are_void_by_default():
    got = sar.void_checks(rows_at_heads("abc1234", "def5678"), [], [])
    assert any("harness_head varies" in f for f in got), got


def test_the_refusal_names_both_heads_and_the_way_out():
    got = " ".join(sar.void_checks(rows_at_heads("abc1234", "def5678"), [], []))
    assert "abc1234" in got and "def5678" in got
    assert "--allow-harness-split" in got


def test_a_reason_lifts_the_refusal():
    got = sar.void_checks(
        rows_at_heads("abc1234", "def5678"), [], [], "docs only; nothing in benchmarks/"
    )
    assert not any("harness_head varies" in f for f in got), got


def test_the_override_is_announced_not_silent(caplog):
    """An override nobody can see in the output is indistinguishable from a
    check that was quietly deleted."""
    with caplog.at_level(logging.INFO):
        sar.void_checks(rows_at_heads("abc1234", "def5678"), [], [], "a stated reason")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "OVERRIDE" in msgs and "a stated reason" in msgs


def test_an_empty_reason_does_not_override():
    """`--allow-harness-split ''` is the default, and must not lift anything."""
    got = sar.void_checks(rows_at_heads("abc1234", "def5678"), [], [], "")
    assert any("harness_head varies" in f for f in got), got


def test_the_override_does_not_lift_other_voids():
    """It is a waiver for one fact, not a blanket. A short sweep is still void."""
    short = sar.Sweep("new-sweep1", dt.datetime(2026, 9, 7, 12, 23))
    short.rows = [{"task": "t"}]
    got = sar.void_checks(rows_at_heads("abc1234", "def5678"), [short], [], "docs only")
    assert any("expected" in f for f in got), got
