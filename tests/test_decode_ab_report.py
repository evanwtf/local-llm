"""Tests for the decode A/B report statistic.

#118 made this load-bearing. The script called its result "the paired median
ratio" while computing median(b) / median(a) -- a ratio of two independent
medians. Repetitions drift (~9% between reps on #118's own data), the two
medians can come from different repetitions, and the drift re-enters the
headline as noise: #118's first report said +20.0% where the paired statistic
on the same data says +16.5%. Pairing is the whole point of running both arms
inside the same repetition, so it is asserted here rather than trusted.

The fixture values are #118's real ctx-2048 numbers, the frontier where the
defect was largest.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import decode_ab_report as report

HEADER = (
    "ctx_tokens,prefill_tokens,prefill_tps,gen_tokens,gen_tps,gen_first_ms,"
    "gen_steady_tokens,gen_steady_tps,kvcache_bytes"
)


def _write_rep(tmp_path, label: str, rep: int, by_ctx: dict[int, float]) -> None:
    lines = [HEADER]
    for ctx, tps in by_ctx.items():
        # prefill_tps is written distinct (10x) so a column mix-up cannot
        # pass silently: the decode statistic and the prefill statistic read
        # different values from the same row.
        lines.append(f"{ctx},2048,{tps * 10},128,{tps},0.0,127,{tps},0")
    (tmp_path / f"{label}-rep{rep}.csv").write_text("\n".join(lines) + "\n")


def _write_964_fixture(tmp_path) -> None:
    """main/pr964 at ctx 2048, exactly as measured in #118 reps 1-3."""
    _write_rep(tmp_path, "main", 1, {2048: 30.22})
    _write_rep(tmp_path, "main", 2, {2048: 27.42})
    _write_rep(tmp_path, "main", 3, {2048: 27.50})
    _write_rep(tmp_path, "pr964", 1, {2048: 36.40})
    _write_rep(tmp_path, "pr964", 2, {2048: 33.71})
    _write_rep(tmp_path, "pr964", 3, {2048: 32.17})


def test_load_keeps_the_repetition_index(tmp_path):
    """The pairing lives in the rep index; losing it is the original defect."""
    _write_964_fixture(tmp_path)
    data = report.load(tmp_path)
    assert data["main"][2048] == {1: 30.22, 2: 27.42, 3: 27.50}


def test_the_ratio_is_paired_within_a_repetition(tmp_path):
    """rep-2 branch over rep-2 baseline, not rep 2 over rep 3.

    The ratio of arm medians on this fixture is 33.71/27.50 = 1.226, which
    divides rep 3's baseline by rep 2's branch. The paired median is 1.2045.
    """
    _write_964_fixture(tmp_path)
    got = report.summarize(report.load(tmp_path))
    assert got.per_frontier[2048] == pytest.approx(1.2045, abs=1e-3)
    assert got.per_frontier[2048] != pytest.approx(1.2258, abs=1e-3)


def test_missing_reps_pair_only_what_both_arms_have(tmp_path):
    """A rep present in one arm only cannot be paired; it must not enter."""
    _write_rep(tmp_path, "a", 1, {2048: 10.0})
    _write_rep(tmp_path, "a", 2, {2048: 10.0})
    _write_rep(tmp_path, "b", 1, {2048: 12.0})
    got = report.summarize(report.load(tmp_path))
    assert got.per_frontier[2048] == pytest.approx(1.2)
    assert got.n_pairs == 1


def test_a_frontier_with_no_shared_rep_is_skipped_not_crashed(tmp_path):
    _write_rep(tmp_path, "a", 1, {2048: 10.0, 4096: 10.0})
    _write_rep(tmp_path, "b", 1, {2048: 12.0})
    _write_rep(tmp_path, "b", 2, {4096: 12.0})
    got = report.summarize(report.load(tmp_path))
    assert got.skipped == [4096]
    assert 2048 in got.per_frontier


def test_nothing_in_common_is_a_named_error_not_a_crash(tmp_path):
    """Two arms that never shared a repetition have no statistic to report."""
    _write_rep(tmp_path, "a", 1, {2048: 10.0})
    _write_rep(tmp_path, "b", 2, {2048: 12.0})
    with pytest.raises(ValueError, match="no frontier"):
        report.summarize(report.load(tmp_path))


def test_the_column_selects_decode_or_prefill(tmp_path):
    """The prefill claim of an A/B is checked with the same paired statistic."""
    _write_rep(tmp_path, "a", 1, {2048: 10.0})
    _write_rep(tmp_path, "b", 1, {2048: 12.0})
    gen = report.load(tmp_path, "gen_steady_tps")
    prefill = report.load(tmp_path, "prefill_tps")
    assert gen["b"][2048][1] == 12.0
    assert prefill["b"][2048][1] == 120.0


def test_pooled_stats_cover_every_paired_point(tmp_path):
    _write_rep(tmp_path, "a", 1, {2048: 10.0, 4096: 10.0})
    _write_rep(tmp_path, "a", 2, {2048: 10.0, 4096: 10.0})
    _write_rep(tmp_path, "b", 1, {2048: 12.0, 4096: 11.0})
    _write_rep(tmp_path, "b", 2, {2048: 12.0, 4096: 11.0})
    got = report.summarize(report.load(tmp_path))
    assert got.n_pairs == 4
    assert got.pooled_median == pytest.approx(1.15)
    assert got.pooled_mean == pytest.approx(1.15)
    assert got.wins == 2  # both frontiers faster on b


def _write_run(tmp_path, name, ratios_by_rep):
    """A minimal A/B directory: two arms, one frontier, N reps."""
    d = tmp_path / name
    d.mkdir()
    head = "ctx_tokens,prefill_tps,gen_steady_tps\n"
    for rep, ratio in ratios_by_rep.items():
        (d / f"a-rep{rep}.csv").write_text(head + "2048,100.0,10.0\n")
        (d / f"b-rep{rep}.csv").write_text(head + f"2048,100.0,{10.0 * ratio}\n")
    return d


def test_repeat_spread_measures_reps_not_frontiers(tmp_path):
    """#136: the comparator must not use the spread across context lengths,
    which is a real dependence on ctx and would flatter every run."""
    d = _write_run(tmp_path, "r1", {1: 1.10, 2: 1.20, 3: 1.15})
    got = [(d, report.summarize(report.load(d)))]
    spread = report.repeat_spread(got)
    assert spread is not None
    assert abs(spread - 0.10) < 1e-9


def test_repeat_spread_is_none_without_enough_reps(tmp_path):
    d = _write_run(tmp_path, "single", {1: 1.10})
    got = [(d, report.summarize(report.load(d)))]
    assert report.repeat_spread(got) is None


def test_between_run_spread_is_reported_for_several_dirs(tmp_path, caplog):
    """#136: the whole point is that this axis is invisible from one run."""
    runs = []
    for name, r in (("r1", 1.10), ("r2", 1.20)):
        d = _write_run(tmp_path, name, {1: r, 2: r, 3: r})
        runs.append((d, report.summarize(report.load(d))))
    with caplog.at_level("INFO"):
        report.log_between_run_spread(runs)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "2 runs" in text
    assert "10.0 pp" in text


def test_one_directory_reports_no_between_run_line(caplog, tmp_path):
    d = _write_run(tmp_path, "only", {1: 1.1, 2: 1.1})
    got = [(d, report.summarize(report.load(d)))]
    with caplog.at_level("INFO"):
        report.log_between_run_spread(got)
    assert not caplog.records


def test_a_bad_directory_does_not_lose_the_others(tmp_path, caplog):
    """With four runs in hand, losing three to one bad directory is wrong."""
    good = _write_run(tmp_path, "good", {1: 1.1, 2: 1.1})
    bad = tmp_path / "empty"
    bad.mkdir()
    with caplog.at_level("ERROR"):
        runs, status = report.report_across_runs([good, bad], "gen_steady_tps")
    assert len(runs) == 1
    assert status == 1


def test_per_rep_ratio_pairs_within_each_rep(tmp_path):
    """#952 claimed the ratio narrows within a session. Answering that by
    hand is how a ratio-of-medians slips back in."""
    d = _write_run(tmp_path, "r", {1: 1.10, 2: 1.20, 3: 1.15})
    got = report.per_rep_ratio(report.load(d))
    assert [round(got[r], 3) for r in (1, 2, 3)] == [1.10, 1.20, 1.15]


def test_per_arm_drift_is_per_frontier_not_a_ratio_of_medians(tmp_path):
    """The shape of this question invites the exact defect 98bc79b fixed."""
    d = tmp_path / "drift"
    d.mkdir()
    head = "ctx_tokens,prefill_tps,gen_steady_tps\n"
    # arm a halves between rep1 and rep3 at both frontiers; arm b is flat.
    for rep, val in ((1, 10.0), (2, 8.0), (3, 5.0)):
        (d / f"a-rep{rep}.csv").write_text(
            head + f"2048,100.0,{val}\n4096,100.0,{val}\n"
        )
        (d / f"b-rep{rep}.csv").write_text(head + "2048,100.0,10.0\n4096,100.0,10.0\n")
    drift = report.per_arm_drift(report.load(d))
    assert abs(drift["a"] - 0.5) < 1e-9
    assert abs(drift["b"] - 1.0) < 1e-9


def test_drift_needs_two_reps():
    assert report.per_arm_drift({"a": {2048: {1: 10.0}}}) == {}


def test_per_rep_ratio_is_empty_unless_there_are_two_arms():
    assert report.per_rep_ratio({"only": {2048: {1: 1.0}}}) == {}
