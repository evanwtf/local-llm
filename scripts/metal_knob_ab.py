"""Knob table and refusal logic for scripts/metal_knob_ab.sh (#162 Task 4).

The driver varies one Metal knob env var between two arms of the same tree and
GGUF. This module is the single source of truth for the knob table and the
refusal paths, so the negative cases are testable without running a
measurement.

The off arm sets the knob to `=0`. All four knobs are value-parsed (per-helper
evidence in evidence/0162-metal-knobs.json), so `0` genuinely disables;
unsetting would select each knob's default, which is not the same arm. The
three helpers disagree on the empty string, so an empty value is refused.

The on arm uses the REQUIRE spelling and fails the run if the fail-closed
error string appears. There is no positive admission print, so absence of the
error is the only admission signal and it must be checked, not assumed.
"""

from __future__ import annotations

import argparse
import pathlib

# knob -> env var + fail-closed error string (empty = no REQUIRE spelling).
# The env var is the REQUIRE spelling for the three knobs that have one; for
# stream-overlap it is the ENABLE spelling (no REQUIRE exists).
KNOBS: dict[str, dict[str, str]] = {
    "session-union": {
        "env": "DS4_METAL_REQUIRE_Q4_SSD_SESSION_UNION",
        "fail_error": "required Metal Q4 SSD session union is ineligible",
    },
    "iq2": {
        "env": "DS4_METAL_REQUIRE_IQ2_XXS_SSD_PREFILL_MM",
        "fail_error": (
            "required Metal IQ2_XXS SSD grouped address-MM path was not selected"
        ),
    },
    "exact-rows": {
        "env": "DS4_METAL_REQUIRE_EXACT_ROWS_PERSISTENT_CACHE",
        "fail_error": (
            "Metal exact-row persistent cache is required but disabled or ineligible"
        ),
    },
    "stream-overlap": {
        "env": "DS4_METAL_ENABLE_Q4_STREAM_OVERLAP",
        "fail_error": "",
    },
}


def validate(knob: str, on_value: str, off_value: str) -> None:
    """Refuse a wrong arm before the driver takes the lock or measures.

    The three helpers disagree on the empty string (metal_graph_tp_env_flag
    returns the default, ds4_gpu_exact_rows_persistent_env_enabled returns
    false, ds4_gpu_env_bool returns on), so an empty value is a wrong arm
    waiting to happen and is refused. The off arm must be `0` (all four knobs
    are value-parsed, so `0` genuinely disables); the on arm must be nonzero.
    """
    if knob not in KNOBS:
        raise SystemExit(
            f"REFUSING: unknown knob '{knob}' (known: {', '.join(sorted(KNOBS))})"
        )
    if not on_value or on_value == "0":
        raise SystemExit(
            f"REFUSING: on value must be a nonzero value, got '{on_value}'"
        )
    if not off_value or off_value != "0":
        raise SystemExit(f"REFUSING: off value must be '0', got '{off_value}'")


def env_var(knob: str) -> str:
    """The env var the driver sets for a knob."""
    return KNOBS[knob]["env"]


def fail_closed_error(knob: str) -> str:
    """The fail-closed error string for a knob, or '' when it has no REQUIRE."""
    return KNOBS[knob]["fail_error"]


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
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="refuse a wrong arm")
    v.add_argument("knob")
    v.add_argument("on_value")
    v.add_argument("off_value")

    e = sub.add_parser("env", help="print the env var for a knob")
    e.add_argument("knob")

    c = sub.add_parser("check-fail-closed", help="exit 0 if the error is present")
    c.add_argument("knob")
    c.add_argument("log", type=pathlib.Path)

    args = parser.parse_args(argv)
    if args.cmd == "validate":
        validate(args.knob, args.on_value, args.off_value)
    elif args.cmd == "env":
        print(env_var(args.knob))
    else:
        return 0 if check_fail_closed(args.knob, args.log) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
