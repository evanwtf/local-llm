"""#762 Stage 2: the cut after one full run. Absolute values, from #762's own table.

The cluster's 2026-09-24/25 replay data, as #762 applied the policy by hand:
GLM-5.3-Flash EXL3 1,856.9 s, DSV4-Flash-Vision 2,854.2 s, Qwen3.8-Flash-Next
NVFP4 3,036.8 s and DeepSeek V4.1 EXL3 3,207.7 s kept; Qwen3.8-Flash-Next FP8
3,823.1 s cut, 26% behind the third. Every stack there passed 21/21.

That table predates the operator's 2x time rule (2026-09-25). Under it, FP8 is
screened out before ranking: 3,823.1 s is 205.9% of GLM's 1,856.9 s.
"""

from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
sys.path.insert(0, str(HERE / "benchmarks" / "agent"))

import screen_stacks as ss

TASKS = [f"replay-{i}" for i in range(7)]


def batch_rows(batch, backend, total_secs, fails=0, trials=3, timeouts=0):
    """Rows whose per-task medians sum to ``total_secs`` (equal per task)."""
    per = total_secs / len(TASKS)
    out, n = [], 0
    for t in range(1, trials + 1):
        for task in TASKS:
            n += 1
            r = {
                "batch": batch,
                "backend": backend,
                "client": "opencode",
                "task": task,
                "trial": t,
                "started": f"2026-09-25T0{t}:00:00-04:00",
                "passed": n > fails,
                "wall_seconds": per,
                "touched_tests": False,
            }
            if n <= timeouts:
                r.update(passed=None, wall_seconds=None, error="timeout")
            out.append(r)
    return out


CLUSTER = (
    batch_rows("b-glm", "glm53exl3", 1856.9)
    + batch_rows("b-dsv4", "dsv4vision", 2854.2)
    + batch_rows("b-nvfp4", "qwen38fnnvfp4", 3036.8)
    + batch_rows("b-v41", "v41exl3", 3207.7)
    + batch_rows("b-fp8", "qwen38fnfp8", 3823.1)
)


def verdicts(rows, **kw):
    return {v.batch: v for v in ss.screen(rows, TASKS, client="opencode", **kw)}


def test_issue_762_table_keeps_four_and_screens_out_fp8() -> None:
    v = verdicts(CLUSTER)
    assert [b for b, x in v.items() if x.result == "keep"] == [
        "b-glm",
        "b-dsv4",
        "b-nvfp4",
        "b-v41",
    ]
    assert v["b-fp8"].result == "screened out"
    assert "205.9% of the leader glm53exl3's 1856.9 s" in v["b-fp8"].reason


def test_the_25_percent_band_keeps_a_close_fourth_and_cuts_a_distant_one() -> None:
    # Without GLM the leader is DSV4 (2,854.2 s) and V4.1 is rank 3 (3,207.7 s).
    # FP8 (3,823.1 s) is 19.2% behind rank 3: kept. A 4,100 s stack is 27.8%
    # behind: cut, though at 143.6% of the leader it passes the time gate.
    rows = [r for r in CLUSTER if r["batch"] != "b-glm"] + batch_rows(
        "b-far", "far", 4100.0
    )
    v = verdicts(rows)
    assert v["b-fp8"].result == "keep"
    assert "19.2% behind rank 3" in v["b-fp8"].reason
    assert v["b-far"].result == "cut"
    assert "27.8% behind rank 3" in v["b-far"].reason


def test_sum_of_medians_and_pass_rate_are_absolute() -> None:
    v = verdicts(CLUSTER)
    assert round(v["b-v41"].sum_medians, 1) == 3207.7
    assert (v["b-v41"].passes, v["b-v41"].rows) == (21, 21)


def test_a_stack_under_90_percent_is_screened_out_not_ranked() -> None:
    rows = CLUSTER + batch_rows("b-mimo", "mimo", 1000.0, fails=3)  # 18/21 = 85.7%
    v = verdicts(rows)
    assert v["b-mimo"].result == "screened out"
    assert "18/21" in v["b-mimo"].reason and "90%" in v["b-mimo"].reason


def test_exactly_90_percent_passes_the_gate() -> None:
    # 19/21 is 90.5%, just over the gate
    rows = batch_rows("b-lead", "lead", 1000.0) + batch_rows(
        "b-edge", "edge", 1100.0, fails=2, trials=3
    )
    v = verdicts(rows)
    assert v["b-edge"].passes == 19
    assert v["b-edge"].result == "keep"


def test_a_stack_at_2x_the_leaders_time_is_screened_out() -> None:
    rows = batch_rows("b-lead", "lead", 1000.0) + batch_rows("b-slow", "slow", 2000.0)
    v = verdicts(rows)
    assert v["b-slow"].result == "screened out"
    assert "200.0% of the leader" in v["b-slow"].reason


def test_timeouts_count_at_the_limit_in_the_medians() -> None:
    # 1 timeout among 21 trials of 100 s/task: task 0's trials are 1800, 100, 100
    rows = batch_rows("b-t", "t", 700.0, timeouts=1)
    v = verdicts(rows, timeout=1800.0)
    assert round(v["b-t"].sum_medians, 1) == 700.0
    assert v["b-t"].passes == 20


def test_a_batch_missing_a_task_is_not_screened() -> None:
    rows = [r for r in batch_rows("b-part", "part", 700.0) if r["task"] != "replay-6"]
    assert verdicts(rows) == {}


def test_excluded_rows_do_not_count() -> None:
    rows = batch_rows("b-x", "x", 700.0)
    rows[0]["excluded"] = True
    rows[0]["passed"] = False
    v = verdicts(rows)
    assert (v["b-x"].passes, v["b-x"].rows) == (20, 20)


def test_a_stacks_runs_pool_into_one_row() -> None:
    # Three GLM runs at 1,856.9 / 1,945.2 / 1,994.1 s must not fill every kept
    # place: they are one stack, ranked once.
    rows = (
        CLUSTER
        + batch_rows("b-glm2", "glm53exl3", 1945.2)
        + batch_rows("b-glm3", "glm53exl3", 1994.1)
    )
    v = {x.backend: x for x in ss.screen(rows, TASKS, client="opencode")}
    assert v["glm53exl3"].runs == 3
    assert (v["glm53exl3"].passes, v["glm53exl3"].rows) == (63, 63)
    assert round(v["glm53exl3"].sum_medians, 1) == 1945.2
    assert [x.backend for x in v.values() if x.result == "keep"][:3] == [
        "glm53exl3",
        "dsv4vision",
        "qwen38fnnvfp4",
    ]
