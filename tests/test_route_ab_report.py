"""Tests for the #149 route A/B report.

The load-bearing properties here are the ones that would silently corrupt
the measurement if they broke: a row must never pool across arms (the arms
share one backend name, so window attribution is the only thing that says
which route served it), a partial sweep must void the screens rather than
sit in an aggregate, and the screens must fire at exactly the thresholds
pre-registered on #149 -- not at convenient ones.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib

_spec = importlib.util.spec_from_file_location(
    "route_ab_report",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "route_ab_report.py",
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def _row(task: str, started: str, wall: float, passed: bool) -> dict:
    return {
        "task": task,
        "backend": "qwen38fnds4shim",
        "started": started,
        "wall_seconds": wall,
        "passed": passed,
    }


def _write_ledger(tmp_path: pathlib.Path, rows: list[dict]) -> pathlib.Path:
    ledger = tmp_path / "results.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return ledger


def _write_run_dir(
    tmp_path: pathlib.Path,
    windows: list[tuple[str, str, str]],
    started: str = "2026-09-05T08:22:33",
) -> pathlib.Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    (run_dir / "run-record.txt").write_text(
        f"# route agent A/B, started {started} EDT\n"
    )
    (run_dir / "sweep-order.txt").write_text(
        "".join(f"{tag} {s} {e}\n" for tag, s, e in windows)
    )
    return run_dir


TASKS = [f"task{i}" for i in range(15)]


def _sweep_rows(
    idx: int, day: int, hour: int, *, fails: set[str] | None = None, wall: float = 100.0
) -> list[dict]:
    """15 rows, one per task, starting at HH:00:00 on the given day."""
    return [
        _row(
            t,
            f"2026-09-{day:02d}T{hour:02d}:{i:02d}:00",
            wall,
            t not in (fails or set()),
        )
        for i, t in enumerate(TASKS)
    ]


FULL_WINDOWS = [
    ("t-sweep1", "09:00:00", "09:59:00"),
    ("r-sweep1", "10:00:00", "10:59:00"),
    ("t-sweep2", "11:00:00", "11:59:00"),
    ("r-sweep2", "12:00:00", "12:59:00"),
    ("t-sweep3", "13:00:00", "13:59:00"),
    ("r-sweep3", "14:00:00", "14:59:00"),
]


def test_windows_shift_across_midnight(tmp_path):
    """A run past midnight dates its later windows on the next day."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run-record.txt").write_text(
        "# route agent A/B, started 2026-09-05T23:40:00 EDT\n"
    )
    (run_dir / "sweep-order.txt").write_text(
        "t-sweep1 23:50:00 00:35:00\nr-sweep1 01:00:00 01:45:00\n"
    )
    anchor = dt.datetime(2026, 9, 5, 23, 40)
    wins = report.read_windows(run_dir, "t-sweep", "r-sweep", anchor)
    assert wins[0][2] == dt.datetime(2026, 9, 5, 23, 50)
    assert wins[0][3] == dt.datetime(2026, 9, 6, 0, 35)
    assert wins[1][2] == dt.datetime(2026, 9, 6, 1, 0)


def test_rows_assign_by_window_and_leftovers_are_counted(tmp_path):
    """A row between windows pools nowhere: it is leftover, never attributed."""
    run_dir = _write_run_dir(
        tmp_path,
        [("t-sweep1", "09:00:00", "09:59:00"), ("r-sweep1", "10:00:00", "10:59:00")],
    )
    anchor = dt.datetime(2026, 9, 5, 9, 0)
    wins = report.read_windows(run_dir, "t-sweep", "r-sweep", anchor)
    rows = (
        _sweep_rows(1, 5, 9)
        + _sweep_rows(1, 5, 10)
        + [_row("orphan", "2026-09-05T09:59:30", 10.0, True)]
    )
    per, leftover = report.assign(rows, wins)
    assert len(per["t-sweep1"]) == 15
    assert len(per["r-sweep1"]) == 15
    assert len(leftover) == 1


def test_short_window_voids_instead_of_pooling(tmp_path, capsys):
    """14 of 15 rows is a hole, not a datapoint."""
    run_dir = _write_run_dir(
        tmp_path,
        [("t-sweep1", "08:30:00", "09:00:00"), ("r-sweep1", "09:10:00", "09:40:00")],
    )
    rows = _sweep_rows(1, 5, 8)[:-1] + _sweep_rows(1, 5, 9)
    ledger = _write_ledger(tmp_path, rows)
    rc = report.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "VOID" in out
    assert "screens are not evaluated" in out


def test_screen1_fires_on_a_consistent_task_flip(tmp_path, capsys):
    """A task failing 2 of 3 t sweeps while passing 3 of 3 r sweeps is the screen."""
    rows = []
    rows += _sweep_rows(1, 5, 9, fails={"task0"})
    rows += _sweep_rows(1, 5, 10)
    rows += _sweep_rows(2, 5, 11, fails={"task0"})
    rows += _sweep_rows(2, 5, 12)
    rows += _sweep_rows(3, 5, 13)
    rows += _sweep_rows(3, 5, 14)
    run_dir = _write_run_dir(tmp_path, FULL_WINDOWS)
    ledger = _write_ledger(tmp_path, rows)
    report.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
    out = capsys.readouterr().out
    assert "screen 1 FIRES: t breaks task0" in out


def test_screen2_needs_six_tasks_not_three(tmp_path, capsys):
    """A pass deficit of 3 is 1 sigma and must read as no signal; 6 fires.

    The deficit is failing t runs (r never fails here), so each fixture
    fails `deficit` distinct tasks in one t sweep only -- no screen-1
    interaction either way.
    """
    for deficit, expect_fire in ((3, False), (6, True)):
        rows = []
        failing = {f"task{i}" for i in range(deficit)}
        hour = 9
        for idx in (1, 2, 3):
            rows += _sweep_rows(idx, 5, hour, fails=failing if idx == 1 else None)
            hour += 1
            rows += _sweep_rows(idx, 5, hour)
            hour += 1
        run_dir = _write_run_dir(tmp_path, FULL_WINDOWS)
        ledger = _write_ledger(tmp_path, rows)
        report.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
        out = capsys.readouterr().out
        assert ("screen 2: FIRES" in out) == expect_fire, f"deficit={deficit}"


def _fixture_from_walls(walls: dict[int, tuple[float, float]]):
    """Rows and windows where the t/r wall per sweep is as given."""
    rows: list[dict] = []
    hour = 9
    for idx in (1, 2, 3):
        rows += _sweep_rows(idx, 5, hour, wall=walls[idx][0] / 15)
        hour += 1
        rows += _sweep_rows(idx, 5, hour, wall=walls[idx][1] / 15)
        hour += 1
    return rows


def test_screen3_fires_only_when_all_pairs_are_slower_and_pooled(tmp_path, capsys):
    """Two slower pairs and one faster do not reach the dominated screen."""
    walls = {1: (120.0, 100.0), 2: (120.0, 100.0), 3: (90.0, 100.0)}
    ledger = _write_ledger(tmp_path, _fixture_from_walls(walls))
    run_dir = _write_run_dir(tmp_path, FULL_WINDOWS)
    report.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
    out = capsys.readouterr().out
    assert "screen 3: does not fire" in out


def test_screen3_fires_on_consistent_slowness(tmp_path, capsys):
    walls = {1: (120.0, 100.0), 2: (120.0, 100.0), 3: (118.0, 100.0)}
    ledger = _write_ledger(tmp_path, _fixture_from_walls(walls))
    run_dir = _write_run_dir(tmp_path, FULL_WINDOWS)
    report.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
    out = capsys.readouterr().out
    assert "screen 3: FIRES" in out


def test_screen4_fires_on_a_clean_buy(tmp_path, capsys):
    """All pairs faster, pooled <= 0.95, no flips, t >= r passes."""
    walls = {1: (90.0, 100.0), 2: (90.0, 100.0), 3: (90.0, 100.0)}
    ledger = _write_ledger(tmp_path, _fixture_from_walls(walls))
    run_dir = _write_run_dir(tmp_path, FULL_WINDOWS)
    report.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
    out = capsys.readouterr().out
    assert "screen 4: FIRES" in out


def test_route_evidence_requires_the_arm_line(tmp_path):
    """An r window whose server log shows the tensor route is not evidence."""
    run_dir = _write_run_dir(tmp_path, [("r-sweep1", "08:30:00", "09:00:00")])
    log = run_dir / "server-r-sweep1.log"
    log.write_text("ds4: " + report.WITHHOLD_LINE + "\n")
    assert report.route_evidence(run_dir, "r-sweep1", "r") is True
    log.write_text(
        "ds4: " + report.TENSOR_LINE + "\n" + "ds4: " + report.WITHHOLD_LINE + "\n"
    )
    assert report.route_evidence(run_dir, "r-sweep1", "r") is False
    log.unlink()
    assert report.route_evidence(run_dir, "r-sweep1", "r") is None


def test_per_task_walls_appear_in_the_report(tmp_path, capsys):
    """The wall grid shows each row's wall_seconds, one line per task."""
    walls = {1: (90.0, 100.0), 2: (90.0, 100.0), 3: (90.0, 100.0)}
    ledger = _write_ledger(tmp_path, _fixture_from_walls(walls))
    run_dir = _write_run_dir(tmp_path, FULL_WINDOWS)
    report.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
    out = capsys.readouterr().out
    assert "per-task wall_seconds" in out
    assert "task0: t=6/6/6 r=7/7/7" in out


def test_report_never_recommends_a_regime(tmp_path, capsys):
    """The deliverable is the numbers; the choice stays with the issue."""
    walls = {1: (90.0, 100.0), 2: (90.0, 100.0), 3: (90.0, 100.0)}
    ledger = _write_ledger(tmp_path, _fixture_from_walls(walls))
    run_dir = _write_run_dir(tmp_path, FULL_WINDOWS)
    report.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
    out = capsys.readouterr().out
    assert "option (a) or option (b)" in out
    assert "choose option" not in out
