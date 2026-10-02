"""The #762 early stop: stop a run once it can no longer earn a place. #762

The operator set both thresholds on 2026-09-25, and on 2026-09-27 made not
wasting compute on bad models a primary goal. These tests pin absolute values
from real runs, not relationships: a relationship-only test passes under a
uniformly wrong convention.

- Failure stop: ceil(0.30 x the leader's passes, scaled to this run's planned
  trials), checked after every trial. The visible verdict decides a failure,
  with its guards; a timeout is a failure.
- Time stop: at 2x the leader's time on the same (task, trial) pairs, checked
  once every task has run its first trial. A timeout counts at the full limit.
"""

from __future__ import annotations

import screening as sc

LIMIT = 1800.0


def row(
    task,
    trial,
    *,
    passed=True,
    secs=100.0,
    touched=False,
    timeout=False,
    batch="b",
    client="opencode",
    excluded=False,
):
    r = {
        "batch": batch,
        "client": client,
        "task": task,
        "trial": trial,
        "passed": None if timeout else passed,
        "wall_seconds": None if timeout else secs,
        "touched_tests": touched,
    }
    if timeout:
        r["error"] = "timeout"
        r["timeout_reason"] = "wall-clock"
    if excluded:
        r["excluded"] = True
    return r


def leader_rows(tasks, trials, fails=0, secs=100.0, batch="lead"):
    out = []
    n = 0
    for t in range(1, trials + 1):
        for task in tasks:
            n += 1
            out.append(row(task, t, passed=n > fails, secs=secs, batch=batch))
    return out


TASKS14 = [f"replay-{i}" for i in range(14)]


# --- the failure threshold -------------------------------------------------


def test_threshold_matches_the_operators_example() -> None:
    # "assume the leader passes 20/20, if the new one fails 6/20 we would stop"
    assert sc.failure_threshold(20, 20, planned=20) == 6


def test_threshold_on_the_m5_max_sushi_run_2_leader() -> None:
    # mlx-serve 26.9.6 passed 35/42; run 2 planned 42 trials: 10.5 -> 11
    assert sc.failure_threshold(35, 42, planned=42) == 11


def test_threshold_scales_a_leader_of_another_size() -> None:
    # mlx-serve 26.9.5 passed 26/40 (2 excluded); a 42-trial run: 27.3 -> 8.19 -> 9
    assert sc.failure_threshold(26, 40, planned=42) == 9


def test_threshold_standard_and_replay_sets_from_issue_762() -> None:
    assert sc.failure_threshold(21, 21, planned=21) == 7
    assert sc.failure_threshold(30, 30, planned=30) == 9
    assert sc.failure_threshold(45, 45, planned=45) == 14


# --- what counts as a failure ------------------------------------------------


def test_a_timeout_and_a_touched_pass_are_failures() -> None:
    assert sc.is_failure(row("t", 1, timeout=True))
    assert sc.is_failure(row("t", 1, touched=True))
    assert sc.is_failure(row("t", 1, passed=False))
    assert not sc.is_failure(row("t", 1))


def test_seconds_counts_a_timeout_at_the_limit() -> None:
    assert sc.seconds(row("t", 1, secs=321.5), LIMIT) == 321.5
    assert sc.seconds(row("t", 1, timeout=True), LIMIT) == 1800.0
    assert sc.seconds(row("t", 1, timeout=True), 3600.0) == 3600.0


# --- choosing the leader ---------------------------------------------------


def test_leader_is_the_best_pass_rate_then_the_fastest() -> None:
    history = (
        leader_rows(TASKS14, 3, fails=7, secs=50.0, batch="fast-but-fails")
        + leader_rows(TASKS14, 3, fails=0, secs=300.0, batch="slow-clean")
        + leader_rows(TASKS14, 3, fails=0, secs=200.0, batch="clean")
    )
    lead = sc.pick_leader(history, TASKS14, client="opencode", exclude_batch="now")
    assert lead is not None
    assert lead.batch == "clean"
    assert (lead.passes, lead.rows) == (42, 42)


def test_leader_must_cover_every_task_and_ignores_this_batch() -> None:
    partial = leader_rows(TASKS14[:10], 3, batch="partial")
    this_run = leader_rows(TASKS14, 3, batch="now")
    assert sc.pick_leader(partial + this_run, TASKS14, "opencode", "now") is None


def test_leader_ignores_excluded_rows_and_other_clients() -> None:
    rows = leader_rows(TASKS14, 1, batch="lead")
    rows[0]["excluded"] = True
    other = leader_rows(TASKS14, 1, batch="claude-run")
    for r in other:
        r["client"] = "claude"
    lead = sc.pick_leader(rows + other, TASKS14, "opencode", "now")
    assert lead is None or lead.batch != "claude-run"
    # task 0 lost its only row, so "lead" no longer covers every task
    assert lead is None


# --- the stop decision -------------------------------------------------------


def test_failure_stop_fires_at_the_threshold_not_before() -> None:
    lead = sc.leader_from_rows(leader_rows(TASKS14, 3, fails=7), "lead")  # 35/42
    done = [row(TASKS14[i], 1, passed=False) for i in range(10)]
    stop, why = sc.should_stop(done, lead, TASKS14, planned=42, timeout=LIMIT)
    assert not stop
    done.append(row(TASKS14[10], 1, timeout=True))
    stop, why = sc.should_stop(done, lead, TASKS14, planned=42, timeout=LIMIT)
    assert stop
    assert "11 failures" in why and "threshold 11" in why


def test_excluded_rows_are_not_counted() -> None:
    lead = sc.leader_from_rows(leader_rows(TASKS14, 3, fails=7), "lead")
    done = [row(TASKS14[i], 1, passed=False, excluded=True) for i in range(12)]
    assert sc.should_stop(done, lead, TASKS14, planned=42, timeout=LIMIT)[0] is False


def test_time_stop_waits_for_the_first_full_round() -> None:
    lead = sc.leader_from_rows(leader_rows(TASKS14, 3, secs=100.0), "lead")
    # 13 of 14 tasks done, each 10x the leader: no time verdict yet
    done = [row(t, 1, secs=1000.0) for t in TASKS14[:13]]
    assert sc.should_stop(done, lead, TASKS14, planned=42, timeout=LIMIT)[0] is False
    done.append(row(TASKS14[13], 1, secs=1000.0))
    stop, why = sc.should_stop(done, lead, TASKS14, planned=42, timeout=LIMIT)
    assert stop
    assert "14000.0 s" in why and "1400.0 s" in why


def test_time_stop_at_exactly_2x_fires_and_below_does_not() -> None:
    lead = sc.leader_from_rows(leader_rows(TASKS14, 1, secs=100.0), "lead")
    under = [row(t, 1, secs=199.0) for t in TASKS14]
    assert sc.should_stop(under, lead, TASKS14, planned=42, timeout=LIMIT)[0] is False
    at = [row(t, 1, secs=200.0) for t in TASKS14]
    assert sc.should_stop(at, lead, TASKS14, planned=42, timeout=LIMIT)[0] is True


def test_time_uses_the_leaders_task_median_when_a_trial_is_missing() -> None:
    # leader ran each task once (100 s); this run's trial-2 rows compare to it
    lead = sc.leader_from_rows(leader_rows(TASKS14, 1, secs=100.0), "lead")
    done = [row(t, 1, secs=100.0) for t in TASKS14] + [row(TASKS14[0], 2, secs=2000.0)]
    stop, why = sc.should_stop(done, lead, TASKS14, planned=42, timeout=LIMIT)
    # 3,400 s against 1,500 s is 2.27x
    assert stop
    assert "3400.0 s" in why and "1500.0 s" in why


def test_sushi_run_2_shape_stops_at_its_eleventh_failure() -> None:
    """The M5 Max case that motivated this: 29/41 against a 35/42 leader."""
    lead = sc.leader_from_rows(leader_rows(TASKS14, 3, fails=7), "lead")
    done, fired_at = [], None
    outcomes = [False] * 12 + [True] * 29  # worst case: failures first
    for i, ok in enumerate(outcomes):
        done.append(row(TASKS14[i % 14], 1 + i // 14, passed=ok))
        if sc.should_stop(done, lead, TASKS14, planned=42, timeout=LIMIT)[0]:
            fired_at = i + 1
            break
    assert fired_at == 11


# --- runs without a batch (the DGX cluster's rows carry batch: null) ---------


def loose(task, trial, started, backend="glm"):
    r = row(task, trial, batch=None)
    r.update(backend=backend, started=started)
    return r


def test_runs_split_batchless_rows_on_a_repeated_trial() -> None:
    rows = [
        loose("a", 1, "2026-09-24T13:00:00-0400"),
        loose("b", 1, "2026-09-24T13:30:00-0400"),
        loose("a", 1, "2026-09-24T14:00:00-0400"),  # (a, 1) again: a new run
    ]
    got = sc.runs(rows)
    assert sorted(got) == ["glm@2026-09-24T13:00", "glm@2026-09-24T14:00"]
    assert len(got["glm@2026-09-24T13:00"]) == 2


def test_runs_split_batchless_rows_on_a_three_hour_gap() -> None:
    rows = [
        loose("a", 1, "2026-09-24T13:00:00-0400"),
        loose("b", 1, "2026-09-24T16:00:00-0400"),  # exactly 3 h: same run
        loose("c", 1, "2026-09-24T19:00:01-0400"),  # over 3 h: a new run
    ]
    assert [len(v) for v in sc.runs(rows).values()] == [2, 1]


def test_runs_keep_batches_whole_and_backends_apart() -> None:
    rows = [row("a", 1, batch="b1"), row("a", 1, batch="b1")]
    rows += [loose("a", 1, "2026-09-24T13:00:00-0400", backend="x")]
    rows += [loose("a", 1, "2026-09-24T13:05:00-0400", backend="y")]
    got = sc.runs(rows)
    assert len(got["b1"]) == 2
    assert "x@2026-09-24T13:00" in got and "y@2026-09-24T13:05" in got


def test_pick_leader_finds_a_batchless_leader() -> None:
    rows = [
        loose(t, 1, f"2026-09-24T13:{i:02d}:00-0400") for i, t in enumerate(TASKS14)
    ]
    lead = sc.pick_leader(rows, TASKS14, "opencode", exclude_batch=None)
    assert lead is not None
    assert lead.batch == "glm@2026-09-24T13:00"
    assert (lead.passes, lead.rows) == (14, 14)


def test_runs_split_a_standard_block_from_a_replay_block_under_the_gap() -> None:
    # A standard run then a replay run on one launch: no (task, trial) pair
    # repeats and the gap is under 3 h, so only the suite tells them apart.
    rows = [
        loose("defang", 1, "2026-09-24T13:00:00-0400"),
        loose("login", 1, "2026-09-24T13:20:00-0400"),
        loose("replay-defang", 1, "2026-09-24T13:40:00-0400"),
        loose("replay-login", 1, "2026-09-24T14:00:00-0400"),
    ]
    got = sc.runs(rows)
    assert sorted(len(v) for v in got.values()) == [2, 2]
    assert {r["task"] for r in got["glm@2026-09-24T13:40"]} == {
        "replay-defang",
        "replay-login",
    }


def test_suite_map_splits_the_714_set_from_the_hard_set() -> None:
    cfg = {
        "task": [
            {"name": "defang"},
            {"name": "replay-a", "kind": "replay"},
            {"name": "replay-b-hidden", "kind": "replay", "suite": "hard"},
        ]
    }
    suites = sc.suite_map(cfg)
    assert suites == {
        "defang": "standard",
        "replay-a": "replay",
        "replay-b-hidden": "hard",
    }
    rows = [
        loose("replay-a", 1, "2026-09-24T13:00:00-0400"),
        loose("replay-b-hidden", 1, "2026-09-24T13:20:00-0400"),
    ]
    # #900: replay and hard share one launch (--replay --replay-hard), so they
    # group as one run. Only standard is split off.
    assert len(sc.runs(rows, suites)) == 1
    assert len(sc.runs(rows)) == 1


def test_a_replay_plus_hard_launch_is_one_run_and_a_leader_900() -> None:
    # The #892 shape: 7 replay tasks and 7 hard tasks, interleaved in one
    # launch, no batch. Before #900 the suite split made two 7-task runs, so
    # pick_leader never found a run with all 14 and early stop stayed off.
    replay_set = [f"replay-r{i}" for i in range(7)]
    hard_set = [f"replay-h{i}-hidden" for i in range(7)]
    suites = {t: "replay" for t in replay_set} | {t: "hard" for t in hard_set}
    tasks = replay_set + hard_set
    rows = [
        loose(t, 1, f"2026-10-01T14:{i * 3:02d}:00-0400") for i, t in enumerate(tasks)
    ]
    got = sc.runs(rows, suites)
    assert list(got) == ["glm@2026-10-01T14:00"]
    assert len(got["glm@2026-10-01T14:00"]) == 14
    lead = sc.pick_leader(rows, tasks, "opencode", None, suites=suites)
    assert lead is not None
    assert (lead.batch, lead.passes, lead.rows) == ("glm@2026-10-01T14:00", 14, 14)
    # The same launch still leads a run of either half on its own.
    half = sc.pick_leader(rows, hard_set, "opencode", None, suites=suites)
    assert half is not None
    assert (half.passes, half.rows) == (7, 7)


def test_standard_still_splits_from_a_replay_plus_hard_launch_900() -> None:
    suites = {"defang": "standard", "replay-a": "replay", "replay-b-hidden": "hard"}
    rows = [
        loose("defang", 1, "2026-10-01T14:00:00-0400"),
        loose("replay-a", 1, "2026-10-01T14:10:00-0400"),
        loose("replay-b-hidden", 1, "2026-10-01T14:20:00-0400"),
    ]
    got = sc.runs(rows, suites)
    assert sorted(len(v) for v in got.values()) == [1, 2]
