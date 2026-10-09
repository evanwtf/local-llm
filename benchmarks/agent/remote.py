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
        # These two describe the CLIENT and are already filed under
        # `client_machine` above. Without them here the loop below copies the
        # SERVER's value to the row's top level: `server_facts.py` runs
        # `machine_facts()` on the server, so the server reports its own cap,
        # and a remote row then claims a cap belonging to the wrong machine.
        # `client_image` never bit only because the server's is None and the
        # loop skips None; the cap is a number, so it did (#477).
        "client_image",
        "client_mem_cap_gib",
    }
)


#: Engine provenance `run.capture_versions` reads from the local disk and
#: process table. On the client these describe the client's own engines, if
#: any, never the server's: a client vLLM venv stamped `vllm` on a remote row,
#: and that in turn deleted the server's honest `vllm_version: unknown`
#: (review of 2026-10-05). Only the server's facts may supply them. `ollama`
#: stays in CLIENT_KEYS, as before.
CLIENT_ENGINE_KEYS = frozenset(
    {
        "server_argv",
        "ds4_head",
        "ds4_dirty",
        "ds4_server_mtime",
        "gguf_path",
        "gguf_bytes",
        "gguf_mtime",
        "llamacpp_head",
        "llamacpp_dirty",
        "llamacpp_server_mtime",
        "vllm",
        "vllm_torch",
        "vllm_torch_cuda",
        "sglang",
        "sglang_image",
        "lmstudio_cli",
        "lmstudio_runtimes",
        "mtplx",
        "metal_route",
    }
)

#: The `servers` fields that come from the server's HTTP answers
#: (`run.probe_server`, `probe_openai_models`, `probe_ollama`). The client
#: asks the live server for these, so its values are current. Every other
#: field in a client's `servers` entry was read off the client's own disk
#: (`engine_identity`, the ds4 route and strip records) and is dropped.
#: `sampling_source` is not here: for Ollama it names the local Ollama
#: version, which on the client is the client's.
HTTP_PROBE_KEYS = frozenset(
    {
        "model_path",
        "model_alias",
        "build_info",
        "total_slots",
        "sampling",
        "served_model_id",
        "accepts_sampling",
        "context_length",
        "quantization",
        "arch",
        "publisher",
        "state",
        "max_context_length",
        "advertised_models",
        "requested_model",
    }
)

#: The HTTP fields that identify what is serving. The live probe and the saved
#: facts must agree on each one both carry, or the facts describe a server
#: that is no longer the one answering.
IDENTITY_KEYS = HTTP_PROBE_KEYS - {"state", "requested_model"}


def merge_servers(live: dict, saved: dict) -> dict:
    """The row's `servers`: the saved entry, with the live HTTP answers on top.

    `saved` is what the server probed when `scripts/server_facts.py` ran, and
    the facts are reusable for six hours. `live` is what the client probed
    just now. A server restarted on other weights or flags under the same
    backend name is caught here: its live identity disagrees with the saved
    one, and the run refuses rather than describe the old configuration.
    """
    merged = {}
    for name in sorted(set(live) | set(saved)):
        entry = dict(saved.get(name) or {})
        fresh = {
            k: v for k, v in (live.get(name) or {}).items() if k in HTTP_PROBE_KEYS
        }
        differ = sorted(
            k
            for k in IDENTITY_KEYS
            if k in entry and k in fresh and entry[k] != fresh[k]
        )
        if differ:
            detail = "; ".join(
                f"{k}: saved {entry[k]!r}, live {fresh[k]!r}" for k in differ
            )
            raise SystemExit(
                f"the server facts describe a different {name!r} from the one "
                f"serving now ({detail}). The server was restarted or changed "
                "since scripts/server_facts.py ran; run it again on the server "
                "and copy the new file to the client."
            )
        entry.update(fresh)
        if entry:
            merged[name] = entry
    return merged


#: How each engine marks one server instance over HTTP (#948): the kind of
#: marker and the route that carries it. A `counter` only grows while one
#: server runs and starts over after a restart. Only engines checked against a
#: live server are listed. vLLM may not export `process_start_time_seconds`
#: in Prometheus multiprocess mode, and a guessed metric name would refuse
#: every batch, so vLLM, SGLang and llama.cpp wait for a live check.
INSTANCE_PROBES: dict[str, tuple[str, str]] = {
    "tensorfold": ("counter", "/health"),  # requests_total, v0.6.0
}


def parse_instance(kind: str, text: str) -> int | None:
    """The instance marker in a server's answer, or None if it is unreadable."""
    if kind != "counter":
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    value = data.get("requests_total") if isinstance(data, dict) else None
    # bool is an int in Python; a true/false counter is not a counter.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _fetch(url: str, timeout: float) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def read_instance(
    base_url: str, engine: str, fetch=None, timeout: float = 5.0
) -> dict | None:
    """The live instance marker of the server at `base_url`, or None.

    None means the engine has no marker. A marker the server does not answer
    is recorded with `value: None`, so the check refuses rather than passes.
    """
    probe = INSTANCE_PROBES.get(engine)
    if probe is None:
        return None
    kind, route = probe
    fetch = fetch or _fetch  # looked up at call time, so a test can stub it
    parts = urllib.parse.urlsplit(base_url)
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, route, "", ""))
    try:
        value = parse_instance(kind, fetch(url, timeout))
    except (OSError, urllib.error.URLError, ValueError):
        value = None
    return {"engine": engine, "kind": kind, "value": value}


def instance_mismatch(saved: dict | None, live: dict | None, engine: str) -> str | None:
    """Why `live` is not the server instance `saved` describes, or None."""
    if engine not in INSTANCE_PROBES:
        return None
    if not saved or saved.get("value") is None:
        return (
            f"the server facts carry no {engine} instance marker (#948); run "
            "scripts/server_facts.py on the server again and copy the new file "
            "to the client"
        )
    if not live or live.get("value") is None:
        return f"cannot read the live {engine} instance marker (#948)"
    if saved.get("kind") != live.get("kind"):
        return (
            f"the server facts record a {saved.get('kind')!r} marker, but {engine} "
            f"answers with a {live.get('kind')!r} one (#948)"
        )
    if live["value"] < saved["value"]:
        return (
            f"the {engine} server restarted since scripts/server_facts.py ran: "
            f"its request counter went from {saved['value']} to {live['value']} "
            "(#948). Run it again on the server and copy the new file to the client."
        )
    return None


def check_instance(facts: dict, backends: dict[str, dict], fetch=None) -> None:
    """Refuse when the server answering is not the one the facts describe (#948).

    The HTTP identity check in `merge_servers` compares only what the server
    advertises, and a server restarted under the same name with other flags
    advertises the same. A restart resets its instance marker.
    """
    for name, spec in backends.items():
        engine = str(spec.get("engine", ""))
        live = read_instance(str(spec.get("base_url", "")), engine, fetch)
        if why := instance_mismatch(facts.get("instance"), live, engine):
            raise SystemExit(f"backend {name!r}: {why}")


def stamp(env: dict, facts: dict) -> dict:
    """`env` re-described for a remote run: server hardware, client kept aside.

    `env` arrives holding the client's machine facts. They move to
    `env["client_machine"]`; the server's hardware facts and its engine
    provenance (from `scripts/server_facts.py`) take their place, so the row's
    hardware identity is the server's and it belongs in the server's ledger.

    Raises SystemExit when the live server disagrees with the saved facts
    (`merge_servers`).
    """
    out = {
        k: v
        for k, v in env.items()
        if k not in CLIENT_ENGINE_KEYS and not k.startswith("digest_")
    }
    # "client_image" travels with the client's facts or the grouping key reads
    # None for it and a containerised row pools with a bare-metal one (#611).
    out["client_machine"] = {
        k: env[k]
        for k in (
            *SERVER_FACT_KEYS,
            "confinement",
            "client_image",
            "client_mem_cap_gib",
        )
        if k in env
    }
    out.pop("client_image", None)
    out.pop("client_mem_cap_gib", None)
    for key in SERVER_FACT_KEYS:
        out.pop(key, None)
    for key, value in (facts.get("env") or {}).items():
        if key not in CLIENT_KEYS and value is not None:
            out[key] = value
    out.update(
        {k: v for k, v in (facts.get("facts") or {}).items() if k in SERVER_FACT_KEYS}
    )
    servers = merge_servers(
        env.get("servers") or {}, (facts.get("env") or {}).get("servers") or {}
    )
    out.pop("servers", None)
    if servers:
        out["servers"] = servers
    # The client probed for engines it cannot see and stamped
    # `<engine>_version: unknown` (#320's loud fallback). The server has since
    # answered, so drop an "unknown" the server's own env contradicts -- a row
    # carrying both `sglang` and `sglang_version: unknown` says two things.
    for key in [k for k in out if k.endswith("_version")]:
        engine = key.removesuffix("_version")
        if out.get(key) == "unknown" and out.get(engine):
            del out[key]
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


def tier_mismatch(backend: str, spec: dict, directory: str) -> str | None:
    """None if `directory` is the machine that owns `backend`'s tier, else why not.

    The facts name the ledger, and a row in the wrong one cannot be repaired.
    Facts taken without `--cluster-peer` name the single Spark, because the
    head node probes the same either way, so a two-node backend's rows would
    join the single-Spark ledger. The foreign-hardware check cannot see it:
    the hardware facts are the head's in both cases (#647). So the tier, which
    `tasks.toml` declares per backend and `scripts/machines.py` maps to one
    machine, must match the machine the facts name.

    A machine outside the registry, with a tier no registered machine owns, is
    not judged: there is nothing to compare it against.
    """
    import sys

    scripts = str(pathlib.Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import machines

    tier = spec.get("tier") or None
    owners = sorted(m.directory for m in machines.MACHINES if m.tier == tier)
    here = machines.by_directory(directory)
    if here is not None and here.tier == tier:
        return None
    if here is None and (tier is None or not owners):
        return None
    if not owners:
        return (
            f"backend {backend!r} declares tier {tier!r}, which no registered "
            f"machine carries, but the server facts name {directory}"
        )
    # A cluster's directory is its node's plus `-x<nodes>` (cluster_id).
    hint = (
        " Pass --cluster-peer <peer> to scripts/server_facts.py for a multi-node "
        "server."
        if any(re.search(r"-x\d+$", o) for o in owners)
        else " Regenerate the facts without --cluster-peer for a one-node server."
    )
    return (
        f"backend {backend!r} declares tier {tier!r}, whose rows belong in "
        f"{', '.join(owners)}, but the server facts name {directory}.{hint}"
    )
