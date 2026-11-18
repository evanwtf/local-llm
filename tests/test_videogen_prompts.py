"""benchmarks/videogen/prompts/v1.json: the frozen prompts and checklists (#1404).

v1 was frozen on 2026-11-17 and the judge control scored 33 of 33 against it
(#1402). A change to the prompts or the checklists is a new version file, not
an edit to this one: scores from v1 compare only with v1.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
V1 = ROOT / "benchmarks" / "videogen" / "prompts" / "v1.json"

#: sha256 of v1.json as frozen. Bytes, not shape: a reformat is an edit too.
V1_SHA256 = "cbca6a71d8d9ac6bec7569bbaa63825a3a6713a0c82e68a69d4791f1d4a5e220"


def test_v1_loads_with_three_clips_and_33_items() -> None:
    data = json.loads(V1.read_text())
    assert data["version"] == "v1"
    clips = data["clips"]
    assert len(clips) == 3
    assert [len(c["checklist"]) for c in clips] == [12, 11, 10]
    assert all(c["prompt"] for c in clips)
    assert data["defects"]


def test_v1_is_byte_for_byte_frozen() -> None:
    assert hashlib.sha256(V1.read_bytes()).hexdigest() == V1_SHA256
