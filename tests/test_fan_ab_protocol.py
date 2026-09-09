"""The #276 protocol, pinned so a run is reproducible from its own record.

The protocol was agreed in words:

    1. max fans until temperature plateaus (idle GPU)
    2. fans auto
    3. begin test
    4. end test
    5. max fans until temp reaches plateau
    6. begin test
    7. end test
    8. let temp reach idle plateau
    9. fans auto

These tests assert the driver still performs that, in that order. A protocol
that lives only in a loop drifts silently: an edit that reorders two lines
changes the experiment and nothing fails.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import fan_ab


def test_a_segment_is_auto_then_max_in_that_order() -> None:
    """Order is not cosmetic: it is what interleaving means.

    Running both autos before both maxes lets a linear drift align with
    condition, which is the failure the alternation exists to prevent.
    """
    assert fan_ab.ARMS == ("auto", "max")


def test_three_segments_because_a_claim_needs_three() -> None:
    """One segment is one paired comparison, and one pair concludes nothing."""
    assert fan_ab.SEGMENTS == 3
    assert len(fan_ab.PHASES) == fan_ab.SEGMENTS * len(fan_ab.ARMS)


def test_the_phases_alternate_and_never_repeat_a_condition() -> None:
    """A,B,A,B,A,B -- no two adjacent phases share a condition."""
    assert fan_ab.PHASES == ("auto", "max", "auto", "max", "auto", "max")
    for first, second in zip(fan_ab.PHASES, fan_ab.PHASES[1:], strict=False):
        assert first != second


def test_the_bulk_cooldown_runs_on_max_for_every_phase() -> None:
    """Step 1 and step 5 are the same step, and neither is the arm's mode.

    Cooling on the phase's own mode would have the auto arm settle at a higher
    floor than the max arm, putting a temperature difference between the
    conditions at t=0 -- the thing the cooldown exists to remove.
    """
    assert fan_ab.COOL_ON_MAX is True


def test_no_settle_in_before_a_sweep() -> None:
    """Both arms leave the same floor and start at once.

    A hold on the arm's own mode warms the auto arm while the max arm sits
    still, which re-creates the asymmetry cooling on max removed. See the
    constant's own comment -- it looked careful and was the largest threat to
    the comparison.
    """
    assert fan_ab.SETTLE_IN_S == 0


def test_the_protocol_is_recorded_verbatim_and_has_nine_steps() -> None:
    """The manifest carries this, so a run describes itself to a stranger."""
    assert len(fan_ab.PROTOCOL) == 9
    assert fan_ab.PROTOCOL[0].startswith("1.")
    assert "max fans" in fan_ab.PROTOCOL[0]
    assert fan_ab.PROTOCOL[-1] == "9. fans auto"


def test_the_settle_ceiling_is_long_enough_to_reach_idle() -> None:
    """420 s was not: the die was still falling at 0.809 C/min when it expired.

    Fitting the observed decay (floor ~31.5 C, tau ~3.2 min) puts the bound at
    ~610 s, so a ceiling below that would silently reintroduce phases that
    began from unequal states.
    """
    assert fan_ab.SETTLE_TIMEOUT_S >= 700


def test_the_gate_is_a_slope_and_the_bound_is_the_measured_noise_floor() -> None:
    """0.30 C/min is the p90 of a settled die's own slope, not a guess.

    A difference-of-means bar of 0.3 C over 30 s would be 36 C/hour restated,
    which nobody would choose deliberately.
    """
    assert fan_ab.SETTLE_MAX_SLOPE == 0.3
    assert fan_ab.SETTLE_WINDOW_S == 180
