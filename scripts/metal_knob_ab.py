"""Knob table and refusal logic for scripts/metal_knob_ab.sh (#162 Task 4).

The driver varies one Metal knob between two arms of the same tree and GGUF.
This module is the single source of truth for the knob table and the refusal
paths, so the negative cases are testable without running a measurement.

Each knob has two env vars: the var that turns it on and the var that turns it
off. For the three opt-in knobs (default off) they are the same variable, set
to a nonzero value to enable and `0` to disable. For exact-rows they are not:
the persistent cache is on by default, so `REQUIRE=0` means "not required" and
leaves the cache running. Its off arm is the DISABLE var, which the source
names "the A/B rollback arm and always wins" (ds4_metal.m:14827). A knob whose
off arm does not change the default is a wrong arm waiting to happen, so
`validate()` refuses it.

gathered-heads is the first presence-based knob. The branch routes n_comp==0
layers to the gathered-heads path unless `DS4_METAL_DISABLE_DECODE_RAW_GATHERED_ATTN`
is set (ds4_metal.m:38408, `getenv(...) != NULL`), so the feature is on by
default and there is no REQUIRE spelling to force it. The on arm must *unset*
the DISABLE var (`env -u`), not assign it: `=0` still counts as set and would
take the raw-only path in both arms. So for a presence knob `on_var == off_var`
(the same DISABLE var), and `validate()` refuses an assignment on arm instead
of the `off_var != on_var` rule that guards the assignment-based exact-rows
shape.

The three helpers disagree on the empty string (metal_graph_tp_env_flag returns
the default, ds4_gpu_exact_rows_persistent_env_enabled returns false,
ds4_gpu_env_bool returns on), so an empty value is refused. The refusal runs
before the driver takes the lock or exports anything, so no helper ever sees an
empty value.

The on arm uses the REQUIRE spelling and fails the run if the fail-closed error
string appears. There is no positive admission print, so absence of the error is
the only admission signal and it must be checked, not assumed. stream-overlap
has no REQUIRE spelling, so it has no admission signal at all: its policy gate
(ds4.c:71766) has seven terms, and any of count, resident, ssd_streaming or
quality can veto the path with no output. A knob with no admission signal is
refused unless the caller passes an explicit acknowledgment, and its rows are
marked `admission_signal: "none"` so they cannot later be read as verified.
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
    },
}


def has_admission_signal(knob: str) -> bool:
    """Whether a run of this knob carries an admission check.

    A knob with no REQUIRE spelling has no fail-closed error, so absence of the
    error is not a signal. stream-overlap is the only such knob.
    """
    return bool(fail_closed_error(knob))


def admission_signal(knob: str) -> str:
    """The admission signal a run of this knob carries.

    'fail-closed' when the on arm is checked for the REQUIRE error; 'none' when
    there is no check. The value goes on the run so a later reader cannot read
    an unverified knob as verified.
    """
    return "fail-closed" if has_admission_signal(knob) else "none"


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

    p = sub.add_parser(
        "presence", help="print 1 if the on arm unsets the var, else 0"
    )
    p.add_argument("knob")

    s = sub.add_parser("admission-signal", help="print the admission signal for a knob")
    s.add_argument("knob")

    c = sub.add_parser("check-fail-closed", help="exit 0 if the error is present")
    c.add_argument("knob")
    c.add_argument("log", type=pathlib.Path)

    args = parser.parse_args(argv)
    if args.cmd == "validate":
        validate(args.knob, args.on_value, args.off_value, args.ack_no_signal)
    elif args.cmd == "on-var":
        logger.info(on_var(args.knob))
    elif args.cmd == "off-var":
        logger.info(off_var(args.knob))
    elif args.cmd == "presence":
        logger.info("1" if presence(args.knob) else "0")
    elif args.cmd == "admission-signal":
        logger.info(admission_signal(args.knob))
    else:
        return 0 if check_fail_closed(args.knob, args.log) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
