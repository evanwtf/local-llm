"""Reading a `fan_ab.py` run (#276).

Analysis code that silently produces a plausible wrong number is the class
this repo tests first. Every case here is a number that looked fine.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import fan_ab_report as far


def phase(values: dict[int, dict[int, float]]) -> dict[int, dict[int, float]]:
    return values


def test_a_truncated_rep_is_dropped_rather_than_divided_by() -> None:
    """The aborted run of 2026-09-09, which produced a -5.5% drift from a rep
    that never finished.

    A killed sweep writes a short CSV, and it is always the LAST rep -- the one
    `per_arm_drift` divides by the first. Nothing about -5.5% looked wrong.
    """
    data = {
        "01-auto": {
            2048: {1: 44.0, 2: 42.0},
            4096: {1: 41.0, 2: 39.0},
            6144: {1: 40.0},
            8192: {1: 39.0},
        }
    }
    got = far.drop_partial_reps(data)
    reps = {r for ctx in got["01-auto"].values() for r in ctx}
    assert reps == {1}, "rep 2 covered 2 frontiers against 4; it is not a rep"


def test_a_complete_run_loses_nothing() -> None:
    """The guard must not eat data from a run that finished properly."""
    data = {"01-auto": {2048: {1: 44.0, 2: 43.0}, 4096: {1: 41.0, 2: 40.0}}}
    got = far.drop_partial_reps(data)
    assert got == data


def test_the_ratio_is_a_median_of_ratios_not_a_ratio_of_medians() -> None:
    """The defect corrected in 98bc79b, which this question invites back.

    Constructed so the two answers differ: per-frontier ratios are 1.1, 1.3,
    1.1 (median 1.1) while the medians are 200 and 260 (ratio 1.3). A ratio of
    medians would report a 30% effect where the typical frontier saw 10%.
    """
    data = {
        "01-auto": {2048: {1: 100.0}, 4096: {1: 200.0}, 6144: {1: 300.0}},
        "02-max": {2048: {1: 110.0}, 4096: {1: 260.0}, 6144: {1: 330.0}},
    }
    got = far.paired_ratio(data, "01-auto", "02-max")
    assert got is not None
    assert abs(got - 1.1) < 1e-9, f"median of ratios is 1.1, got {got}"


def test_pairs_are_adjacent_in_time_and_never_mix_two_autos() -> None:
    """Positional pairing is why the run interleaves at all.

    All-A against all-B does not cancel a linear drift; adjacent pairs do.
    """
    labels = ["01-auto", "02-max", "03-auto", "04-max", "05-auto", "06-max"]
    assert far.pairs(labels) == [
        ("01-auto", "02-max"),
        ("03-auto", "04-max"),
        ("05-auto", "06-max"),
    ]


def test_a_run_cut_short_yields_fewer_pairs_not_a_mismatched_one() -> None:
    """A phase that never ran must remove a pair, not shift the pairing.

    Pairing by position after a gap would compare an auto phase against a max
    phase from a different part of the run, which is precisely the drift the
    interleave exists to cancel.
    """
    assert far.pairs(["01-auto", "02-max", "03-auto"]) == [("01-auto", "02-max")]
    assert far.pairs(["01-auto"]) == []
    assert far.pairs(["02-max", "03-auto"]) == [], "max->auto is not a pair"


def test_a_label_that_is_not_a_phase_is_refused() -> None:
    assert far.condition_of("01-auto") == "auto"
    assert far.condition_of("06-max") == "max"
    assert far.condition_of("base") is None
    assert far.condition_of("01-turbo") is None, "only max and auto exist"
    assert far.index_of("03-auto") == 3
    assert far.index_of("base") is None


def test_a_cooldown_timeout_is_surfaced_not_averaged_away(tmp_path) -> None:
    """A phase that began on a still-cooling machine is not comparable.

    The manifest records which way each wait ended precisely so a reader does
    not have to assume they all succeeded.
    """
    out = tmp_path
    (out / "fan-ab-manifest.json").write_text(
        json.dumps(
            {
                "phases": [
                    {
                        "phase": 1,
                        "cooldown": {"outcome": "plateau"},
                        "start_die_c": 37.1,
                    },
                    {
                        "phase": 2,
                        "cooldown": {"outcome": "timeout"},
                        "start_die_c": 61.4,
                    },
                ]
            }
        )
    )
    manifest = far.manifest_of(out)
    assert far.cooldown_outcomes(manifest) == {1: "plateau", 2: "timeout"}
    assert far.start_die(manifest) == {1: 37.1, 2: 61.4}


def test_a_missing_manifest_is_not_fatal_and_invents_no_temperature(tmp_path) -> None:
    """Temperatures absent must read as absent, never as zero."""
    assert far.manifest_of(tmp_path) is None
    assert far.cooldown_outcomes(None) == {}
    assert far.start_die(None) == {}
