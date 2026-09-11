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
import os
import pathlib
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent

# The engines this repo runs, and how to find their build. `binary_rel` is
# the binary's path inside the tree; `binary` is a brew binary resolved on
# PATH. There is deliberately no `tree` default here: ds4 has four trees on
# this machine and llama.cpp's is set at run time by LLAMACPP_ROOT, so no one
# answer is right for every caller. A backend names its tree with `engine_tree`
# (ds4) or the harness sets LLAMACPP_ROOT (llama.cpp); an engine with neither
# resolves to nothing, which is the "never guess" rule, not a gap.
ENGINES: dict[str, dict[str, str]] = {
    "ds4": {"binary_rel": "ds4-server"},
    "llama.cpp": {"binary_rel": "build/bin/llama-server"},
    "ollama": {"binary": "ollama"},
    "mtplx": {"binary": "mtplx"},
    # mlx-serve is both kinds (#225): a brew install (no tree, `binary`) and a
    # git checkout built by zig (`binary_rel` is the zig build output). A
    # tree-based arm must record its own binary's mtime, not the brew one's --
    # the mtime is the one fact that survives a rebuild from uncommitted code.
    "mlx-serve": {"binary": "mlx-serve", "binary_rel": "zig-out/bin/mlx-serve"},
}


# --- the draft path (#224) ---------------------------------------------------
#
# #191 ran two arms for three and a half hours before anyone established that
# one of them was speculating and the other was not. mlx-serve enables
# prompt-lookup decoding by default; the ds4 arm was launched with no MTP
# sidecar. Neither fact reached a row, so "did this arm speculate?" could only
# be answered by grepping server logs after the fact -- and the harness's own
# `ds4-mtp-timing` warning pointed the opposite way (#222).
#
# The state is read from the RUNNING process's argv, not from a config file
# and not from what a caller believes it passed. argv is what the kernel was
# given, so it cannot drift from what is actually serving.
#
# It is deliberately a launch-time fact, not a per-request one. mlx-serve
# resolves its draft source by priority (MTP > drafter > PLD) and PLD
# self-gates per request on n-gram score, so `pld: "on"` means "not
# force-disabled at launch", NOT "drafted on every token". A row saying "on"
# with no drafting in its server log is consistent; a row saying "off" with
# drafting is not, and would be a bug worth chasing.


def _argv_of(binary: str) -> list[str] | None:
    """argv of the running process for `binary`, or None if it is not up."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", f"[{binary[0]}]{binary[1:]} "],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pids = [p for p in out.stdout.split() if p.isdigit()]
    if not pids:
        return None
    try:
        args = subprocess.run(
            ["ps", "-ww", "-o", "args=", "-p", pids[0]],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return args.stdout.split() or None


def _pld_from_argv(argv: list[str] | None) -> str:
    """ "on"/"off"/"n/a" from one argv snapshot; the pure rule, shared so a
    caller that already holds an argv derives `pld` from the *same* snapshot as
    the draft fields (a restart between two probes must not mix them)."""
    if argv is None:
        return "n/a"
    return "off" if "--no-pld" in argv else "on"


def pld_state() -> str:
    """ "on", "off", or "n/a" -- never a bool, and never a guess.

    "n/a" means no mlx-serve process is running, which is a different fact
    from "off" and must never collapse into it. That is the same discipline
    `engine_dirty` uses: an absent answer is not a negative one.
    """
    return _pld_from_argv(_argv_of("mlx-serve"))


#: The inference servers whose build identity belongs on a log line, in the
#: order they are looked for. One is resident at a time on this machine.
RESIDENT = ("mlx-serve", "ds4-server")


def _sha_of_tree(path: pathlib.Path) -> str | None:
    """Short sha of the worktree containing `path`, or None if it is not one."""
    sha = _git("rev-parse", "--short", "HEAD", cwd=path)
    if not sha:
        return None
    status = _git("status", "--porcelain", cwd=path)
    return f"{sha}-dirty" if status else sha


def running_engine() -> str:
    """Which engine BUILD is serving right now, as "<name>/<version>".

    Read from the running process's argv[0], not from PATH and not from a
    config: #225 runs two mlx-serve builds against each other, and a bare
    `shutil.which` would resolve both arms to the brew binary while the record
    claimed otherwise. The path the kernel was given is the only thing that
    cannot be wrong.

    A binary inside a git worktree reports its sha, because that is what
    identifies a build from source -- `mlx-serve --version` on the PR383 build
    prints no version string at all. A binary outside one reports the version
    directory brew installed it under. "none" means nothing is serving, which
    is a fact worth logging rather than a blank.
    """
    for name in RESIDENT:
        argv = _argv_of(name)
        if not argv:
            continue
        binary = pathlib.Path(argv[0])
        if not binary.is_absolute():
            found = shutil.which(binary.name)
            if not found:
                return f"{name}/unknown"
            binary = pathlib.Path(found)
        resolved = binary.resolve()
        parts = resolved.parts
        # Brew FIRST, and this order is the whole correctness of the function.
        # /opt/homebrew is itself a git checkout, so `git rev-parse` inside the
        # Cellar answers with HOMEBREW's HEAD: probing git first labelled the
        # 26.9.1 arm `mlx-serve/08e85c4e42`, a real sha of the wrong repo, while
        # the source arm reported its own. Two arms both labelled with a sha and
        # no way to tell which is which is worse than no label at all.
        if "Cellar" in parts:
            i = parts.index("Cellar")
            if len(parts) > i + 2:
                return f"{name}/{parts[i + 2]}"
        sha = _sha_of_tree(resolved.parent)
        if sha:
            return f"{name}/{sha}"
        return f"{name}/unknown"
    return "none"


def _default_tree(engine: str) -> pathlib.Path | None:
    """The tree an engine runs on when the caller names none.

    ds4 has no default -- a backend must name its tree, because four exist and
    a wrong one is worse than none. llama.cpp's tree is set at run time by
    `LLAMACPP_ROOT`, the same env var the harness reads; the fallback is the
    documented default, not a guess.
    """
    if engine == "llama.cpp":
        return pathlib.Path(
            os.environ.get("LLAMACPP_ROOT", "~/git/llama.cpp")
        ).expanduser()
    return None


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
    else:
        tree_path = _default_tree(engine)
    got: dict[str, object] = {"engine_name": engine}

    # A git tree: the sha is the version, the tree is the path, and dirty
    # means uncommitted code. A tree that is not a git checkout falls through
    # to the brew-binary path below.
    if tree_path is not None and (tree_path / ".git").exists():
        sha = _git("rev-parse", "--short", "HEAD", cwd=tree_path)
        if sha:
            got["engine_version"] = sha
        got["engine_tree"] = str(tree_path)
        # `engine_dirty` is tri-state, not a bool: None means git could not
        # answer (a failed status must not read as "clean"), "" means clean,
        # and any output means uncommitted code. A clean tree omits the key
        # rather than writing false -- an absent key must not read as "dirty".
        status = _git("status", "--porcelain", cwd=tree_path)
        if status is None:
            got["engine_dirty"] = "unknown"
        elif status:
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
    # It is written in ISO 8601 with an explicit offset, the same shape the
    # harness uses for every timestamp, so a row is comparable across runs.
    binary = _binary_path(spec, tree_path)
    if binary is not None:
        m = _mtime(binary)
        if m is not None:
            got["engine_built"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S%z", time.localtime(m)
            )

    # The draft path, for the engines where it is a launch flag. Omitted for
    # engines where the key would be meaningless -- an absent key is not a
    # negative answer, and "ds4 has no pld field" must not read as "ds4 had
    # PLD off". ds4's own MTP state is the mirror image and is #39's.
    if engine == "mlx-serve":
        # One argv snapshot for every mlx field, so a restart between probes
        # cannot pair `pld` from one process with `draft_source` from another.
        argv = _argv_of("mlx-serve")
        got["pld"] = _pld_from_argv(argv)
        if argv is not None:
            got.update(_mlx_draft_fields(argv))
    return got


def _mlx_draft_fields(argv: list[str]) -> dict[str, object]:
    """`draft_source` and the PLD tuning for the running mlx-serve (#262).

    Resolution lives in `scripts/lib/mlx_serve.py`, the one place that knows
    mlx-serve's draft-source priority; this reaches it without depending on the
    caller's sys.path. It never raises: if the helper cannot be imported the
    keys are simply absent, which reads as "unrecorded" -- not a guess, and not
    "no speculation", the same discipline as the rest of this module.
    """
    try:
        libdir = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lib"
        if str(libdir) not in sys.path:
            sys.path.insert(0, str(libdir))
        import mlx_serve
    except ImportError:
        return {}
    return mlx_serve.draft_provenance_fields(mlx_serve.model_dir_of(argv), argv)
