"""REPS must be even, and the default must be 4 (#201).

Alternation cancels the positional bias only on an EVEN rep count. At REPS=3
reps 1 and 3 run A-first and only rep 2 runs B-first, so the bias lands 2:1 on
one arm instead of dividing out.

The bias is measured, not assumed. Across the twelve reps of #171 whichever
arm ran first was faster in 9 of them, median +0.9%, and **+5.9% on the first
rep of a cold session**, decaying over about an hour. That is larger than most
effects these scripts are used to measure -- #228's upstream prefill claim is
+3.8% -- so an odd sweep does not produce a weaker result, it produces an
uninterpretable one.

It is a refusal rather than a warning because the failure is silent: an odd
sweep writes a complete CSV and a plausible number with nothing to say half
the design is missing, and a warning scrolls past in a batch nobody watches.

All four A/B scripts are covered, not the two #201 names. decode_ab_stack.sh
and metal_knob_ab.sh alternate arms and write run-order.txt exactly as the
other two do, so they carried the identical defect; fixing half of a defect
because the issue title named half of it is how a guard ends up describing
more than it does.

The negative case is the one that keeps the guard honest -- an even value must
NOT be refused, or every sweep is unreachable and nobody notices until an
overnight batch produces no rows.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = [
    ROOT / "scripts" / "decode_ab.sh",
    ROOT / "scripts" / "decode_ab_engine.sh",
    ROOT / "scripts" / "decode_ab_stack.sh",
    ROOT / "scripts" / "metal_knob_ab.sh",
]

# Deliberately absent trees, as in test_decode_ab_engine.py: the REPS guard
# runs before anything is loaded, so a refused value stops at the guard and an
# accepted one falls through to a missing-binary refusal. Two distinguishable
# outcomes, no build, no model, no machine lock.
ARGS = {
    "decode_ab.sh": ["a", "/nonexistent/a.gguf", "b", "/nonexistent/b.gguf"],
    "decode_ab_engine.sh": [
        "a",
        "/nonexistent/tree-a",
        "b",
        "/nonexistent/tree-b",
        "/nonexistent/x.gguf",
    ],
    "decode_ab_stack.sh": [
        "a",
        "/nonexistent/tree-a",
        "/nonexistent/a.gguf",
        "-",
        "b",
        "/nonexistent/tree-b",
        "/nonexistent/b.gguf",
        "-",
    ],
    "metal_knob_ab.sh": [
        "DS4_SOME_KNOB",
        "1",
        "0",
        "/nonexistent/tree",
        "/nonexistent/x.gguf",
    ],
}


def run(script: pathlib.Path, reps: str | None, allow_odd: bool = False):
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}
    if reps is not None:
        env["REPS"] = reps
    if allow_odd:
        env["ALLOW_ODD_REPS"] = "1"
    return subprocess.run(
        ["bash", str(script), *ARGS[script.name]],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        # These runs are expected to fail: the guard refuses and exits
        # non-zero, which is the thing under test (ruff PLW1510, see #197).
        check=False,
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_the_script_parses(script):
    subprocess.run(["bash", "-n", str(script)], check=True)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_the_default_is_four(script):
    """A default of 3 is the #201 defect itself, not a style choice."""
    body = script.read_text()
    assert "REPS=${REPS:-4}" in body, f"{script.name} does not default REPS to 4"
    assert "REPS=${REPS:-3}" not in body, f"{script.name} still defaults REPS to 3"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
@pytest.mark.parametrize("reps", ["1", "3", "5", "7"])
def test_an_odd_rep_count_is_refused(script, reps):
    p = run(script, reps)
    assert p.returncode == 2, f"REPS={reps} was not refused ({p.returncode})"
    assert "REFUSING: REPS" in p.stderr, p.stderr


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
@pytest.mark.parametrize("reps", ["2", "4", "6", "8"])
def test_an_even_rep_count_reaches_the_next_check(script, reps):
    """The negative case: a valid value must fall through, or nothing runs."""
    p = run(script, reps)
    assert "REFUSING: REPS" not in p.stderr, p.stderr


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_the_override_warns_instead_of_refusing(script):
    """An odd count stays reachable when it is the operator's stated intent."""
    p = run(script, "3", allow_odd=True)
    assert "REFUSING: REPS" not in p.stderr, p.stderr
    assert "WARNING: REPS=3 is odd" in p.stderr, p.stderr


def test_the_engine_script_records_run_order():
    """#130 gave decode_ab.sh run-order.txt; the engine script never got it.

    It is the script used for engine A/Bs, where effects are smallest and the
    positional bias matters most -- exactly where a reader needs to test for
    the bias rather than assume it away.
    """
    body = (ROOT / "scripts" / "decode_ab_engine.sh").read_text()
    assert "run-order.txt" in body, "decode_ab_engine.sh does not record run order"
    assert "position=$position of 2" in body, "run-order line lacks the position"
