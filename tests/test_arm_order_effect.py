"""The position effect must be measured from the run directory alone."""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import arm_order_effect as aoe

HEADER = "ctx_tokens,prefill_tps,gen_steady_tps\n"


def write_csv(path: pathlib.Path, rows: dict[int, float], mtime: float) -> None:
    path.write_text(HEADER + "".join(f"{k},{v},{v}\n" for k, v in rows.items()))
    os.utime(path, (mtime, mtime))


def test_parse_name_reads_arm_and_rep():
    got = aoe.parse_name(pathlib.Path("head-20d5dff6-rep3.csv"))
    assert got == ("head-20d5dff6", 3)


def test_parse_name_ignores_a_file_that_is_not_a_rep():
    assert aoe.parse_name(pathlib.Path("engines.txt")) is None
    assert aoe.parse_name(pathlib.Path("summary.csv")) is None


def test_order_comes_from_mtime_not_from_the_name(tmp_path):
    """Alphabetical order is not run order, and alternating reps prove it.

    In an even rep the B arm runs first. Sorting by name would call it second
    and flip the sign of the effect this script exists to measure.
    """
    write_csv(tmp_path / "base-x-rep2.csv", {8192: 100.0}, mtime=2000)
    write_csv(tmp_path / "head-y-rep2.csv", {8192: 110.0}, mtime=1000)  # ran FIRST
    rows = aoe.position_ratios(tmp_path, "prefill_tps")
    assert len(rows) == 1
    rep, first_arm, ratio = rows[0]
    assert rep == 2
    assert first_arm == "head-y", "mtime says head ran first; the name says otherwise"
    assert ratio == pytest.approx(1.1), "110 first / 100 second"


def test_a_rep_with_one_arm_is_dropped(tmp_path):
    """Half a pair is not a slower half."""
    write_csv(tmp_path / "base-x-rep1.csv", {8192: 100.0}, mtime=1000)
    assert aoe.position_ratios(tmp_path, "prefill_tps") == []


def test_only_shared_frontiers_are_paired(tmp_path):
    """A frontier one arm never reached cannot contribute a ratio."""
    write_csv(tmp_path / "base-x-rep1.csv", {8192: 100.0, 16384: 100.0}, mtime=1000)
    write_csv(tmp_path / "head-y-rep1.csv", {8192: 200.0}, mtime=2000)
    _, _, ratio = aoe.position_ratios(tmp_path, "prefill_tps")[0]
    assert ratio == pytest.approx(0.5), "only the 8192 pair is shared"


def test_no_position_effect_reads_as_exactly_one(tmp_path):
    """A guard that always reports an effect is not a guard."""
    write_csv(tmp_path / "base-x-rep1.csv", {8192: 100.0, 16384: 90.0}, mtime=1000)
    write_csv(tmp_path / "head-y-rep1.csv", {8192: 100.0, 16384: 90.0}, mtime=2000)
    _, _, ratio = aoe.position_ratios(tmp_path, "prefill_tps")[0]
    assert ratio == pytest.approx(1.0)


def test_a_zero_rate_does_not_divide(tmp_path):
    """A dead arm writes zeros; dividing by one raises instead of reporting."""
    write_csv(tmp_path / "base-x-rep1.csv", {8192: 100.0}, mtime=1000)
    write_csv(tmp_path / "head-y-rep1.csv", {8192: 0.0}, mtime=2000)
    assert aoe.position_ratios(tmp_path, "prefill_tps") == []


def test_the_column_is_selectable(tmp_path):
    """#130's rule is about both halves of the A/B, not just prefill."""
    (tmp_path / "base-x-rep1.csv").write_text(
        "ctx_tokens,prefill_tps,gen_steady_tps\n8192,100,50\n"
    )
    os.utime(tmp_path / "base-x-rep1.csv", (1000, 1000))
    (tmp_path / "head-y-rep1.csv").write_text(
        "ctx_tokens,prefill_tps,gen_steady_tps\n8192,200,25\n"
    )
    os.utime(tmp_path / "head-y-rep1.csv", (2000, 2000))
    assert aoe.position_ratios(tmp_path, "prefill_tps")[0][2] == pytest.approx(0.5)
    assert aoe.position_ratios(tmp_path, "gen_steady_tps")[0][2] == pytest.approx(2.0)


def test_recorded_order_wins_over_mtime(tmp_path):
    """decode_ab.sh records run order; a recorded position is not a guess.

    mtime is a proxy -- a copied or restored evidence directory can carry
    mtimes that have nothing to do with when the arms ran. When the harness
    wrote the order down, use it. Here the mtimes say base ran first and the
    recorded order says head did; the recorded order must win.
    """
    write_csv(tmp_path / "base-x-rep1.csv", {8192: 100.0}, mtime=1000)
    write_csv(tmp_path / "head-y-rep1.csv", {8192: 110.0}, mtime=2000)
    (tmp_path / "run-order.txt").write_text(
        "rep=1 position=1 of 2 label=head-y\nrep=1 position=2 of 2 label=base-x\n"
    )
    _rep, first_arm, ratio = aoe.position_ratios(tmp_path, "prefill_tps")[0]
    assert first_arm == "head-y", "run-order.txt says head ran first"
    assert ratio == pytest.approx(1.1), "110 first / 100 second"


def test_mtime_is_the_fallback_when_no_order_was_recorded(tmp_path):
    """decode_ab_engine.sh writes no run-order.txt; inference beats nothing."""
    write_csv(tmp_path / "base-x-rep1.csv", {8192: 100.0}, mtime=1000)
    write_csv(tmp_path / "head-y-rep1.csv", {8192: 110.0}, mtime=2000)
    assert not (tmp_path / "run-order.txt").exists()
    _, first_arm, _ = aoe.position_ratios(tmp_path, "prefill_tps")[0]
    assert first_arm == "base-x"


def test_a_partly_recorded_rep_falls_back_rather_than_mixing(tmp_path):
    """Half a recorded rep is worse than none -- a position and an mtime do
    not sort against each other, and silently mixing them would order a rep by
    comparing 1.0 against 1757000000.0."""
    write_csv(tmp_path / "base-x-rep1.csv", {8192: 100.0}, mtime=1000)
    write_csv(tmp_path / "head-y-rep1.csv", {8192: 110.0}, mtime=2000)
    (tmp_path / "run-order.txt").write_text("rep=1 position=2 of 2 label=head-y\n")
    _, first_arm, _ = aoe.position_ratios(tmp_path, "prefill_tps")[0]
    assert first_arm == "base-x", "incomplete record must fall back to mtime"


def test_a_malformed_order_line_is_ignored(tmp_path):
    """A truncated write must not be read as an order."""
    assert aoe.recorded_order.__doc__
    (tmp_path / "run-order.txt").write_text("rep=1 position=\nnot an order line\n")
    assert aoe.recorded_order(tmp_path) == {}
