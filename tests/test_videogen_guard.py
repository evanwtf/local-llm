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

#: Every video extension the guards cover, in lower case.
VIDEO_EXTENSIONS = [
    "mp4",
    "mov",
    "m4v",
    "mkv",
    "webm",
    "avi",
    "heic",
    "heif",
    "avif",
    "3gp",
    "mts",
    "m2ts",
    "hevc",
    "y4m",
]
#: Image extensions refused under benchmarks/videogen/.
FRAME_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff"]

#: Paths the hook must refuse, and paths it must let through.
REFUSED = [
    "dog-a.mp4",
    "IMG_0001.MOV",
    "IMG_0002.HEIC",
    "clips/dog-b.Mov",
    "x.m4v",
    "x.MKV",
    "x.webm",
    "x.y4m",
    "x.M2TS",
    *(f"clip.{ext}" for ext in VIDEO_EXTENSIONS),
    *(f"clip.{ext.upper()}" for ext in VIDEO_EXTENSIONS),
    *(f"benchmarks/videogen/frames/s01.{ext}" for ext in FRAME_EXTENSIONS),
    "benchmarks/videogen/first.PNG",
]
ALLOWED = [
    "scripts/videogen/cut.py",
    "benchmarks/videogen/prompts/v1.json",
    "benchmarks/videogen/refs-v1.json",
    "docs/clock-cap-1319/charts/curve.png",
    "notes-about-mp4.md",
    "mp4.py",
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


def check_ignore(names: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *names],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(result.stdout.split())


def test_gitignore_hides_each_video_extension_in_any_case() -> None:
    names = [
        *(f"a.{ext}" for ext in VIDEO_EXTENSIONS),
        *(f"a.{ext.upper()}" for ext in VIDEO_EXTENSIONS),
        *(f"sub/a.{ext.title()}" for ext in VIDEO_EXTENSIONS),
    ]
    assert check_ignore(names) == sorted(names)


def test_gitignore_hides_frames_under_benchmarks_videogen() -> None:
    names = [
        *(f"benchmarks/videogen/frames/s01.{ext}" for ext in FRAME_EXTENSIONS),
        "benchmarks/videogen/first.PNG",
    ]
    assert check_ignore(names) == sorted(names)
    assert check_ignore(["benchmarks/videogen/refs-v1.json", "mp4.py"]) == []
