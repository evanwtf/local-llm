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

The host that matters is the one that serves the engine. On a local run that
is this machine. On a remote run (#562) it is the server, and its (OS, arch)
comes from the server facts file (`scripts/server_facts.py`): a two-node
screen runs its client on an x86_64 laptop against the aarch64 Sparks.
`stamp()` records the answer on each row as `env.engine_runtime`.
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

#: The row field: how the backend's engine is obtained on the serving host.
ROW_KEY = "engine_runtime"
#: The word for a pair the table does not list: nobody has tried it.
UNKNOWN = "unknown"
#: The word for a pair the table lists with no runtime. A gated run never
#: writes it (the gate refuses first); only the suite's escape reaches it.
NONE = "none"
#: What a backend may declare as `engine_runtime` in tasks.toml. The question
#: is the arch one: was native code compiled on the serving host (`source`), or
#: did it arrive built (`prebuilt`)? Patched Python on a pulled image is still
#: `prebuilt`; the recipe pin records the patches. `unknown` is the honest
#: answer when the recipe cannot show which.
DECLARED = frozenset({"prebuilt", "source", UNKNOWN})
#: Image repositories that are the engine vendor's own releases. A backend on
#: one of these, with no recipe, needs no declaration: the table's one way is
#: the truth. Anything else (a recipe, a third-party or local image) can build
#: the engine differently, so it must declare (#324 review).
VENDOR_IMAGES = ("vllm/vllm-openai:", "lmsysorg/sglang:", "ollama/ollama:")


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
    # #1435: the inkling2-exl3 overlay on exllamav3 v1.6.6, its extension
    # built in the NGC PyTorch image (scripts/inkling2_two_node.py).
    "exllamav3": {
        LINUX_AARCH64: _rt(
            "a pinned overlay checkout, built in the NGC image", "source"
        ),
    },
    # #1459: NVIDIA Hyperflare's `flare` CLI, a cargo build of a tagged
    # checkout plus the repo's patch (hardware/Cortex-X925-128GB-GB10-x2/hyperflare/).
    "hyperflare": {
        DARWIN_ARM64: _rt(_NO_METAL),
        LINUX_AARCH64: _rt(
            "built from a tagged hyperflare-inference checkout", "source"
        ),
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


def needs_declaration(backend: Mapping[str, object]) -> bool:
    """True when a backend must declare `engine_runtime` in tasks.toml.

    A recipe, or an image that is not a vendor release, decides how the engine
    was built, and the engine's table entry cannot know that.
    """
    if backend.get("recipe_dir"):
        return True
    image = str(backend.get("image") or "")
    return bool(image) and not image.startswith(VENDOR_IMAGES)


def kind(engine: str, h: Host, backend: Mapping[str, object] | None = None) -> str:
    """How `engine` is obtained on `h`: `prebuilt`, `source`, `unknown` or `none`.

    These four are the only values. `none` means the table lists no runtime
    there. Otherwise the backend's declared `engine_runtime` wins. Without a
    declaration, the table answers only when it lists one way on `h`; a guess
    between two ways would be worse than `unknown`.
    """
    rt = lookup(engine, h)
    if rt is not None and not rt.ways:
        return NONE
    declared = str((backend or {}).get("engine_runtime") or "")
    if declared:
        return declared
    if rt is None or len(rt.ways) != 1:
        return UNKNOWN
    return next(iter(rt.ways))


def server_host(facts: Mapping[str, object]) -> tuple[Host | None, str | None]:
    """The server's (OS, arch) from a server facts file, or None and the reason.

    `scripts/server_facts.py` writes `preflight.machine_facts()` under `facts`:
    `arch` is `platform.machine()` and `os` is `"<system> <release>"`. A file
    without either names no host, and the caller must not guess one.
    """
    machine = facts.get("facts")
    if not isinstance(machine, Mapping):
        return None, "the server facts carry no machine facts"
    arch = str(machine.get("arch") or "").strip()
    system = str(machine.get("os") or "").strip().split(" ")[0]
    if not arch:
        return None, "the server facts carry no arch"
    if not system:
        return None, "the server facts carry no os"
    return host(system, arch), None


def _is_local(backend: Mapping[str, object]) -> bool:
    url = str(backend.get("engine_url") or backend.get("base_url") or "")
    return bool(url) and urllib.parse.urlsplit(url).hostname in LOOPBACK


def _remote(server_facts: Mapping[str, object] | None) -> bool:
    return server_facts is not None or bool(
        os.environ.get("LOCAL_LLM_SERVER_HOST", "").strip()
    )


def _served(
    backends: Mapping[str, Mapping[str, object]], remote: bool
) -> dict[str, str]:
    """name -> engine, for each backend whose engine the serving host runs.

    On a local run that is a backend on loopback. On a remote run every URL
    already names the server (`remote.rewrite`), so it is every backend with
    an engine. A hosted backend has no engine at all.
    """
    out = {}
    for name, backend in backends.items():
        engine = str(backend.get("engine") or "").lower()
        if engine and (remote or _is_local(backend)):
            out[name] = engine
    return out


def refusal(
    backends: Mapping[str, Mapping[str, object]],
    where: Host | None = None,
    server_facts: Mapping[str, object] | None = None,
) -> str | None:
    """Refuse when a backend names an engine with no runtime on its serving host.

    A local run checks the local backends against this host (`where`, for
    tests). A remote run (#562: `LOCAL_LLM_SERVER_HOST`, or `server_facts`
    given) checks every backend against the server's host from the facts. A
    facts file without the server's arch skips the check and logs why; a
    remote run without facts is refused elsewhere in run.py. An unknown pair
    logs and passes. The test suite's `LOCAL_LLM_ANY_PLATFORM=1` skips it:
    its engines are fakes.
    """
    # The suite fakes every engine and sets this for the whole session
    # (`scripts/platform_guard.py`, #325). Nothing else should set it.
    if os.environ.get("LOCAL_LLM_ANY_PLATFORM") == "1":
        return None
    remote = _remote(server_facts)
    if remote:
        if server_facts is None:
            return None
        here, why = server_host(server_facts)
        if here is None:
            logger.warning(
                "runtime check skipped for the server: %s; run "
                "scripts/server_facts.py on the server again (#324)",
                why,
            )
            return None
    else:
        here = where or host()
    gaps = []
    for name, engine in _served(backends, remote).items():
        rt = lookup(engine, here)
        if rt is None:
            logger.warning(
                "runtime for %s (%s) on %s is unknown: nobody has tried it; "
                "not refused (#324)",
                name,
                engine,
                label(here),
            )
        elif not rt.ways:
            gaps.append(f"{name} ({engine}: {rt.note})")
    if not gaps:
        return None
    return (
        f"REFUSING: no runtime on {label(here)} for {'; '.join(gaps)}. "
        "Run it on a machine that has one, or record a new runtime in "
        "benchmarks/agent/runtimes.py (#324)."
    )


def stamp(
    env_for: Mapping[str, dict[str, object]],
    backends: Mapping[str, Mapping[str, object]],
    server_facts: Mapping[str, object] | None,
    here: Host | None = None,
) -> None:
    """Set `env.engine_runtime` on each backend's row env, from the serving host.

    A local run takes this host (`here`, for tests); a remote run takes the
    server's host from the facts. Facts without the server's arch name no
    host, so the rows get no field rather than a guess. Old rows lack the
    field; nothing backfills it.
    """
    # Only this function writes the field: a value the client's env or the
    # server facts carried in is dropped, so a skip leaves no stale answer.
    for env in env_for.values():
        env.pop(ROW_KEY, None)
    remote = server_facts is not None
    if remote:
        where, why = server_host(server_facts or {})
        if where is None:
            logger.warning("no %s on the rows: %s (#324)", ROW_KEY, why)
            return
    else:
        where = here or host()
    for name, engine in _served(backends, remote).items():
        env_for[name][ROW_KEY] = kind(engine, where, backends[name])


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
