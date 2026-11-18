"""Shared paths, guards and the ffmpeg runner for the video-gen reference scripts (#1404).

The source clips are the operator's own videos, and this repository is public.
Every clip, frame and first-frame PNG lives under `~/bench-logs/videogen/`, never
in a checkout. `refuse_inside_repo` is the mechanism behind that rule: a script
refuses an output path inside ANY git work tree (this checkout, a worktree of
it, or another clone) before it runs ffmpeg, so a typo in an output path cannot
stage a private frame for commit.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pathlib
import shutil
import subprocess
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

#: Where clips and frames live by default. Outside every repository on purpose.
DEFAULT_ROOT = pathlib.Path.home() / "bench-logs" / "videogen"
#: The repository's video-gen settings: arms, prompts and refs files.
BENCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "benchmarks" / "videogen"
#: The image arms (#1424): text-to-image, and image edits. Each names its
#: prompts file under `prompts`.
IMAGE_ARMS_FILE = BENCH_DIR / "image-arms-v1.json"
EDIT_ARMS_FILE = BENCH_DIR / "edit-arms-v1.json"
#: The input images of the image-edit arms (#1424). Private, like the clips.
IMAGE_INPUTS_ROOT = DEFAULT_ROOT / "ref" / "imagegen"
#: The first 8 bytes of every PNG file.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def prompt_name(entry: Mapping[str, Any]) -> str:
    """The run directories' name for a prompt entry.

    An image prompt names itself (`name`, #1424). A video prompt is named by
    its reference clip's stem (`ref: dog-a.mp4` is `dog-a`).
    """
    if entry.get("name"):
        return str(entry["name"])
    return pathlib.PurePath(str(entry["ref"])).stem


def png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR chunk, or None if `data` is no PNG.

    The IHDR chunk always comes first: 8 signature bytes, a 4-byte length,
    `IHDR`, then the width and height as 4-byte big-endian numbers.
    """
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


class InsideRepo(Exception):
    """An output path points inside a git work tree."""


class FfmpegError(Exception):
    """ffmpeg or ffprobe exited nonzero, or printed what we cannot parse."""


def nearest_existing(path: pathlib.Path) -> pathlib.Path:
    """`path` itself if it exists, else its nearest existing ancestor."""
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return pathlib.Path("/")


def git_toplevel(path: pathlib.Path) -> str | None:
    """The work tree that holds `path`, or None if it is in no work tree.

    GIT_* variables are dropped: a git hook (pre-push runs the tests) sets
    GIT_DIR, and that would make every path look like it is in this repo.
    """
    git = shutil.which("git")
    if git is None:
        # Fail closed: without git we cannot prove the path is safe.
        raise InsideRepo("git is not on PATH, so the output path cannot be checked")
    base = nearest_existing(path)
    if base.is_file():
        base = base.parent
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(
        [git, "-C", str(base), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def refuse_inside_repo(path: pathlib.Path) -> None:
    """Raise InsideRepo if `path` is inside any git work tree."""
    resolved = path.expanduser().resolve()
    top = git_toplevel(resolved)
    if top is not None:
        raise InsideRepo(
            f"{resolved} is inside the git work tree {top}. Clips and frames "
            f"are private and must never be committed: write under {DEFAULT_ROOT}."
        )


def require(tool: str) -> str:
    """Return the path of `tool` on PATH, or raise FfmpegError."""
    found = shutil.which(tool)
    if found is None:
        raise FfmpegError(f"{tool} is not on PATH")
    return found


def file_url(path: pathlib.Path) -> str:
    """`file:` plus the absolute path, for an ffmpeg input or output.

    Without the `file:` protocol ffmpeg reads `a:b.mp4` as protocol `a`, and
    a relative path depends on the working directory.
    """
    return "file:" + str(path.expanduser().resolve())


def run(argv: list[str], *, cwd: pathlib.Path | None = None) -> str:
    """Run `argv` (no shell) and return its stdout. Raise FfmpegError on failure.

    `errors="replace"` because ffmpeg can print bytes from a file's own tags,
    and a strict decode would raise before the exit status is read (#1189).
    """
    logger.debug("run: %s", argv)
    result = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise FfmpegError(
            f"{argv[0]} exited {result.returncode}: {result.stderr.strip()[-2000:]}"
        )
    return result.stdout


def ffmpeg_version() -> str:
    """The version token from `ffmpeg -version`, e.g. `6.1.1-3ubuntu5`."""
    first = run(["ffmpeg", "-version"]).splitlines()[0]
    parts = first.split()
    return parts[2] if len(parts) > 2 else first


def sha256(path: pathlib.Path) -> str:
    """Hex sha256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
