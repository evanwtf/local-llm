"""#1404: the guards that keep video files and frames out of this public repo.

Two layers: `.gitignore` hides videos from `git add .`, and the
`refuse-video-files` pre-commit hook refuses a commit that stages one anyway
(`git add -f`). These tests pin both, so a later edit cannot drop either
without a red test.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Paths the hook must refuse, and paths it must let through.
REFUSED = [
    "dog-a.mp4",
    "IMG_0001.MOV",
    "clips/dog-b.mov",
    "x.m4v",
    "x.MKV",
    "benchmarks/videogen/frames/s01.jpg",
    "benchmarks/videogen/first.png",
]
ALLOWED = [
    "scripts/videogen/cut.py",
    "benchmarks/videogen/prompts/v1.json",
    "docs/figure.png",
    "notes-about-mp4.md",
]


def video_hook() -> dict[str, object]:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    hooks = [h for repo in config["repos"] for h in repo["hooks"]]
    matches = [h for h in hooks if h["id"] == "refuse-video-files"]
    assert len(matches) == 1, "the refuse-video-files hook is missing"
    hook: dict[str, object] = matches[0]
    return hook


def test_the_hook_fails_on_a_match_and_runs_at_commit() -> None:
    hook = video_hook()
    assert hook["language"] == "fail"
    assert hook.get("stages", ["pre-commit"]) == ["pre-commit"]
    assert "always_run" not in hook


def test_the_hook_pattern_refuses_videos_and_frames() -> None:
    pattern = re.compile(str(video_hook()["files"]))
    # pre-commit matches `files` with re.search against the repo-relative path.
    assert [p for p in REFUSED if not pattern.search(p)] == []
    assert [p for p in ALLOWED if pattern.search(p)] == []


def test_gitignore_hides_each_video_extension() -> None:
    names = ["a.mp4", "a.mov", "a.MOV", "a.m4v", "a.mkv"]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *names],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sorted(result.stdout.split()) == sorted(names)
