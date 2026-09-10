"""The Metal 4 route markers, and the asymmetry between the two arms (#149).

The T arm is defined by a line being present. The R arm is defined by one line
being present **and another being absent**, and that second half is the one a
port drops. A server running a stale binary beside a fresh tree prints both.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import metal_route
from source_text import code_of

FAST = metal_route.FAST_PATH_LINE
TENSOR = metal_route.TENSOR_LINE
WITHHOLD = metal_route.WITHHOLD_LINE


def log(*lines: str) -> str:
    return "\n".join(("ds4: starting", *lines, "ds4: ready"))


def test_tensor_arm_needs_the_tensor_line() -> None:
    assert metal_route.arm_route_ok(log(FAST, TENSOR), "t") is True
    assert metal_route.arm_route_ok(log(FAST, WITHHOLD), "t") is False


def test_withheld_arm_needs_the_withhold_line() -> None:
    assert metal_route.arm_route_ok(log(FAST, WITHHOLD), "r") is True
    assert metal_route.arm_route_ok(log(FAST), "r") is False


def test_withheld_arm_refuses_a_log_carrying_both_lines() -> None:
    # The half a port drops. Checking only for the withhold line would pass
    # this, and this is what a stale binary beside a fresh tree looks like.
    both = log(FAST, WITHHOLD, TENSOR)
    assert metal_route.arm_route_ok(both, "r") is False
    assert metal_route.arm_route_ok(both, "t") is True


def test_fast_path_is_separate_from_the_route() -> None:
    # An arm that fell back to the slow path is a different experiment, not a
    # slower one, so this is asked of both arms and is not part of arm_route_ok.
    assert metal_route.fast_path_ok(log(TENSOR)) is False
    assert metal_route.fast_path_ok(log(FAST, TENSOR)) is True


def test_why_not_leads_with_the_missing_fast_path() -> None:
    why = metal_route.why_not(log(TENSOR), "t")
    assert FAST in why


def test_why_not_names_the_line_that_decided_it() -> None:
    assert TENSOR in metal_route.why_not(log(FAST, WITHHOLD, TENSOR), "r")
    assert TENSOR in metal_route.why_not(log(FAST, WITHHOLD), "t")


def test_why_not_is_empty_when_the_arm_is_fine() -> None:
    assert metal_route.why_not(log(FAST, TENSOR), "t") == ""


def test_an_unknown_arm_raises_rather_than_reporting_false() -> None:
    # False would read as "that arm took the wrong route", which is a claim
    # about a server. A typo is a claim about the caller.
    with pytest.raises(ValueError):
        metal_route.arm_route_ok(log(FAST, TENSOR), "tensor")


def test_the_report_and_the_driver_share_one_definition() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import route_ab_report

    assert route_ab_report.TENSOR_LINE is metal_route.TENSOR_LINE
    assert route_ab_report.WITHHOLD_LINE is metal_route.WITHHOLD_LINE


def test_the_markers_are_defined_once() -> None:
    # A second literal copy anywhere under scripts/ is the drift this module
    # exists to remove.
    copies = [
        p
        for p in (ROOT / "scripts").rglob("*.py")
        if p.name != "metal_route.py" and TENSOR in code_of(p)
    ]
    assert copies == []
