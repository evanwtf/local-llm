"""scripts/early_stop_check.py: the #762 rule applied to a run in flight.

Hand-built rows with absolute seconds. A leader passes 2 tasks x 3 trials at
100 s each, 600 s in all; the candidate's numbers are chosen against that.
"""

from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
sys.path.insert(0, str(HERE / "benchmarks" / "agent"))

import early_stop_check as esc

SUITES = {"replay-a": "replay", "replay-b": "hard"}


def row(backend, task, trial, secs, minute, passed=True):
    r = {
        "backend": backend,
        "client": "opencode",
        "task": task,
        "trial": trial,
        "batch": None,
        "started": f"2026-10-16T10:{minute:02d}:00-04:00",
        "passed": passed,
        "wall_seconds": secs,
        "touched_tests": False,
    }
    if secs is None:
        r.update(passed=None, error="timeout")
    return r


LEADER = [
    row("lead", t, n, 100.0, 2 * (3 * (n - 1) + i))
    for n in (1, 2, 3)
    for i, t in enumerate(["replay-a", "replay-b"])
]


def cand(times):
    """One trial per entry, round 1 first; None is a timeout."""
    tasks = ["replay-a", "replay-b"]
    return [
        row("cand", tasks[k % 2], k // 2 + 1, s, 40 + k) for k, s in enumerate(times)
    ]


def test_no_run_is_exit_1() -> None:
    status, reason = esc.check(LEADER, "missing", "replay", SUITES, "lead")
    assert status == 1 and "no replay run of missing" in reason


def test_a_fast_round_1_keeps_going() -> None:
    status, reason = esc.check(
        LEADER + cand([150.0, 150.0]), "cand", "replay", SUITES, "lead"
    )
    assert status == 0
    assert "150.0% of the leader" in reason


def test_twice_the_leaders_time_stops() -> None:
    status, reason = esc.check(
        LEADER + cand([200.0, 200.0]), "cand", "replay", SUITES, "lead"
    )
    assert status == esc.STOP and "time stop" in reason


def test_a_timeout_counts_at_the_full_limit() -> None:
    # 100 s + 1,800 s against the leader's 200 s: far past 200%.
    status, reason = esc.check(
        LEADER + cand([100.0, None]), "cand", "replay", SUITES, "lead"
    )
    assert status == esc.STOP and "1900.0 s" in reason


def test_before_round_1_only_the_failure_stop_applies() -> None:
    status, reason = esc.check(
        LEADER + cand([1700.0]), "cand", "replay", SUITES, "lead"
    )
    assert status == 0 and "round 1 not done" in reason


def test_an_unknown_pin_has_no_leader() -> None:
    status, reason = esc.check(
        LEADER + cand([100.0, 100.0]), "cand", "replay", SUITES, "nobody"
    )
    assert status == 1 and "no leader" in reason


def test_record_abort_appends_one_record_when_the_rule_stops(tmp_path) -> None:
    out = tmp_path / "screening-aborts.jsonl"
    rows = LEADER + cand([200.0, 200.0])
    status, _ = esc.check(rows, "cand", "replay", SUITES, "lead", abort_path=out)
    assert status == esc.STOP
    [line] = out.read_text().splitlines()
    rec = __import__("json").loads(line)
    assert rec["backend"] == "cand" and rec["trials_done"] == 2
    assert rec["state"] == "screened out" and "time stop" in rec["reason"]
    assert rec["reason"].startswith("stopped by hand")


def test_record_abort_writes_nothing_when_the_run_continues(tmp_path) -> None:
    out = tmp_path / "screening-aborts.jsonl"
    rows = LEADER + cand([150.0, 150.0])
    status, _ = esc.check(rows, "cand", "replay", SUITES, "lead", abort_path=out)
    assert status == 0 and not out.exists()
