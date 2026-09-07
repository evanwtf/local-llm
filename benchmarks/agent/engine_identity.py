"""Which engine build served a row (#192).

A row in results.jsonl names the client, the harness, the target commit and
the Metal route -- but not the engine build. `metal_route` is literally
`"unrecorded"` on the rows that matter, so an engine A/B cannot say which
engine produced a number, and RECOMMENDATIONS.md has to carry a prose caveat
that the `qwen38fnds4shim` rows ran against a withdrawn build because the rows
themselves cannot say.

This module answers "which build is this" for one engine, in a way that works
for both kinds of engine this repo runs:

- a **git tree** (ds4, llama.cpp): the version is `git rev-parse --short
  HEAD`, the tree is the worktree path, and `engine_dirty` says whether the
  tree had uncommitted code when the row was taken.
- a **brew binary** (ollama, mtplx, mlx-serve): there is no tree, so the
  version is the `--version` output and the "tree" is the install directory.

`engine_dirty` and `engine_built` are not padding. A sha names a commit, not
a binary: a tree rebuilt from uncommitted changes reports a clean sha and runs
different code. That is the same class as a bare `ds4.c:NNNNN` naming nothing
without a sha (#182). The mtime of the binary is the one fact that survives
both -- a rebuilt binary has a new mtime even when the sha is unchanged.

The rule, inherited from ds4_route: **never guess.** An engine this module
cannot resolve is omitted, not reported as `unrecorded` -- `unrecorded` is a
real answer for a server the harness did not start, and must not be invented
for one it did.
"""

from __future__ import annotations

import functools
import pathlib
import shutil
import subprocess

HERE = pathlib.Path(__file__).resolve().parent

# The engines this repo runs, and how to find their build. `tree` is the
# default git tree; a backend config may override it with `engine_tree` when
# several trees of one engine exist (ds4 has four). `binary_rel` is the
# binary's path inside the tree; `binary` is a brew binary resolved on PATH.
ENGINES: dict[str, dict[str, str]] = {
    "ds4": {
        "tree": str(pathlib.Path.home() / "git/ds4-ivan-qwen38fn"),
        "binary_rel": "ds4-server",
    },
    "llama.cpp": {
        "tree": str(pathlib.Path.home() / "git/llama.cpp"),
        "binary_rel": "build/bin/llama-server",
    },
    "ollama": {"binary": "ollama"},
    "mtplx": {"binary": "mtplx"},
    "mlx-serve": {"binary": "mlx-serve"},
}


def _git(*args: str, cwd: pathlib.Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


@functools.cache
def _cmd(*argv: str) -> str | None:
    """First line of a command's stdout, or None. Never raises, never blocks."""
    try:
        r = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").strip().splitlines()
    return out[0].strip() if out and r.returncode == 0 else None


def _mtime(path: pathlib.Path) -> int | None:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return None


def _binary_path(
    spec: dict[str, str], tree: pathlib.Path | None
) -> pathlib.Path | None:
    """The absolute path to the engine's binary, or None if it cannot be found."""
    if "binary_rel" in spec and tree is not None:
        candidate = tree / spec["binary_rel"]
        return candidate if candidate.exists() else None
    if "binary" in spec:
        found = shutil.which(spec["binary"])
        return pathlib.Path(found) if found else None
    return None


def identity(engine: str, tree: str | None = None) -> dict[str, object]:
    """The build identity for one engine, or {} if it cannot be resolved.

    `tree` overrides the registry default -- a backend that runs a specific
    ds4 fork names it here, so the row records exactly which tree served it.
    """
    spec = ENGINES.get(engine)
    if not spec:
        return {}
    # A tree is only known when the caller names one or the registry declares
    # one. A brew binary has neither, and must not fall through to the current
    # directory -- which is a git repo and would report the harness HEAD as
    # the engine's version.
    tree_path = None
    if tree:
        tree_path = pathlib.Path(tree).expanduser()
    elif spec.get("tree"):
        tree_path = pathlib.Path(spec["tree"]).expanduser()
    got: dict[str, object] = {"engine_name": engine}

    # A git tree: the sha is the version, the tree is the path, and dirty
    # means uncommitted code. A tree that is not a git checkout falls through
    # to the brew-binary path below.
    if tree_path is not None and (tree_path / ".git").exists():
        sha = _git("rev-parse", "--short", "HEAD", cwd=tree_path)
        if sha:
            got["engine_version"] = sha
        got["engine_tree"] = str(tree_path)
        if _git("status", "--porcelain", cwd=tree_path):
            got["engine_dirty"] = True
    else:
        # A brew binary: no tree, so the version is the --version output and
        # the "tree" is the install directory. `engine_dirty` is n/a and is
        # omitted rather than written false -- an absent key must not read as
        # "clean", which is reserved for a git tree that verified it. The
        # version command runs against the resolved path, not the bare name,
        # so a binary not on PATH is still identified by its own --version.
        found = shutil.which(spec.get("binary", engine))
        if found:
            got["engine_tree"] = str(pathlib.Path(found).parent)
            ver = _cmd(found, "--version")
            if ver:
                got["engine_version"] = ver

    # The mtime of the binary is the one fact that survives a rebuild from
    # uncommitted changes -- a new binary has a new mtime even when the sha
    # is unchanged. This is the fact that makes `engine_dirty` meaningful.
    binary = _binary_path(spec, tree_path)
    if binary is not None:
        m = _mtime(binary)
        if m is not None:
            got["engine_built"] = m
    return got
