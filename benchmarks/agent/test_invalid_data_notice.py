"""The pre---dir OpenCode notice must stay visible and its link must resolve.

A warning that quietly disappears in a future rewrite is worse than none: the
numbers it guards look ordinary, and the failure they describe is silent.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CANON = ROOT / "docs/archive/results-opencode-pre-dir.md"
CUTOVER = "2026-08-31T21:47:18-04:00"

BANDED = [
    "README.md",
    "NEXT.md",
    "AGENTS.md",
    "hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/RESULTS-agent.md",
    "benchmarks/agent/README.md",
    "benchmarks/agent/METHODOLOGY.md",
]


def test_the_canonical_explanation_exists_and_names_the_cutover() -> None:
    t = CANON.read_text()
    assert CUTOVER in t
    assert "canonical explanation" in t
    # The cause has to survive, not just the warning.
    assert "persistent server" in t


@pytest.mark.parametrize("rel", BANDED)
def test_every_results_bearing_doc_carries_the_notice(rel: str) -> None:
    t = (ROOT / rel).read_text()
    assert "OpenCode results before 2026-08-31 21:47 EDT are INVALID" in t


@pytest.mark.parametrize("rel", BANDED)
def test_the_notice_appears_before_any_result(rel: str) -> None:
    """It must be near the top, not buried under the numbers it qualifies."""
    lines = (ROOT / rel).read_text().split("\n")
    at = next(i for i, l in enumerate(lines) if "are INVALID" in l)
    assert at < 6, f"notice is at line {at} of {rel}"


@pytest.mark.parametrize("rel", BANDED)
def test_the_link_resolves(rel: str) -> None:
    doc = ROOT / rel
    t = doc.read_text()
    start = t.index("](", t.index("are INVALID")) + 2
    target = t[start : t.index(")", start)]
    assert (doc.parent / target).resolve().exists(), (
        f"{rel} links to a missing {target}"
    )
