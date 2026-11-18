"""Shared paths and the ffmpeg runner for the video-gen reference scripts (#1404).

The source clips are the operator's own videos, and this repository is public.
Every clip, frame and first-frame PNG lives under `~/bench-logs/videogen/`, never
in the checkout. `refuse_inside_repo` is the mechanism behind that rule: a script
refuses an output path inside the repository before it runs ffmpeg, so a typo in
an output path cannot stage a private frame for commit.
"""

from __future__ import annotations

import logging
import pathlib
import shutil
import subprocess

logger = logging.getLogger(__name__)

#: The repository root: scripts/videogen/common.py -> parents[2].
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Where clips and frames live by default. Outside the repository on purpose.
DEFAULT_ROOT = pathlib.Path.home() / "bench-logs" / "videogen"


class InsideRepo(Exception):
    """An output path points inside the public repository."""


class FfmpegError(Exception):
    """ffmpeg or ffprobe exited nonzero."""


def refuse_inside_repo(path: pathlib.Path, repo: pathlib.Path = REPO_ROOT) -> None:
    """Raise InsideRepo if `path` resolves to a location inside `repo`."""
    resolved = path.expanduser().resolve()
    if resolved == repo or repo in resolved.parents:
        raise InsideRepo(
            f"{resolved} is inside the repository {repo}. The repository is "
            "public and the clips are private: write under "
            f"{DEFAULT_ROOT} instead."
        )


def require(tool: str) -> str:
    """Return the path of `tool` on PATH, or raise FfmpegError."""
    found = shutil.which(tool)
    if found is None:
        raise FfmpegError(f"{tool} is not on PATH")
    return found


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
