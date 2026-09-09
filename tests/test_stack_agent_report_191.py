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

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "vault" / "stack_agent_ab.sh"

NEW_BACKEND = "qwen38fnmlxserve"
OLD_BACKEND = "qwen38fnds4kimat"

#: The harness head every clean row carries, and the run-record pins.
PINNED_HEAD = "abc1234"


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
    ("old-sweep2", "23:04:00"),
]
# Local-aware, not naive. These are wall-clock instants from a real run whose
# `run-record.txt` was written by `date`, so the local zone IS their meaning --
# `.astimezone()` on a naive value attaches exactly that, and the formatted
# output is unchanged unless the format carries %z.
STARTS = [
    dt.datetime(2026, 9, 7, 20, 58, 0).astimezone(),
    dt.datetime(2026, 9, 7, 21, 40, 0).astimezone(),
    dt.datetime(2026, 9, 7, 22, 22, 0).astimezone(),
    dt.datetime(2026, 9, 7, 23, 4, 0).astimezone(),
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
        "env": {"harness_head": PINNED_HEAD, "harness_dirty": False},
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
        started_at = dt.datetime(2026, 9, 7, 20, 57, 17).astimezone()
    (run_dir / "run-record.txt").write_text(
        producer_started_line(started_at) + "\n"
        "NEW backend=qwen38fnmlxserve engine=mlx-serve @ mlx-serve 26.9.1\n"
        "OLD backend=qwen38fnds4kimat engine=~/git/ds4-ivan-qwen38fn @ bd9cfbc\n"
        f"harness pinned at {PINNED_HEAD} for all 8 sweeps\n"
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
    allow_harness_split: str = "",
):
    ledger = write_ledger(tmp_path, rows)
    run_dir = write_run_dir(tmp_path, started_at, offset)
    caplog.set_level(logging.INFO, logger="stack_agent_report_191")
    argv = ["--ledger", str(ledger), "--run-dir", str(run_dir)]
    if allow_harness_split:
        argv += ["--allow-harness-split", allow_harness_split]
    code = sib.main(argv)
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
    """A row at a different head inside the window is a VOID, not a preference.

    The head selector drops it; the timestamp cut keeps it. The two selectors
    disagree, so the run's identity is ambiguous and the report refuses.
    """
    rows = full_rows()
    rows[0]["env"]["harness_head"] = "def5678"
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "head selector and timestamp cut disagree" in out


def test_a_leftover_at_a_different_head_is_not_counted(tmp_path, caplog):
    """A row from an aborted attempt at a different head must not count.

    The head selector is primary. A row whose env names a different head is
    not tonight's, whatever its timestamp. Here the leftover sits inside the
    timestamp window, so the cut would keep it but the head selector would
    not -- the two selectors disagree, and the report refuses rather than
    count a row that is not the run's.
    """
    rows = full_rows()
    rows.append(
        row(
            NEW_BACKEND,
            "mbox-strip-envelope",
            "2026-09-07T21:00:00-04:00",
            env={"harness_head": "636d3a0", "harness_dirty": True},
        )
    )
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "head selector and timestamp cut disagree" in out


def test_a_leftover_before_the_cut_is_excluded(tmp_path, caplog):
    """A leftover from an aborted attempt before the cut is not counted.

    Both selectors agree to drop it: it is before the cut and at a different
    head. The report proceeds with the clean rows.
    """
    rows = full_rows()
    rows.append(
        row(
            NEW_BACKEND,
            "mbox-strip-envelope",
            "2026-09-07T20:00:00-04:00",
            env={"harness_head": "636d3a0", "harness_dirty": True},
        )
    )
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 0
    assert "raw rows: 60" in out


def test_a_row_after_the_last_sweep_finish_is_excluded(tmp_path, caplog):
    """A row after the final sweep's finish is not absorbed into it.

    It passes the head selector and the timestamp cut, but it is after the
    last sweep's recorded finish. The last window closes like every other;
    the row fits no window and the report refuses rather than count it.
    """
    rows = full_rows()
    rows.append(row(NEW_BACKEND, "task-00", "2026-09-07T23:45:00-04:00"))
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "fit no sweep window" in out


def test_a_row_between_windows_is_excluded(tmp_path, caplog):
    """A row during a server restart fits no window.

    It is after one sweep's finish and before the next one's start. The
    earlier sweep must not absorb it.
    """
    rows = full_rows()
    # new-sweep1 finishes 21:38, old-sweep1 starts 21:40 -- a restart gap.
    rows.append(row(NEW_BACKEND, "task-00", "2026-09-07T21:39:00-04:00"))
    code, out = run_report(tmp_path, rows, caplog)
    assert code == 2
    assert "fit no sweep window" in out


def test_allow_harness_split_is_a_noop_and_refuses(tmp_path, caplog):
    """The head selector enforces head-split by selection, so the override
    flag cannot work. A flag that silently does nothing is a trap; it refuses
    loudly rather than pretend it had an effect."""
    code, out = run_report(
        tmp_path, full_rows(), caplog, allow_harness_split="docs only"
    )
    assert code == 2
    assert "--allow-harness-split is a no-op" in out


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
