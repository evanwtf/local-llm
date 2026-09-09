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

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

import metal_knob as mk
import metal_knob_ab as driver


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


def test_run_engagement_writes_count_file(tmp_path):
    """The engagement count must land in a file, not on the function's stdout,
    so a progress echo cannot pollute the captured value."""
    import subprocess

    tree = tmp_path / "tree"
    tree.mkdir()
    stub = tree / "ds4-bench"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "for i in $(seq 1 5); do\n"
        "  echo 'ds4: packed FA use=1 max_threads=256 tew=0 tgmem=0 need=0' >&2\n"
        "done\n"
    )
    stub.chmod(0o755)

    out = tmp_path / "out"
    out.mkdir()

    script = (
        pathlib.Path(__file__).resolve().parents[1] / "scripts" / "metal_knob_ab.sh"
    )
    py = script.parent / "lib" / "metal_knob.py"
    # Source only the run_engagement function, then run it against the stub.
    bash = (
        f"eval \"$(sed -n '/^run_engagement()/,/^}}/p' {script})\"\n"
        f"OUT={out} PY={py} KNOB=gathered-heads TREE={tree} GGUF=stub.gguf "
        f"PROMPT=stub.txt CTX_START=2048 STEP=2048 GEN=128 run_engagement on unset\n"
    )
    subprocess.run(["bash", "-c", bash], check=True, capture_output=True)
    count = (out / "engagement-on.count").read_text().strip()
    assert count == "5"


def test_relative_out_is_absolutized(tmp_path):
    """#203: a relative OUT must become absolute before the lock, or the arm's
    `( cd "$TREE" && ... )` resolves the --csv path under the ds4 tree and the
    CSV files land nowhere."""
    import subprocess

    script = (
        pathlib.Path(__file__).resolve().parents[1] / "scripts" / "metal_knob_ab.sh"
    )
    bash = (
        f"eval \"$(sed -n '/^absolutize_out()/,/^}}/p' {script})\"\n"
        f"cd {tmp_path}\n"
        f"OUT=rel/out\n"
        f"absolutize_out\n"
        f"printf '%s' \"$OUT\"\n"
    )
    got = subprocess.run(
        ["bash", "-c", bash], check=True, capture_output=True, text=True
    ).stdout
    assert got == f"{tmp_path}/rel/out"


def test_absolute_out_is_unchanged(tmp_path):
    """An already-absolute OUT must pass through untouched."""
    import subprocess

    script = (
        pathlib.Path(__file__).resolve().parents[1] / "scripts" / "metal_knob_ab.sh"
    )
    bash = (
        f"eval \"$(sed -n '/^absolutize_out()/,/^}}/p' {script})\"\n"
        f"cd {tmp_path}\n"
        f"OUT=/abs/path\n"
        f"absolutize_out\n"
        f"printf '%s' \"$OUT\"\n"
    )
    got = subprocess.run(
        ["bash", "-c", bash], check=True, capture_output=True, text=True
    ).stdout
    assert got == "/abs/path"


# --- print-based admission (ds4#952's diagnostic opt-ins) --------------------
#
# c1909040 and bbf5a796 add two Metal knobs gated to Apple M5 that announce
# themselves on stderr exactly once when the path is admitted. That is a
# stronger signal than either kind the driver had: the line appears only when
# the kernel is dispatched, so the off arm must produce none at all rather than
# merely fewer.


def test_payload_reuse_carries_a_print_admission_signal():
    knob = "q4-mpp-payload-reuse"
    assert mk.admission_signal(knob) == "print"
    assert mk.has_admission_signal(knob)
    assert mk.on_var(knob) == "DS4_METAL_ENABLE_Q4_MPP_PAYLOAD_REUSE"
    # Opt-in, default off, so the off arm is the same var set to 0.
    assert mk.off_var(knob) == "DS4_METAL_ENABLE_Q4_MPP_PAYLOAD_REUSE"
    assert mk.admission_pattern(knob) == "Metal Q4 MPP payload reuse admitted"
    # No trace var: the engine prints unprompted. The driver builds the trace
    # assignment conditionally because `env VAR=1` with an empty VAR is `=1`,
    # which kills the arm.
    assert mk.trace_var(knob) == ""


def test_cooperative_source_has_no_signal_because_its_line_is_a_compile_notice():
    """Both knobs print a line and only one of them means anything.

    cooperative-source prints from the Metal library compile block, on device
    and env alone -- it says the shader was built with the macro, not that any
    Q4_K dense matmul took the path. Listing it as an admission print would let
    a run that never dispatched the kernel once read as verified, which is the
    exact result this table exists to refuse.
    """
    knob = "q4-mpp-cooperative"
    assert mk.admission_signal(knob) == "none"
    assert not mk.has_admission_signal(knob)
    assert mk.admission_print(knob) == ""
    assert mk.admission_pattern(knob) == ""
    # So it cannot be measured by accident.
    with pytest.raises(SystemExit):
        mk.validate(knob, "1", "0")
    mk.validate(knob, "1", "0", acknowledge_no_signal=True)


def test_print_admission_requires_the_off_arm_to_be_silent():
    assert mk.print_admission_ok(48, 0) is True
    assert mk.print_admission_ok(1, 0) is True
    # The off arm printed: the knob did not turn the path off, so both arms
    # ran the same kernel and the comparison is between two identical arms.
    assert mk.print_admission_ok(48, 1) is False
    # The on arm never engaged: nothing was measured.
    assert mk.print_admission_ok(0, 0) is False
    # This is strictly stronger than the count rule, which would accept both.
    assert mk.count_admission_ok(48, 1) is True
    assert mk.print_admission_ok(48, 1) is False


def test_counting_takes_a_pattern_and_defaults_to_the_trace(tmp_path):
    log = tmp_path / "arm.log"
    log.write_text(
        "ds4: packed FA use=1\n"
        "ds4: Q4 MPP cooperative source enabled (diagnostic)\n"
        "ds4: packed FA use=2\n"
        "unrelated\n"
    )
    assert mk.count_trace_lines(log) == 2
    assert mk.count_trace_lines(log, "Q4 MPP cooperative source enabled") == 1
    assert mk.count_trace_lines(log, "Metal Q4 MPP payload reuse admitted") == 0


def test_counting_an_empty_pattern_is_refused(tmp_path):
    """Every line contains the empty string, so an empty pattern would report
    the log's line count as an engagement count -- a knob that never engaged
    would read as engaging on every line of output."""
    log = tmp_path / "arm.log"
    log.write_text("one\ntwo\n")
    with pytest.raises(ValueError):
        mk.count_trace_lines(log, "")


def test_a_print_knob_is_not_refused_for_lacking_a_signal():
    """validate() refuses a knob with no admission signal unless acknowledged.
    A print knob has one, so it must pass without METAL_KNOB_ACK_NO_SIGNAL."""
    mk.validate("q4-mpp-payload-reuse", "1", "0")


# ------------------------------------------------- the ported driver (#235)


def test_the_two_arm_spellings_describe_the_same_arm() -> None:
    """`arm_cmd` is for the shell, `arm_env` is for Python. One table.

    The shell interpolated `arm_cmd`'s output into `env $env_prefix
    ./ds4-bench ...` UNQUOTED, relying on `-u VAR` splitting into two words
    and `VAR=value` into one. That worked because both values came from this
    table -- it was one hand-passed value away from not working. Python asks
    the same table for `(env, unset)` instead, and this is what stops the two
    answers drifting.
    """
    for knob in mk.KNOBS:
        for label in ("on", "off"):
            cmd = mk.arm_cmd(knob, label, "7")
            env, unset = mk.arm_env(knob, label, "7")
            if cmd.startswith("-u "):
                assert env == {} and unset == [cmd[3:]], (knob, label)
            else:
                var, _, value = cmd.partition("=")
                assert env == {var: value} and unset == [], (knob, label)


def test_only_the_presence_knobs_on_arm_unsets() -> None:
    """`=0` still counts as set, so an assignment here is two identical arms.

    `gathered-heads` is on by default and has no REQUIRE spelling: the branch
    tests `getenv(...) != NULL`, so setting the DISABLE var to `0` takes the
    raw-only path in BOTH arms and produces two arms wearing different labels
    and the same numbers.
    """
    unsetting = {
        (knob, label)
        for knob in mk.KNOBS
        for label in ("on", "off")
        if mk.arm_env(knob, label, "1")[1]
    }
    assert unsetting == {(knob, "on") for knob in mk.KNOBS if mk.presence(knob)}
    assert unsetting, "gathered-heads is a presence knob; something is wrong"


def test_a_bad_arm_label_is_refused_by_both_spellings() -> None:
    with pytest.raises(SystemExit):
        mk.arm_cmd("gathered-heads", "onn", "1")
    with pytest.raises(SystemExit):
        mk.arm_env("gathered-heads", "onn", "1")


def test_the_driver_refuses_an_odd_rep_count(tmp_path, monkeypatch) -> None:
    """Refused, not warned. Alternation cancels position bias only when even.

    At REPS=3 reps 1 and 3 run A-first and only rep 2 runs B-first, so the
    bias lands 2:1 on one arm. Across #171's twelve reps whichever arm ran
    first was faster in 9, median +0.9%, +5.9% on the first rep of a cold
    session. An odd sweep produces a complete CSV and a plausible number with
    no sign that half the design is missing.
    """

    def explode(*a, **k):
        raise AssertionError("a refused run must not reach the machine")

    monkeypatch.setattr(driver.child, "run", explode)
    rc = driver.main(
        [
            "gathered-heads",
            "1",
            "0",
            str(tmp_path / "tree"),
            str(tmp_path / "m.gguf"),
            str(tmp_path / "out"),
            "--reps",
            "3",
        ]
    )
    assert rc != 0, "an odd rep count must not exit 0"


def test_the_driver_never_calls_pgrep() -> None:
    source = (ROOT / "scripts" / "metal_knob_ab.py").read_text()
    assert "pgrep" not in source and "pkill" not in source
