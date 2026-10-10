"""Tests for the interleaved-series read-out (#970).

The rule decides which download a public doc recommends, so the tests weight
the refusals: a challenger with one fewer pass, or one just over the
slowdown ceiling, must not win; a pass that edited the tests is a failure.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import series_readout


def row(task: str, wall: float, *, passed: bool = True, **extra: Any) -> dict[str, Any]:
    return {
        "backend": extra.pop("backend", "a"),
        "task": task,
        "wall_seconds": wall,
        "passed": passed,
        "output_tokens": 100,
        **extra,
    }


def write(
    tmp_path: pathlib.Path, name: str, rows: list[dict[str, Any]]
) -> pathlib.Path:
    path = tmp_path / f"{name}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_sum_of_medians_is_per_task_median_summed() -> None:
    rows = [row("t1", 10), row("t1", 30), row("t1", 20), row("t2", 5)]
    assert series_readout.sum_of_medians(rows) == 25.0


def test_a_pass_that_touched_the_tests_is_a_failure(tmp_path: pathlib.Path) -> None:
    run = series_readout.read_run(
        write(tmp_path, "a", [row("t1", 10), row("t2", 10, touched_tests=True)])
    )
    assert series_readout.arm_stats("a", [run]).passes == 1


def test_excluded_rows_are_dropped(tmp_path: pathlib.Path) -> None:
    run = series_readout.read_run(
        write(tmp_path, "a", [row("t1", 10), row("t1", 999, excluded=True)])
    )
    assert len(run.rows) == 1


def stats(passes: int, total: float, trials: int = 42) -> series_readout.ArmStats:
    return series_readout.ArmStats(
        name="x",
        trials=trials,
        passes=passes,
        run_passes=[passes],
        sum_of_medians=total,
        run_sums_of_medians=[total],
        median_trial=1.0,
        output_tokens=0,
        tasks=frozenset({"t"}),
    )


@pytest.mark.parametrize(
    ("b_passes", "b_total", "expected"),
    [
        (42, 1000.0, True),  # equal passes, faster
        (42, 1050.0, True),  # exactly at the 5% ceiling
        (42, 1050.1, False),  # just over the ceiling
        (41, 900.0, False),  # one fewer pass loses even when faster
        (43, 1049.0, True),  # more passes, inside the ceiling
    ],
)
def test_the_rule(b_passes: int, b_total: float, expected: bool) -> None:
    a = stats(42, 1000.0)
    assert series_readout.rule_picks_b(a, stats(b_passes, b_total), 0.05) is expected


def test_the_rule_compares_pass_rates_when_run_counts_differ() -> None:
    """#1023: one run of B against three of A compared 42 passes with 123,
    so a perfect B could never win."""
    a = stats(123, 3951.8, trials=126)
    assert series_readout.rule_picks_b(a, stats(42, 3738.2, trials=42), 0.05)
    # 40/42 (95.2%) is below 123/126 (97.6%): still a loss
    assert not series_readout.rule_picks_b(a, stats(40, 3738.2, trials=42), 0.05)


def test_two_backends_in_one_arm_are_refused(tmp_path: pathlib.Path) -> None:
    run = series_readout.read_run(
        write(tmp_path, "a", [row("t1", 10), row("t1", 10, backend="other")])
    )
    with pytest.raises(ValueError, match="more than one backend"):
        series_readout.arm_stats("a", [run])


def test_main_refuses_arms_with_different_tasks(tmp_path: pathlib.Path) -> None:
    a = write(tmp_path, "a", [row("t1", 10)])
    b = write(tmp_path, "b", [row("t2", 10, backend="b")])
    assert series_readout.main(["--arm", f"A={a}", "--arm", f"B={b}"]) == 2


def test_main_exit_status_names_the_pick(tmp_path: pathlib.Path) -> None:
    a = write(tmp_path, "a", [row("t1", 100), row("t2", 100)])
    faster = write(
        tmp_path, "b", [row("t1", 90, backend="b"), row("t2", 90, backend="b")]
    )
    failing = write(
        tmp_path,
        "c",
        [row("t1", 50, backend="b"), row("t2", 50, passed=False, backend="b")],
    )
    assert series_readout.main(["--arm", f"A={a}", "--arm", f"B={faster}"]) == 0
    assert series_readout.main(["--arm", f"A={a}", "--arm", f"B={failing}"]) == 1


def test_an_empty_run_file_is_refused(tmp_path: pathlib.Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(ValueError, match="no usable rows"):
        series_readout.read_run(empty)


def test_a_timeout_counts_at_the_full_limit(tmp_path: pathlib.Path) -> None:
    """A timed-out row has no wall; dropping it or reading None would hide the
    slowest trial of the arm (#977)."""
    timed_out = {"backend": "a", "task": "t1", "error": "timeout", "passed": None}
    run = series_readout.read_run(write(tmp_path, "a", [row("t2", 10), timed_out]))
    stats = series_readout.arm_stats("a", [run], timeout=1800.0)
    assert stats.sum_of_medians == 1810.0
    assert stats.passes == 1
