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
        "".join(
            producer_sweep_line(tag, base + offset) + "\n"
            for (tag, _), base in zip(ORDER, STARTS)
        )
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
