"""benchmarks/videogen/prompts/v1.json: the frozen prompts and checklists (#1404).

v1 was frozen on 2026-11-17 and the judge control scored 33 of 33 against it
(#1402). A change to the prompts or the checklists is a new version file, not
an edit to this one: scores from v1 compare only with v1.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import frames

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


REFS = ROOT / "benchmarks" / "videogen" / "refs-v1.json"
HEX64 = re.compile(r"[0-9a-f]{64}")


def test_refs_v1_names_the_three_v1_clips() -> None:
    refs = json.loads(REFS.read_text())
    prompts = json.loads(V1.read_text())
    assert [c["ref"] for c in refs["clips"]] == [c["ref"] for c in prompts["clips"]]
    assert [c["duration"] for c in refs["clips"]] == ["8.0", "6.0", "4.5"]
    assert [c["crop"] for c in refs["clips"]] == [None, None, "1080:608:0:700"]
    for clip in refs["clips"]:
        assert HEX64.fullmatch(clip["sha256"])
        names = sorted(clip["frames_sha256"])
        stills = [n for n in names if frames.STILL.match(n)]
        assert names == sorted(["first.png", "strip.jpg", *stills])
        # One still a second, rounded up: 8 s, 6 s, 4.5 s.
        assert len(stills) == math.ceil(float(clip["duration"]))
        assert all(HEX64.fullmatch(d) for d in clip["frames_sha256"].values())


def test_refs_v1_frames_used_the_current_parameters() -> None:
    """A change to frames.PARAMS changes every frame: it needs refs-v2.json."""
    refs = json.loads(REFS.read_text())
    assert refs["frames"]["params"] == frames.PARAMS
    assert refs["frames"]["font"] == frames.FONT_NAME
