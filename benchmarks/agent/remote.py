"""Run the harness on a client machine against a model served elsewhere. #562

The DGX Spark is meant to be a model server that other machines' agents call
over the LAN. Until #562 every DGX agent row was taken with OpenCode on the
Spark itself, so the 128 GiB pool held the model, its KV cache and the trial at
once, and every server ran below what the box could give it. In remote mode the
harness and OpenCode run on a client, and the server's machine holds only the
server.

Three things in the harness assumed the server is local, and this module is
what replaces each of them:

- **Where the server is.** Backends in `tasks.toml` say `127.0.0.1`. The server
  host comes from `LOCAL_LLM_SERVER_HOST`, never from a committed address (the
  repo is public), and `rewrite()` swaps it into every URL a backend carries.
- **Whose memory the gates read.** The memory gate and the #485 headroom gate
  protect the *server's* pool. `mem_available_gib()` reads it from the server's
  node_exporter over HTTP instead of from the client's `/proc/meminfo`.
- **Which hardware a row describes.** The ledger is one file per machine (#20),
  keyed on the row's `env.arch` / `env.cpu`. A remote row belongs to the
  **server's** ledger, so `stamp()` puts the server's facts in those fields and
  keeps the client's under `env.client_machine`, with `env.topology = "remote"`.
  The server's facts, and its launch argv, come from a JSON file written on the
  server by `scripts/server_facts.py` and named by `LOCAL_LLM_SERVER_FACTS`.

Nothing here runs unless `LOCAL_LLM_SERVER_HOST` is set, so a local run is
unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("agent-bench")

ENV_HOST = "LOCAL_LLM_SERVER_HOST"
ENV_FACTS = "LOCAL_LLM_SERVER_FACTS"
NODE_EXPORTER_PORT = 9100
DCGM_EXPORTER_PORT = 9400
#: Every backend field that names the server.
URL_KEYS = ("base_url", "models_url", "props_url", "engine_url")
LOOPBACK = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
#: The row fields that identify hardware (results.HARDWARE_KEYS plus the rest of
#: preflight.machine_facts). In remote mode these describe the server.
SERVER_FACT_KEYS = (
    "arch",
    "os",
    "cpu",
    "cpu_count",
    "memory_gib",
    "gpu",
    "metal_ceiling_gib",
    "metal_ceiling_raised",
)
GIB = 1024**3


def host() -> str | None:
    """The server host, or None for a local run."""
    value = os.environ.get(ENV_HOST, "").strip()
    return value or None


def rewrite_url(url: str | None, server: str) -> str | None:
    """`url` with a loopback host replaced by `server`; anything else unchanged."""
    if not url:
        return url
    parts = urllib.parse.urlsplit(url)
    if parts.hostname not in LOOPBACK:
        return url
    netloc = server if parts.port is None else f"{server}:{parts.port}"
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))


def rewrite(backend: dict, server: str) -> dict:
    """A copy of `backend` whose URLs point at `server` instead of loopback."""
    out = dict(backend)
    for key in URL_KEYS:
        if key in out:
            out[key] = rewrite_url(out[key], server)
    return out


def node_exporter_url(server: str) -> str:
    return f"http://{server}:{NODE_EXPORTER_PORT}/metrics"


def parse_node_meminfo(text: str) -> dict[str, float]:
    """MemTotal / MemAvailable / MemFree in GiB from node_exporter text."""
    got: dict[str, float] = {}
    for key in ("MemTotal", "MemAvailable", "MemFree"):
        m = re.search(
            rf"^node_memory_{key}_bytes(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)\s*$",
            text,
            re.MULTILINE,
        )
        if m:
            got[key] = float(m.group(1)) / GIB
    return got


def server_meminfo(server: str, timeout: float = 5.0) -> dict[str, float]:
    """The server's memory, or {} when node_exporter cannot be read."""
    try:
        with urllib.request.urlopen(node_exporter_url(server), timeout=timeout) as r:
            return parse_node_meminfo(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("could not read the server's memory from node_exporter: %s", exc)
        return {}


def mem_available_gib(server: str) -> float | None:
    """The server's MemAvailable in GiB, or None when it cannot be read."""
    return server_meminfo(server).get("MemAvailable")


def parse_dcgm_watts(text: str) -> float | None:
    """Summed `DCGM_FI_DEV_POWER_USAGE` across GPUs, or None if absent."""
    values = [
        float(m.group(1))
        for m in re.finditer(
            r"^DCGM_FI_DEV_POWER_USAGE(?:\{[^}]*\})?\s+([0-9.eE+-]+)\s*$",
            text,
            re.MULTILINE,
        )
    ]
    return sum(values) if values else None


def gpu_watts(server: str, timeout: float = 5.0) -> float | None:
    """The server's GPU power from its DCGM exporter, or None if unreadable.

    The idle-stall watchdog's sampler in remote mode: the client has no GPU,
    and `timeout_policy.gpu_watts` would read None forever, which the watchdog
    holds rather than counts as idle -- safe, but it disables the watchdog.
    """
    url = f"http://{server}:{DCGM_EXPORTER_PORT}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return parse_dcgm_watts(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def server_facts(path: str | None = None) -> dict | None:
    """The facts `scripts/server_facts.py` wrote on the server, or None."""
    path = path or os.environ.get(ENV_FACTS)
    if not path:
        return None
    try:
        return json.loads(pathlib.Path(path).expanduser().read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{ENV_FACTS}={path}: cannot read server facts: {exc}")


#: Fields that describe the machine running the trial, not the server. They stay
#: as the client measured them; everything else the server reported wins.
CLIENT_KEYS = frozenset(
    {
        "claude",
        "opencode",
        "codex",
        "aider",
        "ollama",
        "harness_head",
        "harness_dirty",
        "target_commit",
        "samplers",
        "client",
        "confinement",
        "macos",
        "machine",
    }
)


def stamp(env: dict, facts: dict) -> dict:
    """`env` re-described for a remote run: server hardware, client kept aside.

    `env` arrives holding the client's machine facts. They move to
    `env["client_machine"]`; the server's hardware facts and its engine
    provenance (from `scripts/server_facts.py`) take their place, so the row's
    hardware identity is the server's and it belongs in the server's ledger.
    """
    out = dict(env)
    out["client_machine"] = {
        k: env[k] for k in (*SERVER_FACT_KEYS, "confinement") if k in env
    }
    for key in SERVER_FACT_KEYS:
        out.pop(key, None)
    for key, value in (facts.get("env") or {}).items():
        if key not in CLIENT_KEYS and value is not None:
            out[key] = value
    out.update(
        {k: v for k, v in (facts.get("facts") or {}).items() if k in SERVER_FACT_KEYS}
    )
    out["topology"] = "remote"
    out["server_machine"] = facts.get("directory")
    return out


def topology_mismatch(backends: dict[str, dict], remote_mode: bool) -> list[str]:
    """Backends whose declared `topology` does not match this run.

    A remote row and a local row of the same backend name would pool in every
    table keyed by backend (`gen_tables`, `reco_rows`), although they measure
    different things: a remote trial carries the LAN and the client's CPU. So a
    remote run needs a backend declared `topology = "remote"` in `tasks.toml`,
    and a local run refuses one.
    """
    want = "remote" if remote_mode else None
    return sorted(n for n, b in backends.items() if (b.get("topology") or None) != want)


def results_path(repo: pathlib.Path, facts: dict) -> pathlib.Path:
    """The server's ledger: rows describe the server, so they live there."""
    return repo / "hardware" / facts["directory"] / "results.jsonl"
