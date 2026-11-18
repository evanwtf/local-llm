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

logger = logging.getLogger(__name__)

#: Where clips and frames live by default. Outside every repository on purpose.
DEFAULT_ROOT = pathlib.Path.home() / "bench-logs" / "videogen"


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
