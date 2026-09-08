"""Synthetic read-outs for the #191 stack A/B report (scripts/stack_agent_report_191.py).

Every fixture is built here -- none of them read tonight's ledger, which
does not exist at test time and must never become a fixture.

The refusals are the whole job, and each is checked against input that should
trip it: a clean tree tells you nothing about a guard.
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

import stack_agent_report_191 as sib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "stack_agent_ab.sh"

NEW_BACKEND = "qwen38fnmlxserve"
OLD_BACKEND = "qwen38fnds4kimat"


def producer_fmt(pattern: str) -> str:
    """The date format stack_agent_ab.sh itself writes, pulled out of the
    script. Fixtures are built from this so the reader can never drift from
    the producer again."""
    found = re.search(pattern, SCRIPT.read_text())
    assert found, f"stack_agent_ab.sh no longer matches {pattern!r}"
    return found.group(1)


def producer_started_line(when: dt.datetime) -> str:
    return f"# stack agent A/B, started {when.strftime(producer_fmt(r"started \$\(date '([^']+)'\)"))}".rstrip()


def producer_sweep_line(
    tag: str, start: dt.datetime, finish: dt.datetime | None = None
) -> str:
    fmt = producer_fmt(r"\$tag \$started \$\(date '([^']+)'\)\" >> ")
    end = finish if finish is not None else start + dt.timedelta(minutes=40)
    return f"{tag} {start.strftime(fmt)} {end.strftime(fmt)}"


TASKS = [f"task-{i:02d}" for i in range(15)]
#: Sweep order as stack_agent_ab.sh writes it: new, old, new, old.
ORDER = [
    ("new-sweep1", "20:58:00"),
    ("old-sweep1", "21:40:00"),
    ("new-sweep2", "22:22:00"),
    ("old-sweep2", "23:00:00"),
]
STARTS = [
    dt.datetime(2026, 9, 7, 20, 58, 0),
    dt.datetime(2026, 9, 7, 21, 40, 0),
    dt.datetime(2026, 9, 7, 22, 22, 0),
    dt.datetime(2026, 9, 7, 23, 0, 0),
]


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


def arm_of(tag: str) -> str:
    return tag.rsplit("-sweep", 1)[0]


def full_rows(offset: dt.timedelta = dt.timedelta(0)) -> list[dict]:
    """60 rows: 15 tasks x 4 sweeps, every trial a clean pass."""
    rows = []
    for (tag, _), base in zip(ORDER, STARTS):
        backend = NEW_BACKEND if arm_of(tag) == "new" else OLD_BACKEND
        for i, task in enumerate(TASKS):
            when = base + offset + dt.timedelta(seconds=i)
            rows.append(row(backend, task, f"2026-09-07T{when:%H:%M:%S}-04:00"))
    return rows


def write_run_dir(
    tmp_path: pathlib.Path,
    started_at: dt.datetime | None = None,
    offset: dt.timedelta = dt.timedelta(0),
) -> pathlib.Path:
    run_dir = tmp_path / "191-mlx-ab"
    run_dir.mkdir()
    if started_at is None:
        started_at = dt.datetime(2026, 9, 7, 20, 57, 17)
    (run_dir / "run-record.txt").write_text(
        producer_started_line(started_at) + "\n"
        "NEW backend=qwen38fnmlxserve engine=mlx-serve @ mlx-serve 26.9.1\n"
        "OLD backend=qwen38fnds4kimat engine=~/git/ds4-ivan-qwen38fn @ bd9cfbc\n"
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
    caplog.set_level(logging.INFO, logger="stack_agent_report_191")
    code = sib.main(["--ledger", str(ledger), "--run-dir", str(run_dir)])
    return code, caplog.text


# --- The arms are #191's, configured at main() time -------------------------


def test_importing_the_sibling_leaves_138_untouched():
    """configure() runs in main(), not at import, so importing this module
    must not move stack_agent_report's own defaults. This must run before any
    test that calls configure(), which mutates rep's globals."""
    assert sib.rep.NEW_BACKEND == "qwen38fnds4kimat"  # #138's default, unset env
    assert sib.rep.OLD_BACKEND == "qwen38fnds4shim"


def test_an_unset_environment_is_191():
    sib.configure()
    assert sib.rep.NEW_BACKEND == NEW_BACKEND
    assert sib.rep.OLD_BACKEND == OLD_BACKEND
    assert sib.rep.BACKENDS == {NEW_BACKEND: "new", OLD_BACKEND: "old"}
    assert sib.rep.check_arms() is None


def test_the_environment_names_the_arms(monkeypatch):
    """The env overrides the #191 defaults, not just re-states them."""
    monkeypatch.setenv("NEW_BACKEND", "qwen38fnmlxserve2")
    monkeypatch.setenv("OLD_BACKEND", "qwen38fnds4kimat2")
    sib.configure()
    assert sib.rep.BACKENDS == {
        "qwen38fnmlxserve2": "new",
        "qwen38fnds4kimat2": "old",
    }


def test_the_same_name_on_both_arms_is_void(monkeypatch, tmp_path, caplog):
    """One name cannot be both arms: BACKENDS would hold a single entry and
    every row would score as `old`, leaving the new arm empty."""
    monkeypatch.setenv("NEW_BACKEND", OLD_BACKEND)
    monkeypatch.setenv("OLD_BACKEND", OLD_BACKEND)
    with caplog.at_level(logging.INFO):
        rc = sib.main(["--ledger", str(tmp_path / "nothing.jsonl")])
    assert rc == 2
    assert any("VOID: both arms are named" in r.message for r in caplog.records)


# --- The wall filter is the #191 difference ---------------------------------


def test_passed_wall_uses_only_passed_trials():
    """A wrong-code failure (passed=False) contributes no wall, even though
    #138's task_wall would include it."""
    rows = [
        {"task": "t", "passed": True, "solution_empty": False, "wall_seconds": 100},
        {"task": "t", "passed": False, "solution_empty": False, "wall_seconds": 400},
        {"task": "t", "passed": True, "solution_empty": False, "wall_seconds": 400},
    ]
    got = sib.passed_wall(rows)
    assert got == pytest.approx(200.0)  # geometric mean of 100 and 400


def test_a_task_with_no_passed_trial_has_no_wall():
    rows = [
        {"task": "t", "passed": False, "solution_empty": False, "wall_seconds": 400},
        {"task": "t", "passed": False, "solution_empty": True, "wall_seconds": 6},
    ]
    assert sib.passed_wall(rows) is None


def test_a_task_that_failed_on_one_arm_does_not_pair(tmp_path):
    """A task the old arm failed is not a wall pair, however fast it ran."""
    sib.configure()  # a prior test left BACKENDS collapsed; re-establish #191
    rows = full_rows()
    for r in rows:
        if r["backend"] == OLD_BACKEND and r["task"] == "task-00":
            r.update(passed=False, solution_empty=False, wall_seconds=400)
    run_dir = write_run_dir(tmp_path)
    sweeps = sib.rep.sweep_windows(run_dir)
    sib.rep.assign(rows, sweeps)
    paired = sib.pairs_by_task(sweeps)
    assert "task-00" not in [t for t, _, _ in paired]
    assert len(paired) == 14


# --- The refusals, each tripped by input that should trip it -----------------


def test_below_ten_pairs_says_could_not_tell(tmp_path, caplog):
    """Six tasks dead on the new arm leave nine wall pairs: below MIN_PAIRS=10,
    the wall endpoint is COULD NOT TELL, not a number."""
    rows = full_rows()
    for r in rows:
        if r["backend"] == NEW_BACKEND and r["task"] in set(TASKS[:6]):
            r.update(passed=False, solution_empty=False, wall_seconds=400)
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 0
    assert "COULD NOT TELL" in out
    assert "verdict rests on pass and death bars" in out


def test_the_old_arm_control_floor_is_a_void(tmp_path, caplog):
    """Three tasks fail in both old sweeps: 24/30, one pass below the 83% floor."""
    rows = full_rows()
    for r in rows:
        if r["backend"] == OLD_BACKEND and r["task"] in set(TASKS[:3]):
            r["passed"] = False
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "old-arm control 24/30 below floor 25" in out


def test_a_harness_head_split_is_void(tmp_path, caplog):
    rows = full_rows()
    rows[0]["env"]["harness_head"] = "def5678"
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "harness_head varies" in out


def test_a_short_sweep_cell_is_void(tmp_path, caplog):
    rows = full_rows()
    rows.pop()
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "has 14 rows" in out


def test_an_arm_with_zero_rows_is_void(tmp_path, caplog):
    """The new arm produced no rows at all -- the 2026-09-08 failure mode.

    The reporter must VOID loudly, not divide by zero and not quietly report
    the old arm as if it were a comparison. The raw-count and per-sweep checks
    both fire before any pass or wall arithmetic runs.
    """
    rows = [r for r in full_rows() if r["backend"] != NEW_BACKEND]
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "raw count 30 != 60" in out
    assert "new-sweep1 has 0 rows" in out
    assert "wall:" not in out  # never reached the wall arithmetic


# --- The happy path ----------------------------------------------------------


def test_a_clean_run_reports_pass_before_wall(tmp_path, caplog):
    code, out = run_report(tmp_path, full_rows(), caplog)
    assert code == 0
    assert out.index("paired pass") < out.index("wall:")
    assert "wall: n_pairs 15" in out
    assert "SCREEN PASS" in out
