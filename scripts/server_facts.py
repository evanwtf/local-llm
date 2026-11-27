#!/usr/bin/env python3
"""Write the facts a remote client needs about this server. #562

Run on the machine that serves the model, then copy the file to the client and
point `LOCAL_LLM_SERVER_FACTS` at it. The client's rows then describe this
machine's hardware and land in this machine's ledger, and carry the server's
launch argv for the #213 pooling guard, which the client cannot read from its
own process table. It refuses to write facts without that argv for an engine
whose argv it can read, unless `--no-argv-reason` says why (#213).

    uv run python scripts/server_facts.py --backend <name> --out /tmp/server-facts.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import cluster_id
import currency
import hardware_id
import node_image
import preflight

sys.path.insert(0, str(REPO / "scripts" / "lib"))
import logs


def check_current(
    backend: str, spec: dict, run: currency.Runner = currency._run
) -> str | None:
    """None if the backend's recipe and image are the latest, else the refusal.

    The operator's rule since 2026-09-28: the server does not serve a batch on
    a stale recipe or image. A remote backend that declares neither
    `recipe_dir` nor `image` refuses too -- otherwise a stack could skip the
    check just by not saying what it runs.
    """
    items = currency.backend_items(spec, run)
    if not items:
        if spec.get("topology") == "remote":
            return (
                f"preflight: backend {backend!r} declares no recipe_dir or image in "
                "tasks.toml, so its currency cannot be checked; add them"
            )
        return None
    return currency.gate(f"server:{backend}", items)


#: Engines whose launch argv `run.capture_versions` reads on the server (#213):
#: vLLM, SGLang and TensorFold from their containers or processes, llama.cpp and
#: ds4 from the process on the backend's port, TandemLLM from its checkout.
#: Facts for one of these without a `server_argv` mean the reader missed the
#: server, so they refuse unless someone states why.
ARGV_ENGINES = frozenset(
    {"vllm", "tensorfold", "sglang", "llama.cpp", "tandemllm", "ds4"}
)


def argv_gap(backend: str, engine: str, env: dict, reason: str | None) -> str | None:
    """The refusal for facts without a launch argv, or None (#213).

    A row records what the engine did, never what it was told, unless it
    carries `server_argv`; the pooling guard in results.py reads it. Every
    remote row gets one, or `server_argv_missing` with the reason, so a row
    never lacks argv silently. Sets `server_argv_missing` in `env`.
    """
    if env.get("server_argv"):
        return None
    reason = (reason or "").strip()
    if engine not in ARGV_ENGINES:
        env["server_argv_missing"] = reason or (
            f"no launch-argv reader for engine {engine!r} (#213)"
        )
        return None
    if reason:
        env["server_argv_missing"] = reason
        return None
    return (
        f"server_facts: no server_argv for backend {backend!r}: the {engine} "
        "launch-argv reader found no serving process or container for its "
        "model (#213). Check that the server runs on this machine under the "
        "backend's model name, or pass --no-argv-reason TEXT to record why "
        "the rows carry none."
    )


def parse_containers(output: str) -> list[dict[str, Any]]:
    """Each `docker inspect` JSON line in `output`, as a dict."""
    found = []
    for line in output.splitlines():
        try:
            info = json.loads(line)
        except ValueError:
            continue
        if isinstance(info, dict):
            found.append(info)
    return found


def _command(info: dict[str, Any]) -> list[str]:
    cfg = info.get("Config") or {}
    return [str(x) for x in (*(cfg.get("Entrypoint") or []), *(cfg.get("Cmd") or []))]


def _serves(info: dict[str, Any], models: set[str]) -> bool:
    """True if the container's command names one of `models`."""
    cmd = _command(info)
    if node_image.flag_value(cmd, "--served-model-name") in models:
        return True
    return any(m in " ".join(cmd).split() for m in models)


def serving_container(
    infos: list[dict[str, Any]], engine: str, models: set[str]
) -> tuple[dict[str, Any] | None, str]:
    """The peer's container that serves `engine`, or None and the reason.

    A container is a candidate when its command names the engine. With two
    or more, the one whose command names the model wins; two or more that
    name it are unknown, never a guess.
    """
    hits = [i for i in infos if engine in " ".join(_command(i)).lower()]
    if len(hits) > 1:
        named = [i for i in hits if _serves(i, models)]
        if len(named) == 1:
            return named[0], ""
        return None, (
            f"{len(hits)} running {engine} containers on the peer, "
            f"{len(named)} name {', '.join(sorted(models))}: cannot tell which serves"
        )
    if not hits:
        return None, (
            f"no running {engine} container on the peer "
            "(a native engine serves outside docker)"
        )
    return hits[0], ""


def peer_container_settings(peer: str, engine: str, models: set[str]) -> dict:
    """The worker's `server_env` and `server_cap_add`, read over ssh (#680).

    Finding 2, worker half: a two-node treatment can live in the worker's
    container env or CapAdd, so the head's inspect alone cannot show it. The
    filter is the head's own (`run.engine_settings_from`): engine variables
    only, secrets dropped by name, IPv4 masked. When no container serves, or
    ssh fails, the reason goes under `server_env_missing`, so a row never
    lacks the worker's env silently.
    """
    import run

    try:
        out = cluster_id._ssh(peer, node_image.PS_INSPECT)
    except cluster_id.ClusterVerificationError as exc:
        # The message quotes the whole script; name it instead. ssh's own
        # message can carry an address, and rows are public.
        detail = str(exc).replace(node_image.PS_INSPECT, "docker ps; docker inspect")
        return {
            "server_env_missing": run.mask_ipv4(
                f"cannot read the peer's containers: {detail}"
            )[:300]
        }
    info, why = serving_container(parse_containers(out), engine.lower(), models)
    if info is None:
        return {"server_env_missing": why}
    got: dict[str, Any] = run.engine_settings_from(info)
    if "server_env" not in got:
        got["server_env_missing"] = (
            "the peer's serving container sets no engine variables"
        )
    return got


def collect(
    backend: str,
    tasks_file: pathlib.Path,
    cluster_peer: str | None = None,
    no_argv_reason: str | None = None,
    no_image_check_reason: str | None = None,
) -> dict:
    """The server's hardware facts and the engine provenance for `backend`.

    The engine half is `run.capture_versions` itself, run here on the server:
    the llama.cpp head, the vLLM wheel and torch versions and the launch argv
    all come from this machine's filesystem and process table, which a client
    cannot see.
    """
    import tomllib

    import run

    cfg = tomllib.loads(tasks_file.read_text())
    if backend not in cfg["backend"]:
        raise SystemExit(f"unknown backend {backend!r} in {tasks_file}")
    facts, platform = hardware_id.facts_for_this_machine()
    # A two-node server is a different machine from one Spark, and its rows
    # must not land in the single-Spark ledger (#647). The peer is verified
    # before the name is used, and a failed check raises rather than falling
    # back -- see cluster_id.
    directory = (
        cluster_id.verify_and_name(cluster_peer)
        if cluster_peer
        else hardware_id.directory_name(facts, platform)
    )
    # The other half of #647: the peer check proves a cluster exists, and this
    # proves the backend needs one. A two-node backend without --cluster-peer
    # gets the single-Spark name and its rows the single-Spark ledger, and a
    # one-node backend with it gets the cluster's. Refuse both here, before
    # the file exists; run.py checks again when it reads the file.
    import remote

    why = remote.tier_mismatch(backend, cfg["backend"][backend], directory)
    if why:
        raise SystemExit(f"server_facts: {why}")
    # #948: the marker of the server instance answering now. The client
    # refuses a batch when the live marker says the server restarted since.
    spec = cfg["backend"][backend]
    instance = remote.read_instance(
        str(spec.get("base_url", "")),
        str(spec.get("engine", "")),
        token=spec.get("auth_token") or None,
    )
    if instance is None:
        raise SystemExit(
            f"server_facts: no instance check is defined for engine "
            f"{spec.get('engine')!r} (#948). Add a probe to "
            "remote.INSTANCE_PROBES, or the reason it has none to "
            "remote.NO_INSTANCE_MARKER."
        )
    if instance["kind"] != "none" and instance["value"] is None:
        raise SystemExit(
            f"server_facts: cannot read the {instance['engine']} instance marker "
            f"from {spec.get('base_url')}; start the server first (#948)."
            + remote.metrics_hint(instance["engine"])
        )
    # #1516: refuses a split or off-pin serving image unless the reason is given.
    env = run.capture_versions(
        cfg,
        {backend: cfg["backend"][backend]},
        allow_unstamped=True,
        no_image_check_reason=no_image_check_reason,
    )
    engine = str(spec.get("engine", "")).lower()
    if why := argv_gap(backend, engine, env, no_argv_reason):
        raise SystemExit(why)
    if instance["kind"] == "none":
        # #948: copied onto every row by remote.stamp, so a row never lacks
        # the restart check silently.
        env["instance_unchecked"] = instance["reason"]
    if cluster_peer:
        # #680 finding 3: `facts` (memory_gib, cpu_count) describe the head
        # alone and stay per node, so a cluster row compares with the rows
        # before it. This says the server had two nodes and what the other had.
        # Finding 2: the worker's container env and CapAdd sit with its facts.
        peer = {
            **cluster_id.peer_facts(cluster_peer),
            **peer_container_settings(
                cluster_peer, engine, {str(spec.get("model", ""))}
            ),
        }
        env["cluster"] = {"nodes": 2, "peer": peer}
    return {
        "instance": instance,
        "directory": directory,
        # The worker's LAN name, so the client's memory gates read both nodes
        # (#680 finding 9). None on a single node.
        "peer_host": cluster_id.peer_lan_name(cluster_peer) if cluster_peer else None,
        "backend": backend,
        "facts": preflight.machine_facts(),
        "env": env,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--backend", required=True, help="the backend this server serves")
    p.add_argument(
        "--tasks-file",
        type=pathlib.Path,
        default=REPO / "benchmarks" / "agent" / "tasks.toml",
    )
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument(
        "--cluster-peer",
        metavar="HOST",
        help="this server spans two nodes: the peer to verify before naming "
        "the cluster. Refuses if the peer is unreachable, is different "
        "hardware, or has no ACTIVE RDMA link -- it never falls back to the "
        "single-node name (#647)",
    )
    p.add_argument(
        "--no-image-check-reason",
        metavar="TEXT",
        help="write the facts although the serving image check refuses (#1516), "
        "and record TEXT and the refusal on every row as "
        "env.serving_image_unchecked",
    )
    p.add_argument(
        "--no-argv-reason",
        metavar="TEXT",
        help="write the facts although the launch argv could not be read, and "
        "record TEXT on every row as env.server_argv_missing (#213)",
    )
    args = p.parse_args(argv)
    import tomllib

    logs.configure()
    spec = tomllib.loads(args.tasks_file.read_text())["backend"].get(args.backend)
    if spec is None:
        raise SystemExit(f"unknown backend {args.backend!r} in {args.tasks_file}")
    why = check_current(args.backend, spec)
    if why:
        print(why, file=sys.stderr)
        return 2
    data = collect(
        args.backend,
        args.tasks_file,
        args.cluster_peer,
        args.no_argv_reason,
        args.no_image_check_reason,
    )
    # Checked by client_container.py before every client batch, so a facts
    # file reused from an earlier batch cannot skip this server's gate.
    items = currency.backend_items(spec)
    data["currency"] = currency.facts_record(args.backend, items)
    # Copied into each row's env by remote.stamp: which recipe commit ran, and
    # whether it was pinned on purpose (#1257).
    if (recipe := currency.recipe_record(items, spec)) is not None:
        data["env"]["recipe"] = recipe
    args.out.write_text(json.dumps(data, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {
                "directory": data["directory"],
                "backend": data["backend"],
                "server_argv": bool(data["env"].get("server_argv")),
                # #1514: which FlashInfer autotune cache the rows will name.
                "autotune_cache": data["env"].get("autotune_cache"),
                # #1516: the image each node serves.
                "serving_image": data["env"].get("serving_image"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
