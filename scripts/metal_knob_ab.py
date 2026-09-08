"""Knob table and refusal logic for scripts/metal_knob_ab.sh (#162 Task 4).

The driver varies one Metal knob between two arms of the same tree and GGUF.
This module is the single source of truth for the knob table and the refusal
paths, so the negative cases are testable without running a measurement.

Each knob has two env vars: the var that turns it on and the var that turns it
off. For the three opt-in knobs (default off) they are the same variable, set
to a nonzero value to enable and `0` to disable. For exact-rows they are not:
the persistent cache is on by default, so `REQUIRE=0` means "not required" and
leaves the cache running. Its off arm is the DISABLE var, which the source
names "the A/B rollback arm and always wins" (ds4_metal.m:14828 at ds4-pr952 77a054e1). A knob whose
off arm does not change the default is a wrong arm waiting to happen, so
`validate()` refuses it.

gathered-heads is the first presence-based knob. The branch routes n_comp==0
layers to the gathered-heads path unless `DS4_METAL_DISABLE_DECODE_RAW_GATHERED_ATTN`
is set (ds4_metal.m:38408 at ds4-pr952 77a054e1, `getenv(...) != NULL`), so the feature is on by
default and there is no REQUIRE spelling to force it. The on arm must *unset*
the DISABLE var (`env -u`), not assign it: `=0` still counts as set and would
take the raw-only path in both arms. So for a presence knob `on_var == off_var`
(the same DISABLE var), and `validate()` refuses an assignment on arm instead
of the `off_var != on_var` rule that guards the assignment-based exact-rows
shape.

One knob suffices even though two variables touch the n_comp==0 layers. The
second, `DS4_METAL_DISABLE_DECODE_RAW_PACKED32` (ds4_metal.m:36774 at ds4-pr952 77a054e1), relaxes
`packed_shape` for n_comp==0 layers, but setting GATHERED_ATTN routes those
layers raw-only, so `packed_shape` never applies to them and the relaxation is
moot. For n_comp!=0 layers, `n_comp != 0u` is already true on main, so the
relaxation changes nothing there either. One variable fully reverts the
observable difference for this model.

The three helpers disagree on the empty string (metal_graph_tp_env_flag returns
the default, ds4_gpu_exact_rows_persistent_env_enabled returns false,
ds4_gpu_env_bool returns on), so an empty value is refused. The refusal runs
before the driver takes the lock or exports anything, so no helper ever sees an
empty value.

The on arm uses the REQUIRE spelling and fails the run if the fail-closed error
string appears. There is no positive admission print, so absence of the error is
the only admission signal and it must be checked, not assumed. stream-overlap
has no REQUIRE spelling, so it has no admission signal at all: its policy gate
(ds4.c:71917 at ds4-pr952 77a054e1) has seven terms, and any of count, resident, ssd_streaming or
quality can veto the path with no output. A knob with no admission signal is
refused unless the caller passes an explicit acknowledgment, and its rows are
marked `admission_signal: "none"` so they cannot later be read as verified.

gathered-heads has no REQUIRE spelling, so it cannot fail closed. Instead it
carries a count-based admission signal: the driver runs one short
single-frontier engagement pass per arm with
`DS4_METAL_TRACE_M5_FLASH_ATTN_PACKED32_REDUCE` set, counts the `packed FA use=`
trace lines, and refuses the timed run unless the on arm engaged more layers
than the off arm and both are non-zero. Equal counts mean the knob did nothing,
which is the tight-meaningless result the driver refuses. The counts go on the
run so every run carries its own engagement evidence.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys

logger = logging.getLogger(__name__)

# knob -> on var, off var, whether it is on by default, the fail-closed error
# string (empty = no REQUIRE spelling), and whether the on arm unsets rather
# than assigns. For the opt-in knobs the off var is the on var set to `0`; for
# exact-rows it is the DISABLE var, because the cache is on by default and
# REQUIRE=0 leaves it running. For gathered-heads it is the DISABLE var too,
# and the on arm unsets it (presence-based, no REQUIRE spelling).
TRACE_PATTERN = "packed FA use="

KNOBS: dict[str, dict[str, str | bool]] = {
    "session-union": {
        "on_var": "DS4_METAL_REQUIRE_Q4_SSD_SESSION_UNION",
        "off_var": "DS4_METAL_REQUIRE_Q4_SSD_SESSION_UNION",
        "default_on": False,
        "fail_error": "required Metal Q4 SSD session union is ineligible",
    },
    "iq2": {
        "on_var": "DS4_METAL_REQUIRE_IQ2_XXS_SSD_PREFILL_MM",
        "off_var": "DS4_METAL_REQUIRE_IQ2_XXS_SSD_PREFILL_MM",
        "default_on": False,
        "fail_error": (
            "required Metal IQ2_XXS SSD grouped address-MM path was not selected"
        ),
    },
    "exact-rows": {
        "on_var": "DS4_METAL_REQUIRE_EXACT_ROWS_PERSISTENT_CACHE",
        "off_var": "DS4_METAL_DISABLE_EXACT_ROWS_PERSISTENT_CACHE",
        "default_on": True,
        "fail_error": (
            "Metal exact-row persistent cache is required but disabled or ineligible"
        ),
    },
    "stream-overlap": {
        "on_var": "DS4_METAL_ENABLE_Q4_STREAM_OVERLAP",
        "off_var": "DS4_METAL_ENABLE_Q4_STREAM_OVERLAP",
        "default_on": False,
        "fail_error": "",
    },
    "gathered-heads": {
        "on_var": "DS4_METAL_DISABLE_DECODE_RAW_GATHERED_ATTN",
        "off_var": "DS4_METAL_DISABLE_DECODE_RAW_GATHERED_ATTN",
        "default_on": True,
        "fail_error": "",
        "presence": True,
        "trace_var": "DS4_METAL_TRACE_M5_FLASH_ATTN_PACKED32_REDUCE",
    },
    # ds4#952's two diagnostic opt-ins (c1909040, bbf5a796), both gated to
    # Apple M5. They print similar-looking lines and the lines mean different
    # things, which is the whole reason only one of them carries a signal.
    #
    # cooperative-source prints from the Metal library COMPILE block
    # (ds4_metal.m:7900 at ds4-pr952 ff749b84), beside the tensor-API line and
    # inside the same `macros[...] = @"1"` stanza. It fires on device and env
    # alone -- it says the shader was compiled with the macro, not that any
    # Q4_K dense matmul ever took the path. Treating it as admission would let
    # a run that never dispatched the kernel once read as verified, which is
    # the tight-meaningless result this table exists to refuse. So: no signal,
    # and METAL_KNOB_ACK_NO_SIGNAL is required to measure it.
    #
    # There is no dispatch-level signal to use instead. When cooperative is on,
    # the kernel is swapped by a compile macro rather than selected at the call
    # site, so nothing prints and nothing counts.
    "q4-mpp-cooperative": {
        "on_var": "DS4_METAL_ENABLE_Q4_MPP_COOPERATIVE_SOURCE",
        "off_var": "DS4_METAL_ENABLE_Q4_MPP_COOPERATIVE_SOURCE",
        "default_on": False,
        "fail_error": "",
    },
    # payload-reuse is the opposite case and does carry a real signal. Its
    # line comes from ds4_gpu_q4_mpp_payload_reuse_admitted(), called from the
    # two dispatch sites (ds4_metal.m:23719 and 33273 at ds4-pr952 ff749b84)
    # under `weight_type == DS4_METAL_TENSOR_Q4_K && !cooperative && enabled`.
    # It fires only when the path is actually taken, so an off arm that prints
    # it means the knob did not turn the path off.
    #
    # Note that guard's middle term: the two knobs are MUTUALLY EXCLUSIVE.
    # With cooperative on, payload reuse never engages, so they cannot be
    # measured together and a combined arm would measure only cooperative.
    "q4-mpp-payload-reuse": {
        "on_var": "DS4_METAL_ENABLE_Q4_MPP_PAYLOAD_REUSE",
        "off_var": "DS4_METAL_ENABLE_Q4_MPP_PAYLOAD_REUSE",
        "default_on": False,
        "fail_error": "",
        "admission_print": "Metal Q4 MPP payload reuse admitted",
    },
}


def has_admission_signal(knob: str) -> bool:
    """Whether a run of this knob carries an admission check.

    A knob with no REQUIRE spelling has no fail-closed error, but it may still
    carry a count-based check (gathered-heads) or no check at all
    (stream-overlap).
    """
    return admission_signal(knob) != "none"


def admission_signal(knob: str) -> str:
    """The admission signal a run of this knob carries.

    'fail-closed' when the on arm is checked for the REQUIRE error; 'print'
    when the on arm must emit an admission line the off arm never emits;
    'count' when the on arm must engage more trace lines than the off arm;
    'none' when there is no check. The value goes on the run so a later reader
    cannot read an unverified knob as verified.
    """
    if fail_closed_error(knob):
        return "fail-closed"
    if admission_print(knob):
        return "print"
    if trace_var(knob):
        return "count"
    return "none"


def validate(
    knob: str,
    on_value: str,
    off_value: str,
    acknowledge_no_signal: bool = False,
) -> None:
    """Refuse a wrong arm before the driver takes the lock or measures.

    The on arm must be nonzero, except for a presence knob whose on arm unsets
    the var (`env -u`) and so must carry no value at all. The off arm must
    actually turn the knob off: for a default-off knob that is `0` on the on
    var; for a default-on knob it is a nonzero value on the DISABLE var, and
    the off var must differ from the on var unless the knob is presence-based
    (where the on arm unsets the same DISABLE var the off arm sets). A
    default-on knob whose off arm is `REQUIRE=0` leaves the cache running and
    both arms identical, so it is refused.

    A knob with no admission signal is refused unless the caller acknowledges
    it. Without the acknowledgment the driver would produce a clean, tight,
    meaningless result indistinguishable from "the knob does nothing".
    """
    if knob not in KNOBS:
        raise SystemExit(
            f"REFUSING: unknown knob '{knob}' (known: {', '.join(sorted(KNOBS))})"
        )
    meta = KNOBS[knob]
    presence = bool(meta.get("presence", False))
    if presence:
        # The on arm is `env -u`, so an assignment on arm is a wrong arm by
        # construction: `=0` still counts as set and would take the raw-only
        # path in both arms. The sentinel "unset" marks the env -u arm; the
        # driver's `${2:?on value}` needs a non-empty positional, so the on arm
        # cannot be expressed as an empty string.
        if on_value and on_value != "unset":
            raise SystemExit(
                f"REFUSING: knob '{knob}' is presence-based; the on arm must "
                f"unset the var (env -u), not assign it, got on value '{on_value}'"
            )
    elif not on_value or on_value == "0":
        raise SystemExit(
            f"REFUSING: on value must be a nonzero value, got '{on_value}'"
        )
    if not off_value:
        raise SystemExit(f"REFUSING: off value must not be empty, got '{off_value}'")
    if meta["default_on"]:
        if off_value == "0":
            raise SystemExit(
                f"REFUSING: knob '{knob}' is on by default; off value must be "
                f"nonzero (DISABLE=1), got '{off_value}'"
            )
        if not presence and meta["off_var"] == meta["on_var"]:
            raise SystemExit(
                f"REFUSING: knob '{knob}' is on by default; its off var must "
                f"differ from its on var, got off_var == on_var == {meta['on_var']}"
            )
    elif off_value != "0":
        raise SystemExit(f"REFUSING: off value must be '0', got '{off_value}'")
    if not has_admission_signal(knob) and not acknowledge_no_signal:
        raise SystemExit(
            f"REFUSING: knob '{knob}' has no admission signal; pass "
            f"--ack-no-signal to measure it anyway (rows are marked "
            f"admission_signal: none)"
        )


def on_var(knob: str) -> str:
    """The env var the driver sets for the on arm."""
    return str(KNOBS[knob]["on_var"])


def off_var(knob: str) -> str:
    """The env var the driver sets for the off arm."""
    return str(KNOBS[knob]["off_var"])


def presence(knob: str) -> bool:
    """Whether the on arm unsets the var (`env -u`) rather than assigning it."""
    return bool(KNOBS[knob].get("presence", False))


def arm_cmd(knob: str, label: str, value: str) -> str:
    """The env prefix for an arm: `-u VAR` (presence on) or `VAR=value`.

    The shell runs `env $prefix ./ds4-bench ...`, so the on arm's argv is a
    pure function of the knob table. A presence knob's on arm must unset the
    var, not assign it; any other arm assigns. The value is the on/off value
    the driver was given, so the prefix carries the exact arm construction.
    """
    if label not in ("on", "off"):
        raise SystemExit(
            f"REFUSING: unknown arm label '{label}' (expected 'on' or 'off')"
        )
    var = on_var(knob) if label == "on" else off_var(knob)
    if label == "on" and presence(knob):
        return f"-u {var}"
    return f"{var}={value}"


def fail_closed_error(knob: str) -> str:
    """The fail-closed error string for a knob, or '' when it has no REQUIRE."""
    return str(KNOBS[knob]["fail_error"])


def check_fail_closed(knob: str, log: pathlib.Path) -> bool:
    """Whether the fail-closed error appears in a run log.

    There is no positive admission print, so absence of the error is the only
    admission signal. The on arm must not fail closed.
    """
    error = fail_closed_error(knob)
    if not error:
        return False
    return error in log.read_text(errors="replace")


def trace_var(knob: str) -> str:
    """The env var that traces the packed-FA path, or '' when the knob has none."""
    return str(KNOBS[knob].get("trace_var", ""))


def admission_print(knob: str) -> str:
    """The stderr line the engine prints when this knob is admitted, or ''.

    Not a trace: the engine prints it once, unprompted, from the dispatch
    site. So it needs no trace var, and its absence in the off arm is
    meaningful rather than merely smaller.

    A line printed from the Metal library compile block does NOT qualify and
    must not be listed here -- it reports that a macro was set, which is true
    whether or not a single matmul takes the path. See q4-mpp-cooperative.
    """
    return str(KNOBS[knob].get("admission_print", ""))


def admission_pattern(knob: str) -> str:
    """The string to count in a run log to decide whether the knob engaged."""
    printed = admission_print(knob)
    if printed:
        return printed
    if trace_var(knob):
        return TRACE_PATTERN
    return ""


def count_trace_lines(log: pathlib.Path, pattern: str = TRACE_PATTERN) -> int:
    """Count the lines matching `pattern` in a run log.

    The default is the packed-FA trace, which prints one line per dispatch:
    'ds4: packed FA use=...'. A count knob's on arm must engage more layers
    than its off arm, so the counts are the admission evidence. A print knob
    passes its own admission string instead, which the engine emits once.
    """
    if not pattern:
        raise ValueError("refusing to count an empty pattern: every line matches")
    return sum(
        1 for line in log.read_text(errors="replace").splitlines() if pattern in line
    )


def print_admission_ok(on_count: int, off_count: int) -> bool:
    """Whether a print knob engaged in the on arm and only in the on arm.

    Stricter than the count check, and it can afford to be: the engine emits
    this line only when it dispatches the path, so an off arm that emits one
    means the knob did not turn the path off and the comparison is between two
    identical arms wearing different labels.
    """
    return on_count > 0 and off_count == 0


def count_admission_ok(on_count: int, off_count: int) -> bool:
    """Whether a count knob's on arm demonstrably engaged more than its off arm.

    Both counts must be non-zero (the path was selected, not merely requested)
    and the on arm must exceed the off arm (the knob changed the layer count).
    Equal counts mean the knob did nothing, which is the tight-meaningless
    result the driver refuses.
    """
    return on_count > off_count > 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="refuse a wrong arm")
    v.add_argument("knob")
    v.add_argument("on_value")
    v.add_argument("off_value")
    v.add_argument(
        "--ack-no-signal",
        action="store_true",
        help="measure a knob with no admission signal; rows are marked 'none'",
    )

    o = sub.add_parser("on-var", help="print the on-arm env var for a knob")
    o.add_argument("knob")

    f = sub.add_parser("off-var", help="print the off-arm env var for a knob")
    f.add_argument("knob")

    p = sub.add_parser("presence", help="print 1 if the on arm unsets the var, else 0")
    p.add_argument("knob")

    a = sub.add_parser(
        "arm-cmd", help="print the env prefix for an arm: -u VAR or VAR=value"
    )
    a.add_argument("knob")
    a.add_argument("label")
    a.add_argument("value")

    s = sub.add_parser("admission-signal", help="print the admission signal for a knob")
    s.add_argument("knob")

    c = sub.add_parser("check-fail-closed", help="exit 0 if the error is present")
    c.add_argument("knob")
    c.add_argument("log", type=pathlib.Path)

    t = sub.add_parser("trace-var", help="print the trace var for a knob, or ''")
    t.add_argument("knob")

    ap = sub.add_parser(
        "admission-pattern",
        help="print the log string that proves this knob engaged, or ''",
    )
    ap.add_argument("knob")

    n = sub.add_parser(
        "count-trace-lines", help="print the admission line count in a log"
    )
    n.add_argument("log", type=pathlib.Path)
    n.add_argument(
        "--pattern",
        default=TRACE_PATTERN,
        help="the string to count (default: the packed-FA trace)",
    )

    pk = sub.add_parser(
        "print-admission-ok",
        help="exit 0 if the on arm printed the admission line and the off arm did not",
    )
    pk.add_argument("on_count", type=int)
    pk.add_argument("off_count", type=int)

    k = sub.add_parser(
        "count-admission-ok",
        help="exit 0 if on count > off count and both are non-zero",
    )
    k.add_argument("on_count", type=int)
    k.add_argument("off_count", type=int)

    args = parser.parse_args(argv)
    if args.cmd == "validate":
        validate(args.knob, args.on_value, args.off_value, args.ack_no_signal)
    elif args.cmd == "on-var":
        logger.info(on_var(args.knob))
    elif args.cmd == "off-var":
        logger.info(off_var(args.knob))
    elif args.cmd == "presence":
        logger.info("1" if presence(args.knob) else "0")
    elif args.cmd == "arm-cmd":
        logger.info(arm_cmd(args.knob, args.label, args.value))
    elif args.cmd == "admission-signal":
        logger.info(admission_signal(args.knob))
    elif args.cmd == "trace-var":
        logger.info(trace_var(args.knob))
    elif args.cmd == "admission-pattern":
        logger.info(admission_pattern(args.knob))
    elif args.cmd == "count-trace-lines":
        logger.info(count_trace_lines(args.log, args.pattern))
    elif args.cmd == "print-admission-ok":
        return 0 if print_admission_ok(args.on_count, args.off_count) else 1
    elif args.cmd == "count-admission-ok":
        return 0 if count_admission_ok(args.on_count, args.off_count) else 1
    else:
        return 0 if check_fail_closed(args.knob, args.log) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
