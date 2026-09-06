"""#162 Task 4: the metal_knob_ab driver's refusal paths.

The negative cases are the whole job: an empty value is a wrong arm waiting to
happen (the three helpers disagree on the empty string), an unknown knob is a
typo that would otherwise run a different experiment, a fail-closed error on
the on arm means the knob did not take effect, a default-on knob whose off arm
is `REQUIRE=0` leaves the cache running and both arms identical, and a knob
with no admission signal is refused unless explicitly acknowledged. The
driver's measurement itself is verified by the evidence artifact, not by a
unit test.
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
    """A default-off knob's off arm must be '0'; any other value is a different arm."""
    with pytest.raises(SystemExit, match="off value"):
        mk.validate("session-union", "1", "2")


def test_valid_arm_accepts():
    mk.validate("session-union", "1", "0")


def test_every_known_knob_accepts_a_valid_arm():
    for knob in mk.KNOBS:
        off = "1" if mk.KNOBS[knob]["default_on"] else "0"
        # A presence knob's on arm unsets the var, so it carries the sentinel.
        on = "unset" if mk.presence(knob) else "1"
        # A knob with no admission signal needs the explicit acknowledgment.
        mk.validate(
            knob, on, off, acknowledge_no_signal=not mk.has_admission_signal(knob)
        )


def test_no_signal_knob_refused_without_ack():
    """stream-overlap has no REQUIRE spelling, so it has no admission signal.
    Without an explicit acknowledgment it must be refused, or the driver would
    produce a clean, tight, meaningless result indistinguishable from 'the knob
    does nothing'."""
    with pytest.raises(SystemExit, match="no admission signal"):
        mk.validate("stream-overlap", "1", "0")


def test_no_signal_knob_accepts_with_ack():
    mk.validate("stream-overlap", "1", "0", acknowledge_no_signal=True)


def test_admission_signal_values():
    """The three REQUIRE knobs carry a fail-closed check; gathered-heads carries
    a count check; stream-overlap has none. The value goes on the run so an
    unverified knob cannot be read as verified."""
    for knob in ("session-union", "iq2", "exact-rows"):
        assert mk.admission_signal(knob) == "fail-closed"
    assert mk.admission_signal("gathered-heads") == "count"
    assert mk.admission_signal("stream-overlap") == "none"


def test_default_on_knob_off_value_zero_refused():
    """exact-rows is on by default, so REQUIRE=0 leaves the cache running and
    both arms identical. The off arm must be a nonzero DISABLE value."""
    with pytest.raises(SystemExit, match="on by default"):
        mk.validate("exact-rows", "1", "0")


def test_default_on_knob_valid_off_arm_accepts():
    mk.validate("exact-rows", "1", "1")


def test_presence_knob_assignment_on_arm_refused():
    """gathered-heads is presence-based: the on arm must unset the DISABLE var
    (`env -u`), not assign it. `=0` still counts as set and would take the
    raw-only path in both arms, so an assignment on arm is a wrong arm by
    construction."""
    with pytest.raises(SystemExit, match="presence-based"):
        mk.validate("gathered-heads", "1", "1")


def test_presence_knob_unset_sentinel_accepts():
    """The on arm carries the sentinel 'unset' (the driver's `${2:?on value}`
    needs a non-empty positional); the off arm sets the DISABLE var nonzero.
    gathered-heads carries a count admission signal, so no ack is needed."""
    mk.validate("gathered-heads", "unset", "1")


def test_presence_knob_off_value_zero_refused():
    """gathered-heads is on by default, so the off arm must be a nonzero
    DISABLE value, not `0`."""
    with pytest.raises(SystemExit, match="on by default"):
        mk.validate("gathered-heads", "unset", "0")


def test_presence_knob_has_count_admission_signal():
    """gathered-heads has no REQUIRE spelling, so it cannot fail closed. It
    carries a count-based admission signal instead: the on arm must engage more
    trace lines than the off arm. It must not need the acknowledgment."""
    assert mk.admission_signal("gathered-heads") == "count"
    assert mk.has_admission_signal("gathered-heads") is True
    mk.validate("gathered-heads", "unset", "1")


def test_arm_cmd_presence_on_unsets():
    """The on arm of a presence knob must unset the var, not assign it. This is
    the branch the shell drives: `env -u VAR`, never `VAR=value`."""
    cmd = mk.arm_cmd("gathered-heads", "on", "unset")
    assert cmd == "-u DS4_METAL_DISABLE_DECODE_RAW_GATHERED_ATTN"
    assert "=" not in cmd


def test_arm_cmd_presence_off_assigns():
    assert mk.arm_cmd("gathered-heads", "off", "1") == (
        "DS4_METAL_DISABLE_DECODE_RAW_GATHERED_ATTN=1"
    )


def test_arm_cmd_non_presence_assigns():
    assert mk.arm_cmd("session-union", "on", "1") == (
        "DS4_METAL_REQUIRE_Q4_SSD_SESSION_UNION=1"
    )
    assert mk.arm_cmd("session-union", "off", "0") == (
        "DS4_METAL_REQUIRE_Q4_SSD_SESSION_UNION=0"
    )


def test_arm_cmd_unknown_label_refused():
    with pytest.raises(SystemExit, match="arm label"):
        mk.arm_cmd("session-union", "middle", "1")


def test_default_on_knob_off_var_differs_from_on_var():
    """A default-on knob's off arm must use the DISABLE var, not the REQUIRE
    var. If they were the same, the off arm would set REQUIRE=0, which does
    not turn the cache off. A presence knob is the exception: its on arm unsets
    the same DISABLE var the off arm sets, so on_var == off_var is correct."""
    for meta in mk.KNOBS.values():
        if meta["default_on"] and not meta.get("presence", False):
            assert meta["off_var"] != meta["on_var"]
        else:
            assert meta["off_var"] == meta["on_var"]


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


def test_on_var_is_the_require_spelling():
    """The on arm sets the REQUIRE spelling, not a bare enable."""
    assert mk.on_var("session-union") == "DS4_METAL_REQUIRE_Q4_SSD_SESSION_UNION"
    assert mk.on_var("iq2") == "DS4_METAL_REQUIRE_IQ2_XXS_SSD_PREFILL_MM"
    assert mk.on_var("exact-rows") == "DS4_METAL_REQUIRE_EXACT_ROWS_PERSISTENT_CACHE"
    assert mk.on_var("stream-overlap") == "DS4_METAL_ENABLE_Q4_STREAM_OVERLAP"


def test_off_var_is_the_disable_spelling_for_default_on():
    """exact-rows' off arm is the DISABLE var, not the REQUIRE var."""
    assert mk.off_var("exact-rows") == "DS4_METAL_DISABLE_EXACT_ROWS_PERSISTENT_CACHE"
    assert mk.off_var("session-union") == "DS4_METAL_REQUIRE_Q4_SSD_SESSION_UNION"


def test_trace_var():
    assert (
        mk.trace_var("gathered-heads")
        == "DS4_METAL_TRACE_M5_FLASH_ATTN_PACKED32_REDUCE"
    )
    assert mk.trace_var("stream-overlap") == ""


def test_count_trace_lines(tmp_path):
    log = tmp_path / "on.log"
    log.write_text(
        "ds4: packed FA use=1 max_threads=256 tew=0 tgmem=0 need=0\n"
        "ds4: packed FA use=1 max_threads=256 tew=0 tgmem=0 need=0\n"
        "ds4: ready\n"
    )
    assert mk.count_trace_lines(log) == 2


def test_count_trace_lines_empty(tmp_path):
    log = tmp_path / "on.log"
    log.write_text("ds4: ready\n")
    assert mk.count_trace_lines(log) == 0


def test_count_admission_ok():
    assert mk.count_admission_ok(43, 41) is True


def test_count_admission_equal_refused():
    assert mk.count_admission_ok(41, 41) is False


def test_count_admission_zero_refused():
    assert mk.count_admission_ok(43, 0) is False
    assert mk.count_admission_ok(0, 0) is False
