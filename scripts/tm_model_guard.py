#!/usr/bin/env python3
"""Fail if Time Machine would back up a model file. #440

On 2026-09-18 the network backup share was 94% full. Six directories of
weights and caches, 773 GB in all, had been created after the exclusion list
was written, and nothing named them. Every new engine checkout with its own
`gguf/` directory, and every new model cache, repeats that.

This walks `$HOME`, skipping the fixed exclusions (`SkipPaths` in the Time
Machine preferences) for speed. It asks `tmutil isexcluded` about every file of
1 GiB or more; that answer covers fixed exclusions, sticky exclusions, and
excluded parent directories. A model file that Time Machine includes is a
failure. Other large files are counted and listed, never failed: they are the
operator's data.

    uv run python scripts/tm_model_guard.py            # exit 1 if a model file is included
    uv run python scripts/tm_model_guard.py --list     # also name the other large files

Fix a failure with a fixed-path exclusion, which survives the directory being
deleted and made again:

    sudo tmutil addexclusion -p <directory>
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import pathlib
import plistlib
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

GIB = 1024**3
PREFS = pathlib.Path("/Library/Preferences/com.apple.TimeMachine.plist")

#: Weight formats the engines on this machine read. A file must also be at
#: least MODEL_MIN bytes: a tokenizer or a test fixture is not the problem.
MODEL_SUFFIXES = frozenset(
    {".gguf", ".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".npz", ".onnx"}
)
MODEL_MIN = GIB
#: The find threshold, in KiB, for "a large file". find tests the apparent
#: size, so it can only over-select; `allocated` decides.
FIND_MIN_KIB = GIB // 1024


@dataclasses.dataclass(frozen=True)
class Verdict:
    included_models: list[tuple[pathlib.Path, int]]
    other_large: list[tuple[pathlib.Path, int]]

    @property
    def exit_code(self) -> int:
        return 1 if self.included_models else 0


def is_model_file(path: pathlib.Path, size: int) -> bool:
    return path.suffix.lower() in MODEL_SUFFIXES and size >= MODEL_MIN


def skip_paths(prefs: dict[str, object]) -> list[str]:
    got = prefs.get("SkipPaths") or []
    return [str(p) for p in got] if isinstance(got, list) else []


def prunable(home: pathlib.Path, paths: Iterable[str]) -> list[str]:
    """The skip paths strictly inside `home`; find never reaches the others."""
    root = str(home).rstrip("/") + "/"
    return [p.rstrip("/") for p in paths if p.startswith(root)]


def find_argv(home: pathlib.Path, prune: Sequence[str]) -> list[str]:
    """`find` over one filesystem, pruning `prune`, printing big files NUL-split."""
    argv = ["find", "-x", str(home)]
    if prune:
        argv.append("(")
        for i, p in enumerate(prune):
            if i:
                argv.append("-o")
            argv += ["-path", p]
        argv += [")", "-prune", "-o"]
    argv += ["-type", "f", "-size", f"+{FIND_MIN_KIB}k", "-print0"]
    return argv


def split_paths(raw: bytes) -> list[pathlib.Path]:
    return [pathlib.Path(p.decode()) for p in raw.split(b"\0") if p]


def parse_isexcluded(out: str) -> bool:
    return out.lstrip().startswith("[Excluded]")


def check(
    files: Iterable[tuple[pathlib.Path, int]],
    is_excluded: Callable[[pathlib.Path], bool],
) -> Verdict:
    models: list[tuple[pathlib.Path, int]] = []
    other: list[tuple[pathlib.Path, int]] = []
    for path, size in files:
        if size < GIB or is_excluded(path):
            continue
        (models if is_model_file(path, size) else other).append((path, size))
    return Verdict(included_models=models, other_large=other)


def tmutil_isexcluded(path: pathlib.Path) -> bool:
    out = subprocess.run(
        ["tmutil", "isexcluded", str(path)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return parse_isexcluded(out)


def allocated(st: os.stat_result) -> int:
    """Bytes the file occupies on disk, which is what a backup copies.

    Not `st_size`: a sparse disk image reports its apparent size. OrbStack's
    `data.img.raw` read 3.17 TB apparent and held 904 MB (2026-09-19), and by
    apparent size it alone was 97% of the "large files" total.
    """
    return st.st_blocks * 512


def large_files(
    home: pathlib.Path, prune: Sequence[str]
) -> list[tuple[pathlib.Path, int]]:
    # find exits non-zero on unreadable (TCC-protected) directories; the paths
    # it did reach are still the answer, so the status is not checked.
    raw = subprocess.run(
        find_argv(home, prune), capture_output=True, check=False
    ).stdout
    out: list[tuple[pathlib.Path, int]] = []
    for p in split_paths(raw):
        try:
            out.append((p, allocated(p.stat())))
        except OSError:
            continue
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--home", type=pathlib.Path, default=pathlib.Path.home())
    p.add_argument("--list", action="store_true", help="name the other large files")
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    try:
        prefs = plistlib.loads(PREFS.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        prefs = {}
    prune = prunable(args.home, skip_paths(prefs))
    logger.info("walking %s, skipping %d fixed exclusions", args.home, len(prune))
    verdict = check(large_files(args.home, prune), tmutil_isexcluded)
    for path, size in verdict.included_models:
        logger.error("INCLUDED model file %.1f GiB: %s", size / GIB, path)
    logger.info(
        "%d other large file(s) included, %.1f GiB (not a failure%s)",
        len(verdict.other_large),
        sum(s for _, s in verdict.other_large) / GIB,
        "" if args.list else "; --list names them",
    )
    if args.list:
        for path, size in verdict.other_large:
            logger.info("  %.1f GiB  %s", size / GIB, path)
    if verdict.exit_code:
        logger.error(
            "%d model file(s) would be backed up; exclude their directory with "
            "`sudo tmutil addexclusion -p <dir>`",
            len(verdict.included_models),
        )
    else:
        logger.info("OK: no model file is included in Time Machine")
    return verdict.exit_code


if __name__ == "__main__":
    sys.exit(main())
