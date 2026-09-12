"""The pre---dir OpenCode failure must stay explained where it can be found.

The top-of-doc banner that once sat on every results-bearing doc was retired
(2026-09-12): the data it guarded is old, the ledger holds none of those rows,
and a warning on every header earned less than it cost. What must not vanish is
the *cause* -- so this guards the canonical explanation, not a banner.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
CANON = ROOT / "docs/archive/results-opencode-pre-dir.md"
CUTOVER = "2026-08-31T21:47:18-04:00"


def test_the_canonical_explanation_exists_and_names_the_cutover() -> None:
    t = CANON.read_text()
    assert CUTOVER in t
    assert "canonical explanation" in t
    # The cause has to survive, not just the warning.
    assert "persistent server" in t
