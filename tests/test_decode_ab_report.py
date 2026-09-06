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


def test_arms_with_different_rep_counts_warn(tmp_path, caplog):
    """A run read mid-write has one arm with fewer reps than the other, and
    load() pairs only the reps both arms share -- so the missing rep drops
    out silently and changes the median. The warning is the loud version."""
    d = tmp_path / "midwrite"
    d.mkdir()
    _write_rep(d, "a", 1, {2048: 10.0})
    _write_rep(d, "a", 2, {2048: 10.0})
    _write_rep(d, "a", 3, {2048: 10.0})
    _write_rep(d, "b", 1, {2048: 12.0})
    _write_rep(d, "b", 2, {2048: 12.0})
    with caplog.at_level("WARNING"):
        runs, status = report.report_across_runs([d], "gen_steady_tps")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "different repetition counts" in text
    assert "a has [1, 2, 3]" in text
    assert "b has [1, 2]" in text
    # The run still summarizes; the warning does not refuse it.
    assert len(runs) == 1
    assert status == 0


def test_balanced_arms_do_not_warn(tmp_path, caplog):
    """A complete run has the same reps on both arms; the guard must stay
    silent so a normal run does not read as suspect."""
    d = tmp_path / "balanced"
    d.mkdir()
    _write_rep(d, "a", 1, {2048: 10.0})
    _write_rep(d, "a", 2, {2048: 10.0})
    _write_rep(d, "b", 1, {2048: 12.0})
    _write_rep(d, "b", 2, {2048: 12.0})
    with caplog.at_level("WARNING"):
        report.report_across_runs([d], "gen_steady_tps")
    assert not caplog.records


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


def test_both_directions_are_named_in_the_per_rep_line(tmp_path, caplog):
    """The file already warns that a bare ratio has been misread the wrong
    way round once. Printing one direction reintroduced that."""
    d = _write_run(tmp_path, "dir", {1: 1.25, 2: 1.25})
    with caplog.at_level("INFO"):
        report.log_within_run_structure(report.load(d))
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "b/a" in text and "a/b" in text
    assert "1.250" in text and "0.800" in text


def test_the_arm_that_drifts_most_is_named(tmp_path, caplog):
    """ds4#952's claim was about WHICH arm moves more, and two signed
    percentages are easy to eyeball backwards."""
    d = tmp_path / "d"
    d.mkdir()
    head = "ctx_tokens,prefill_tps,gen_steady_tps\n"
    for rep, av in ((1, 10.0), (2, 5.0)):
        (d / f"a-rep{rep}.csv").write_text(head + f"2048,100.0,{av}\n")
        (d / f"b-rep{rep}.csv").write_text(head + "2048,100.0,10.0\n")
    with caplog.at_level("INFO"):
        report.log_within_run_structure(report.load(d))
    assert "arm that drifts most: a" in " ".join(r.getMessage() for r in caplog.records)


def test_the_between_run_block_names_both_directions(tmp_path, caplog):
    """Same reason as the per-rep line: a bare ratio has been misread once."""
    runs = []
    for name, r in (("r1", 1.25), ("r2", 1.25)):
        d = _write_run(tmp_path, name, {1: r, 2: r})
        runs.append((d, report.summarize(report.load(d))))
    with caplog.at_level("INFO"):
        report.log_between_run_spread(runs)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "b/a 1.250" in text and "a/b 0.800" in text


def test_legacy_median_divides_two_independent_medians(tmp_path):
    """The pre-98bc79b statistic, kept only so #136 can measure against it."""
    d = tmp_path / "legacy"
    d.mkdir()
    head = "ctx_tokens,prefill_tps,gen_steady_tps\n"
    # arm a: 10, 20, 30 -> median 20.  arm b: 40, 10, 10 -> median 10.
    for rep, (av, bv) in enumerate([(10.0, 40.0), (20.0, 10.0), (30.0, 10.0)], start=1):
        (d / f"a-rep{rep}.csv").write_text(head + f"2048,100.0,{av}\n")
        (d / f"b-rep{rep}.csv").write_text(head + f"2048,100.0,{bv}\n")
    data = report.load(d)
    assert abs(report.legacy_median(data) - 0.5) < 1e-9
    # The paired statistic pairs within each rep: 4.0, 0.5, 0.333 -> median 0.5
    # here by coincidence of this fixture; what matters is that they are
    # computed differently, which the next test pins.
    assert report.legacy_median(data) is not None


def test_legacy_and_paired_disagree_when_arms_drift_apart(tmp_path):
    """The whole point of the fix: the two medians can come from different
    repetitions, so drift re-enters the answer."""
    d = tmp_path / "drift"
    d.mkdir()
    head = "ctx_tokens,prefill_tps,gen_steady_tps\n"
    # a: 10,10,40 -> median 10.   b: 10,40,40 -> median 40.
    # ratio of medians = 4.0; paired ratios are 1.0, 4.0, 1.0 -> median 1.0.
    for rep, (av, bv) in enumerate([(10.0, 10.0), (10.0, 40.0), (40.0, 40.0)], start=1):
        (d / f"a-rep{rep}.csv").write_text(head + f"2048,100.0,{av}\n")
        (d / f"b-rep{rep}.csv").write_text(head + f"2048,100.0,{bv}\n")
    data = report.load(d)
    paired = report.summarize(data).median
    assert abs(paired - 1.0) < 1e-9
    assert abs(report.legacy_median(data) - 4.0) < 1e-9


def test_legacy_needs_two_arms():
    assert report.legacy_median({"only": {2048: {1: 1.0}}}) is None


def test_runs_needed_collapses_as_k_rises():
    """#136 item 2: what k runs would have bought."""
    got = report.runs_needed([1.10, 1.20, 1.15, 1.16])
    assert [row[0] for row in got] == [1, 2, 3, 4]
    # k=1 spans the full range of single runs
    assert abs(got[0][1] - 1.10) < 1e-9 and abs(got[0][2] - 1.20) < 1e-9
    # k=n is one estimate, so no spread at all
    assert got[-1][3] == 0.0
    # and the spread must not grow as runs are added
    spreads = [row[3] for row in got]
    assert spreads == sorted(spreads, reverse=True)


def test_one_outlier_is_absorbed_by_three_runs():
    """The #118 shape: three tight, one 3.5 pp out. A median over any three
    lands in the tight cluster, which is why three was enough there."""
    got = {row[0]: row[3] for row in report.runs_needed([1.16, 1.21, 1.17, 1.17])}
    assert got[1] > got[3]
    assert got[3] < 1.0  # under a point of spread by k=3


def test_runs_needed_handles_a_single_run():
    got = report.runs_needed([1.2])
    assert got == [(1, 1.2, 1.2, 0.0)]


def test_the_block_is_silent_below_three_runs(tmp_path, caplog):
    """Two runs cannot say what three would have bought."""
    runs = []
    for name, r in (("r1", 1.10), ("r2", 1.20)):
        d = _write_run(tmp_path, name, {1: r, 2: r})
        runs.append((d, report.summarize(report.load(d))))
    with caplog.at_level("INFO"):
        report.log_runs_needed(runs)
    assert not caplog.records


def test_the_quotable_line_always_carries_a_run_count(tmp_path):
    """#136 item 3: a figure with no run count cannot be read. Make the
    quotable form cheaper than the bare number so the count travels."""
    runs = []
    for name, r in (("r1", 1.10), ("r2", 1.20), ("r3", 1.15)):
        d = _write_run(tmp_path, name, {1: r, 2: r})
        runs.append((d, report.summarize(report.load(d))))
    line = report.quotable(runs)
    assert "over 3 runs" in line
    assert "spread" in line
    assert "1.100" in line and "1.200" in line


def test_a_single_run_quote_says_it_is_not_a_measurement(tmp_path):
    """The one case where the number must arrive with a warning attached."""
    d = _write_run(tmp_path, "only", {1: 1.10, 2: 1.10})
    runs = [(d, report.summarize(report.load(d)))]
    line = report.quotable(runs)
    assert "SINGLE run" in line
    assert "not a measurement" in line
    assert "over 1 runs" not in line


def test_quotable_names_the_direction():
    """A bare ratio has been misread the wrong way round once already."""
    assert report.quotable([]) == "no runs"


# ---------------------------------------------------------------------------
# #140: the prompt is an input to a prefill figure, so it belongs on the quote.


def _prompt_dir(tmp_path, name: str, prompt: str, size: int) -> pathlib.Path:
    """A complete one-frontier run whose CSVs carry a prompt stamp."""
    import prompt_meta

    d = tmp_path / name
    d.mkdir()
    _write_rep(d, "q4", 1, {2048: 30.0})
    _write_rep(d, "q8", 1, {2048: 27.0})
    src = tmp_path / prompt
    if not src.exists():
        src.write_bytes(b"x" * size)
    for csv_path in d.glob("*-rep*.csv"):
        prompt_meta.stamp(csv_path, src)
    return d


def test_the_quote_names_the_prompt(tmp_path):
    d = _prompt_dir(tmp_path, "run1", "promessi_sposi.txt", 1329139)
    runs, _ = report.report_across_runs([d], "prefill_tps")
    line = report.quotable(runs, report.prompt_for(runs))
    assert "promessi_sposi.txt" in line
    assert "1298 KiB" in line


def test_the_quote_says_so_when_no_run_recorded_the_prompt(tmp_path):
    d = tmp_path / "run1"
    d.mkdir()
    _write_rep(d, "q4", 1, {2048: 30.0})
    _write_rep(d, "q8", 1, {2048: 27.0})
    runs, _ = report.report_across_runs([d], "prefill_tps")
    line = report.quotable(runs, report.prompt_for(runs))
    assert "NOT RECORDED" in line


def test_runs_on_different_prompts_are_not_pooled_into_one_quote(tmp_path):
    """@adamlawi's whole point: two prompts, 2.4 pp apart, same binaries."""
    short = _prompt_dir(tmp_path, "run1", "short.txt", 135000)
    long_ = _prompt_dir(tmp_path, "run2", "long.txt", 405000)
    runs, _ = report.report_across_runs([short, long_], "prefill_tps")
    assert report.prompt_for(runs) is None
    line = report.quotable(runs, report.prompt_for(runs))
    assert "NOT RECORDED" in line or "different prompts" in line


def test_prompt_for_reports_the_one_prompt_several_runs_share(tmp_path):
    a = _prompt_dir(tmp_path, "run1", "p.txt", 1000)
    b = _prompt_dir(tmp_path, "run2", "p.txt", 1000)
    runs, _ = report.report_across_runs([a, b], "prefill_tps")
    ref = report.prompt_for(runs)
    assert ref is not None and ref.name == "p.txt"


# -- voided runs (#162) ------------------------------------------------------
#
# Run 2 of the #952 batch caught another session's pytest suite on arm A and
# not arm B, and was written off in a markdown file the pooling tool could not
# read. The confound flattered the hypothesis, so a report that had pooled it
# would have looked entirely healthy. These assert the rule as an artifact.


def _void(d: pathlib.Path, reason: str = "# Run is VOID - asymmetric load") -> None:
    """Mark a run directory void the way the batch does: a sibling file."""
    (d.parent / f"{d.name}{report.VOID_SUFFIX}").write_text(reason + "\n\nbody\n")


def test_a_voided_run_is_refused_not_pooled(tmp_path, caplog):
    good = _write_run(tmp_path, "run1", {1: 1.10, 2: 1.10})
    bad = _write_run(tmp_path, "run2", {1: 2.00, 2: 2.00})
    _void(bad)
    with caplog.at_level("ERROR"):
        runs, status = report.report_across_runs([good, bad], "gen_steady_tps")
    assert [d.name for d, _ in runs] == ["run1"]
    # Other runs are still usable, so the exit status stays clean.
    assert status == 0
    assert "REFUSED run2" in caplog.text


def test_the_refusal_carries_the_reason_from_the_marker(tmp_path, caplog):
    """A refusal with no reason is one someone will override blind."""
    bad = _write_run(tmp_path, "run2", {1: 1.0})
    _void(bad, "# pytest from another session landed on arm A")
    good = _write_run(tmp_path, "run1", {1: 1.1, 2: 1.1})
    with caplog.at_level("ERROR"):
        report.report_across_runs([good, bad], "gen_steady_tps")
    assert "pytest from another session landed on arm A" in caplog.text
    # The leading '#' of the markdown heading is not part of the reason.
    assert "-- pytest" in caplog.text


def test_a_marker_inside_the_directory_also_voids_the_run(tmp_path, caplog):
    """Whoever voids a run should not have to guess which spelling is read."""
    bad = _write_run(tmp_path, "run2", {1: 1.0})
    (bad / "VOID.md").write_text("# thermals, not the change under test\n")
    with caplog.at_level("ERROR"):
        runs, status = report.report_across_runs([bad], "gen_steady_tps")
    assert runs == []
    assert status == 1
    assert "thermals" in caplog.text


def test_every_run_void_is_a_failure_not_a_silent_pass(tmp_path):
    """No output must not be readable as nothing wrong."""
    bad = _write_run(tmp_path, "run2", {1: 1.0})
    _void(bad)
    runs, status = report.report_across_runs([bad], "gen_steady_tps")
    assert runs == []
    assert status == 1


def test_a_voided_run_cannot_move_the_quoted_number(tmp_path):
    """The property that matters: the headline is the same either way.

    The void arm here reads 2.000 against the others' 1.100. If it leaked
    into the pool the median and the run count both move, and the quotable
    line -- the thing that gets pasted into an issue -- would carry it.
    """
    dirs = [_write_run(tmp_path, f"run{i}", {1: 1.10, 2: 1.10}) for i in (1, 3, 4)]
    bad = _write_run(tmp_path, "run2", {1: 2.00, 2: 2.00})
    _void(bad)
    clean, _ = report.report_across_runs(dirs, "gen_steady_tps")
    with_void, _ = report.report_across_runs([*dirs, bad], "gen_steady_tps")
    assert report.quotable(with_void) == report.quotable(clean)
    assert "over 3 runs" in report.quotable(with_void)


def test_include_void_pools_it_and_says_so(tmp_path, caplog):
    """An override exists for inspecting one, and it is never quiet."""
    good = _write_run(tmp_path, "run1", {1: 1.10, 2: 1.10})
    bad = _write_run(tmp_path, "run2", {1: 2.00, 2: 2.00})
    _void(bad)
    with caplog.at_level("WARNING"):
        runs, status = report.report_across_runs(
            [good, bad], "gen_steady_tps", include_void=True
        )
    assert [d.name for d, _ in runs] == ["run1", "run2"]
    assert status == 0
    assert "do not quote this" in caplog.text


def test_a_run_with_no_marker_is_untouched(tmp_path):
    """The guard must not fire on the normal case."""
    good = _write_run(tmp_path, "run1", {1: 1.10, 2: 1.10})
    assert report.void_marker(good) is None


# -- the per-frontier table belongs to the batch, not to run 1 ---------------


def test_per_frontier_across_runs_collects_every_run(tmp_path):
    dirs = [
        _write_run(tmp_path, "run1", {1: 1.20}),
        _write_run(tmp_path, "run2", {1: 1.00}),
        _write_run(tmp_path, "run3", {1: 1.10}),
    ]
    got = [(d, report.summarize(report.load(d))) for d in dirs]
    across = report.per_frontier_across_runs(got)
    assert sorted(across[2048]) == pytest.approx([1.00, 1.10, 1.20])


def test_the_multi_run_table_does_not_report_run_ones_number(tmp_path, caplog):
    """The defect this exists for: run 1 said -1.8%, four runs said -0.1%.

    Run 1 here is the outlier at 1.200 and the median over the three runs is
    1.100. Both appear, and the single-run table must be labelled as such.
    """
    dirs = [
        _write_run(tmp_path, "run1", {1: 1.20}),
        _write_run(tmp_path, "run2", {1: 1.00}),
        _write_run(tmp_path, "run3", {1: 1.10}),
    ]
    got = [(d, report.summarize(report.load(d))) for d in dirs]
    with caplog.at_level("INFO"):
        report.log_per_frontier_across_runs(got)
    assert "median over 3 runs" in caplog.text
    assert "paired b/a" in caplog.text
    assert "1.100" in caplog.text
    # The range across runs is on the row, so a reader cannot take the median
    # for a tight number.
    assert "1.000 - 1.200" in caplog.text


def test_the_per_frontier_block_is_silent_for_one_run(tmp_path, caplog):
    """With one directory the detail table below already is the whole report."""
    d = _write_run(tmp_path, "run1", {1: 1.10, 2: 1.10})
    got = [(d, report.summarize(report.load(d)))]
    with caplog.at_level("INFO"):
        report.log_per_frontier_across_runs(got)
    assert caplog.text == ""


def test_a_frontier_missing_from_a_run_is_not_filled_in(tmp_path):
    """The run count on the row must say how many runs stand behind it."""
    d1 = _write_run(tmp_path, "run1", {1: 1.20})
    d2 = tmp_path / "run2"
    d2.mkdir()
    head = "ctx_tokens,prefill_tps,gen_steady_tps\n"
    (d2 / "a-rep1.csv").write_text(head + "2048,100.0,10.0\n4096,100.0,10.0\n")
    (d2 / "b-rep1.csv").write_text(head + "2048,100.0,11.0\n4096,100.0,11.0\n")
    got = [(d, report.summarize(report.load(d))) for d in (d1, d2)]
    across = report.per_frontier_across_runs(got)
    assert len(across[2048]) == 2
    assert len(across[4096]) == 1
