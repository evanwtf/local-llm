"""Which engine runtimes exist on which (OS, arch), and how each is obtained. #324

The fleet is three points, not two operating systems:

- (Darwin, arm64): the M5 Max, Metal.
- (Linux, aarch64): the DGX Spark, CUDA.
- (Linux, x86_64): the 3080 Ti desktop, CUDA.

The rows already record `env.arch`, so the data keeps the two Linux machines
apart. The code did not: nothing asked whether an engine can run on this host
at all. aarch64 Linux is the thin corner. Prebuilt wheels and images ship for
x86_64 first, so the Spark often needs a source build where the desktop gets a
wheel, and MLX has no Linux arm64 runtime at all (#293).

`RUNTIMES` is the one table for that. Each entry says how this project obtains
the engine on that host: `prebuilt` (a release archive, a wheel, an image, an
installer) or `source` (a build from a checkout). An empty set means the engine
has no runtime there, and `note` says why. A host that is not listed is
**unknown**: nobody has tried, so nothing refuses.

`refusal()` turns a "no runtime" entry into a refusal before a batch starts,
instead of a server that never comes up an hour in. Every engine `tasks.toml`
names must have a row here (`test_runtimes.py`), so a new engine is placed in
the table on purpose.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import platform
import urllib.parse
from collections.abc import Mapping

logger = logging.getLogger("agent-bench")

Host = tuple[str, str]

DARWIN_ARM64: Host = ("Darwin", "arm64")
LINUX_AARCH64: Host = ("Linux", "aarch64")
LINUX_X86_64: Host = ("Linux", "x86_64")

#: The loopback names a local backend's `base_url` uses (as in remote.py).
LOOPBACK = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


@dataclasses.dataclass(frozen=True)
class Runtime:
    """How the engine is obtained on one host. Empty `ways`: it cannot run."""

    ways: frozenset[str]
    note: str


def _rt(note: str, *ways: str) -> Runtime:
    return Runtime(frozenset(ways), note)


_NO_METAL = "a CUDA engine: no Metal build"
_NO_MLX = "MLX ships no Linux arm64 runtime (#293)"

#: engine -> host -> Runtime. Evidence: the ledgers' rows per machine and the
#: `engine_tree` / `image` / `recipe_dir` each backend declares.
RUNTIMES: dict[str, dict[Host, Runtime]] = {
    "atlas": {
        LINUX_AARCH64: _rt("an image built locally from the Atlas fork", "source"),
    },
    "ds4": {
        DARWIN_ARM64: _rt("built from a ds4 checkout (Metal)", "source"),
        LINUX_AARCH64: _rt("built from a ds4 checkout (CUDA)", "source"),
    },
    "llama.cpp": {
        DARWIN_ARM64: _rt("built from a llama.cpp checkout (Metal)", "source"),
        LINUX_AARCH64: _rt("built from a tagged llama.cpp checkout (CUDA)", "source"),
        LINUX_X86_64: _rt("built from a llama.cpp checkout (CUDA)", "source"),
    },
    "mlx-serve": {
        DARWIN_ARM64: _rt("built from an mlx-serve checkout", "source"),
        LINUX_AARCH64: _rt(_NO_MLX),
    },
    # Measured on the M5 Max only, and how it was installed is not recorded.
    "mtplx": {},
    "ollama": {
        DARWIN_ARM64: _rt("the release installer", "prebuilt"),
        LINUX_AARCH64: _rt("the release installer", "prebuilt"),
        LINUX_X86_64: _rt("the release installer", "prebuilt"),
    },
    "sglang": {
        DARWIN_ARM64: _rt(_NO_METAL),
        LINUX_AARCH64: _rt("a released CUDA image", "prebuilt"),
        LINUX_X86_64: _rt("released wheels and images", "prebuilt"),
    },
    "sushi": {
        DARWIN_ARM64: _rt("an mlx-serve fork built from source", "source"),
        LINUX_AARCH64: _rt(_NO_MLX),
    },
    "tandemllm": {
        LINUX_AARCH64: _rt("its install.sh, from a tagged checkout", "source"),
    },
    "tensorfold": {
        LINUX_AARCH64: _rt("a linux-aarch64 release archive", "prebuilt"),
    },
    "vllm": {
        DARWIN_ARM64: _rt(_NO_METAL),
        LINUX_AARCH64: _rt("released images and wheels for aarch64", "prebuilt"),
        LINUX_X86_64: _rt("released wheels and images", "prebuilt"),
    },
}


def host(system: str | None = None, machine: str | None = None) -> Host:
    """(OS, arch) for this machine, or for the values given, in the table's names.

    macOS says `arm64` and Linux says `aarch64` for the same architecture, so
    the arch follows the OS's own spelling.
    """
    system = system or platform.system()
    arch = (machine or platform.machine()).lower()
    if arch in ("amd64", "x64"):
        arch = "x86_64"
    if arch in ("arm64", "aarch64"):
        arch = "arm64" if system == "Darwin" else "aarch64"
    return (system, arch)


def label(h: Host) -> str:
    return f"{h[0]} {h[1]}"


def lookup(engine: str, h: Host | None = None) -> Runtime | None:
    """The engine's runtime on `h` (default: here), or None when unknown."""
    return RUNTIMES.get(engine.lower(), {}).get(h or host())


def _is_local(backend: Mapping[str, object]) -> bool:
    url = str(backend.get("engine_url") or backend.get("base_url") or "")
    return bool(url) and urllib.parse.urlsplit(url).hostname in LOOPBACK


def refusal(
    backends: Mapping[str, Mapping[str, object]], where: Host | None = None
) -> str | None:
    """Refuse when a backend served on this host names an engine with no runtime.

    Only a local backend is this host's question. A remote run (#562) serves
    the engine on another machine, and a hosted backend has no engine at all.
    The test suite's `LOCAL_LLM_ANY_PLATFORM=1` also skips it: its engines are
    fakes.
    """
    if os.environ.get("LOCAL_LLM_SERVER_HOST", "").strip():
        return None
    # The suite fakes every engine and sets this for the whole session
    # (`scripts/platform_guard.py`, #325). Nothing else should set it.
    if os.environ.get("LOCAL_LLM_ANY_PLATFORM") == "1":
        return None
    here = where or host()
    gaps = []
    for name, backend in backends.items():
        engine = str(backend.get("engine") or "").lower()
        if not engine or not _is_local(backend):
            continue
        rt = lookup(engine, here)
        if rt is not None and not rt.ways:
            gaps.append(f"{name} ({engine}: {rt.note})")
    if not gaps:
        return None
    return (
        f"REFUSING: no runtime on {label(here)} for {'; '.join(gaps)}. "
        "Run it on a machine that has one, or record a new runtime in "
        "benchmarks/agent/runtimes.py (#324)."
    )


def describe(h: Host | None = None) -> list[str]:
    """One line per engine: how it is obtained on `h`, for the preflight log."""
    here = h or host()
    lines = []
    for engine in sorted(RUNTIMES):
        rt = RUNTIMES[engine].get(here)
        if rt is None:
            lines.append(f"{engine}: unknown")
        elif not rt.ways:
            lines.append(f"{engine}: none ({rt.note})")
        else:
            lines.append(f"{engine}: {'+'.join(sorted(rt.ways))} ({rt.note})")
    return lines
