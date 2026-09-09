"""Every marker our parsers look for has a real example on disk.

`benchmarks/agent/test_ds4_route.py` asserted for months that ds4 prints

    Metal 4 tensor API available but not enabled (set DS4_METAL_ENABLE_TENSOR=1)

It does not, and never has. The invented line appears in 0 real logs; the true
one -- `available but not enabled (numerics); set DS4_METAL_ENABLE_TENSOR=1 to
override` -- appears in 12. The test passed because the matcher it exercised
was a substring of both.

This file is the guard against reintroducing that: a marker constant with no
real example is a string somebody typed, and it fails here.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "logs"
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import ds4_server
import metal_equivalence
import metal_route


def corpus() -> str:
    return "\n".join(
        p.read_text(errors="replace") for p in sorted(FIXTURES.glob("*.log"))
    )


@pytest.mark.parametrize(
    "marker",
    [
        metal_route.TENSOR_LINE,
        metal_route.WITHHOLD_LINE,
        metal_route.FAST_PATH_LINE,
        metal_route.ANY_MARKER,
        ds4_server.GRAPH_MARKER,
        ds4_server.MTP_OFF,
        metal_equivalence.SUMMARY_MARK,
    ],
)
def test_every_marker_has_a_real_example(marker: str) -> None:
    assert marker in corpus(), (
        f"{marker!r} appears in no fixture under {FIXTURES}. A parser with no "
        "real example is tested against the string its author typed -- which "
        "is how test_ds4_route came to assert a line ds4 has never printed."
    )


def test_the_two_route_fixtures_are_opposites() -> None:
    tensor = (FIXTURES / "ds4-server-route-tensor.log").read_text()
    withheld = (FIXTURES / "ds4-server-route-withheld.log").read_text()
    assert metal_route.arm_route_ok(tensor, metal_route.TENSOR)
    assert not metal_route.arm_route_ok(tensor, metal_route.WITHHELD)
    assert metal_route.arm_route_ok(withheld, metal_route.WITHHELD)
    assert not metal_route.arm_route_ok(withheld, metal_route.TENSOR)


def test_both_route_fixtures_took_the_fast_path() -> None:
    # An arm that fell back to the slow path is a different experiment, so the
    # fixtures have to be arms, not merely logs.
    for name in ("ds4-server-route-tensor.log", "ds4-server-route-withheld.log"):
        assert metal_route.fast_path_ok((FIXTURES / name).read_text()), name


def test_the_graph_fixtures_are_an_mtp_arm_and_its_control() -> None:
    mtp = FIXTURES / "ds4-server-graph-mtp.log"
    plain = FIXTURES / "ds4-server-graph-plain.log"
    assert "MTP=Q4_K" in ds4_server.assert_graph(mtp, want_mtp=True)
    assert ds4_server.MTP_OFF in ds4_server.assert_graph(plain, want_mtp=False)
    with pytest.raises(ds4_server.GraphMismatch):
        ds4_server.assert_graph(plain, want_mtp=True)
    with pytest.raises(ds4_server.GraphMismatch):
        ds4_server.assert_graph(mtp, want_mtp=False)


def test_want_mtp_none_asserts_nothing_but_still_needs_a_line() -> None:
    # stack_agent_ab's arms differ BY configuration, so it has no single
    # answer to hold. An absent line is still a failure: "the server never
    # started" is true whatever the arm expected.
    mtp = FIXTURES / "ds4-server-graph-mtp.log"
    plain = FIXTURES / "ds4-server-graph-plain.log"
    assert ds4_server.assert_graph(mtp, want_mtp=None)
    assert ds4_server.assert_graph(plain, want_mtp=None)
    with pytest.raises(ds4_server.ServerNeverStarted):
        ds4_server.assert_graph(FIXTURES / "shim-strip-on.log", want_mtp=None)


def test_the_gate_fixture_carries_both_summaries_and_they_differ() -> None:
    # The reason parse_summary grew a `route=` selector: the asserted `auto`
    # candidate reads all zeros against the withhold tree, and the drift #149
    # pre-registered is on the tensor-optin line underneath it.
    text = (FIXTURES / "ds4-test-metal-tensor-equivalence.log").read_text()
    auto = metal_equivalence.parse_summary(text, route="auto")
    optin = metal_equivalence.parse_summary(text, route="tensor-optin")
    assert auto["worst_rms"] == 0.0
    assert optin["worst_rms"] == pytest.approx(1.38592)
    assert optin["worst_max_abs"] == pytest.approx(7.26952)
    # The default takes the first, which is the asserted candidate.
    assert metal_equivalence.parse_summary(text) == auto
    assert metal_equivalence.verdict(0, text) == "pass"
