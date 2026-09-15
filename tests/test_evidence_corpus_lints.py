"""#218: every committed evidence file lints under the current schema.

`evidence.py verify` re-runs claims against live ds4 trees, which exist only
on the M5 Max, so CI cannot run it. `lint` needs no tree: it checks the
schema, the required keys, and each claim's shape. This test runs that check
over every `evidence/*.json`, so a malformed finding fails CI the day it lands.

Four files failed lint before this test existed, and three more failed when
#176 required every cited line number to be declared. Preserved evidence is
not rewritten, so they stay on GRANDFATHERED with the reason. The test also
asserts each one still fails, so the list cannot go stale. A grandfathered
file is exempt from every lint rule, not only the one it breaks.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import evidence

EVIDENCE_DIR = REPO / "evidence"

#: File name -> why it does not lint. Do not add a new file here: fix it.
GRANDFATHERED = {
    "0162-metal-diff-expect.json": "has no schema field",
    "0170-qwen38-metal-suites.json": "has no schema field",
    "0190-kv-prefix-reuse.json": "has no schema field",
    "0210-mtp-sidecar.json": "declares 'evidence-2', a spelling never accepted",
    # #176: each cites a line number its declaration fields do not list. The
    # #182 citation retrofit repinned the statements and left the declarations.
    "0158-prefill.json": (
        "#176: appended-interval-source cites "
        "ds4_bench.c:839 at ds4-main 9ab70534; enumerates is [518, 843] (a46aa8b)"
    ),
    "0162-metal-diff.json": (
        "#176: hc-fusion-pre-m5 cites "
        "ds4_metal.m:3602 at ds4-pr952 77a054e1; context_lines is [3599] (1667beb)"
    ),
    "0162-metal-knobs.json": (
        "#176: require-error-strings cites "
        "ds4_metal.m:20449-20451 at ds4-pr952 77a054e1; no declaration covers it "
        "(1667beb)"
    ),
}

FILES = sorted(p.name for p in EVIDENCE_DIR.glob("*.json"))


def test_the_corpus_is_not_empty():
    assert len(FILES) > len(GRANDFATHERED)


def test_every_grandfathered_file_exists():
    assert set(GRANDFATHERED) <= set(FILES)


@pytest.mark.parametrize("name", [f for f in FILES if f not in GRANDFATHERED])
def test_evidence_file_lints(name):
    evidence.load_finding(EVIDENCE_DIR / name)


@pytest.mark.parametrize("name", sorted(GRANDFATHERED))
def test_grandfathered_file_still_fails(name):
    """A file that lints clean now must leave the list."""
    with pytest.raises(evidence.Refused):
        evidence.load_finding(EVIDENCE_DIR / name)
