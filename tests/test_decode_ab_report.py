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