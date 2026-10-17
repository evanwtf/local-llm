"""scripts/timeout_sizing.py: the evidence for the flat 1,800 s step timeout.

Hand-built rows with absolute seconds. The negative cases matter most: a
timeout is never a pass, a guarded pass is not a pass, and a pass that ran
exactly as long as a limit is kept.
"""

from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
sys.path.insert(0, str(HERE / "benchmarks" / "agent"))

import timeout_sizing as tp


def row(task, secs, backend="b", passed=True, **kw):
    r = {
        "backend": backend,
        "client": "opencode",
        "task": task,
        "passed": passed,
        "wall_seconds": secs,
        "touched_tests": False,
    }
    r.update(kw)
    return r


def timeout(task, backend="b"):
    return {
        "backend": backend,
        "client": "opencode",
        "task": task,
        "error": "timeout",
        "wall_seconds": None,
    }


ROWS = [
    row("easy", 60.0),
    row("easy", 100.0),
    row("easy", 1700.0),  # one slow pass in the tail
    timeout("easy"),
    row("hard", 1400.0, backend="lead"),
    row("hard", 1600.0, backend="lead"),
    row("hard", 1000.0, backend="other"),
    timeout("hard"),
    timeout("never"),
]


def test_task_stats_counts_passes_and_timeouts_apart() -> None:
    s = {t.task: t for t in tp.task_stats(ROWS)}
    assert (s["easy"].passes, s["easy"].median, s["easy"].longest) == (3, 100.0, 1700.0)
    assert s["easy"].timeouts == 1
    assert (s["never"].passes, s["never"].median, s["never"].timeouts) == (0, None, 1)


def test_a_guarded_pass_is_not_a_pass() -> None:
    rows = [row("t", 50.0, touched_tests=True), row("t", 70.0)]
    assert tp.pass_seconds(rows) == [70.0]


def test_a_flat_limit_loses_only_longer_passes_per_backend() -> None:
    lost = tp.lost_to_flat(ROWS, 1500.0)
    assert dict(lost) == {"b": 1, "lead": 1}  # 1700 s and 1600 s
    assert sum(tp.lost_to_flat(ROWS, 1200.0).values()) == 3


def test_a_pass_exactly_at_the_limit_is_kept() -> None:
    assert not tp.lost_to_flat([row("t", 1200.0)], 1200.0)


def test_per_task_limits_clamp_and_keep_the_ceiling_without_a_pass() -> None:
    limits = tp.per_task_limits(ROWS, k=3.0, floor=600.0, limit=1800.0)
    assert limits["easy"] == 600.0  # 3 x 100 s, raised to the floor
    assert limits["hard"] == 1800.0  # 3 x 1400 s, capped at the limit
    assert limits["never"] == 1800.0  # no pass: nothing to size from


def test_simulate_per_task_counts_losses_and_savings() -> None:
    sim = tp.simulate_per_task(ROWS, k=3.0, floor=600.0, limit=1800.0)
    assert sim.passes_lost == 1  # easy's 1700 s pass
    assert sim.timeout_seconds == 3 * 1800.0
    assert sim.seconds_saved == 1200.0  # only easy's timeout ends sooner
