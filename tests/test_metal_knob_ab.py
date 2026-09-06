"""#162 Task 4: the metal_knob_ab driver's refusal paths.

The negative cases are the whole job: an empty value is a wrong arm waiting to
happen (the three helpers disagree on the empty string), an unknown knob is a
typo that would otherwise run a different experiment, and a fail-closed error
on the on arm means the knob did not take effect. The driver's measurement
itself is verified by the evidence artifact, not by a unit test.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import metal_knob_ab as mk


def test_unknown_knob_refused():
    with pytest.raises(SystemExit, match="unknown knob"):
        mk.validate("bogus", "1", "0")


def test_empty_on_value_refused():
    with pytest.raises(SystemExit, match="on value"):
        mk.validate("session-union", "", "0")


def test_empty_off_value_refused():
    with pytest.raises(SystemExit, match="off value"):
        mk.validate("session-union", "1", "")


def test_on_value_zero_refused():
    """The on arm must be nonzero; '0' is the off arm."""
    with pytest.raises(SystemExit, match="on value"):
        mk.validate("session-union", "0", "0")


def test_off_value_not_zero_refused():
    """The off arm must be '0'; any other value is a different arm."""
    with pytest.raises(SystemExit, match="off value"):
        mk.validate("session-union", "1", "2")


def test_valid_arm_accepts():
    mk.validate("session-union", "1", "0")


def test_every_known_knob_accepts_a_valid_arm():
    for knob in mk.KNOBS:
        mk.validate(knob, "1", "0")


def test_fail_closed_error_detected(tmp_path):
    log = tmp_path / "on.log"
    log.write_text("ds4: required Metal Q4 SSD session union is ineligible\n")
    assert mk.check_fail_closed("session-union", log) is True


def test_fail_closed_error_absent(tmp_path):
    log = tmp_path / "on.log"
    log.write_text("ds4: ready\n")
    assert mk.check_fail_closed("session-union", log) is False


def test_stream_overlap_has_no_fail_closed_check(tmp_path):
    """stream-overlap has no REQUIRE spelling, so no error can fail it."""
    log = tmp_path / "on.log"
    log.write_text("anything at all\n")
    assert mk.check_fail_closed("stream-overlap", log) is False


def test_require_knobs_have_error_strings():
    """The three REQUIRE knobs each carry a fail-closed error; stream-overlap
    does not. A knob with an empty error would silently skip the admission
    check, which is the absence-of-error signal being assumed rather than
    checked."""
    for knob in ("session-union", "iq2", "exact-rows"):
        assert mk.fail_closed_error(knob), f"{knob} must have a fail-closed error"
    assert mk.fail_closed_error("stream-overlap") == ""


def test_env_var_is_the_require_spelling():
    """The on arm sets the REQUIRE spelling, not a bare enable."""
    assert mk.env_var("session-union") == "DS4_METAL_REQUIRE_Q4_SSD_SESSION_UNION"
    assert mk.env_var("iq2") == "DS4_METAL_REQUIRE_IQ2_XXS_SSD_PREFILL_MM"
    assert mk.env_var("exact-rows") == "DS4_METAL_REQUIRE_EXACT_ROWS_PERSISTENT_CACHE"
    assert mk.env_var("stream-overlap") == "DS4_METAL_ENABLE_Q4_STREAM_OVERLAP"
