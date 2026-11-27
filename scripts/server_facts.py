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
import logging
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

logger = logging.getLogger(__name__)


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


#: Where `run.capture_versions` records the tag of the head's serving
#: container, per engine reader. A peer container must run the same image.
HEAD_IMAGE_KEYS = ("vllm_image", "sglang_image", "tensorfold_image")
#: The rank-1 markers seen in the two-node launchers: vLLM's `--node-rank 1`,
#: a `--rank 1` flag, the TensorFold Zig worker's `follow` subcommand, and
#: its `org.tensorfold.rank` label. They only break ties.
RANK_FLAGS = ("--node-rank", "--rank")
RANK_LABEL_SUFFIX = ".rank"


def _rank(info: dict[str, Any]) -> str | None:
    """The node rank a container declares, or None when it declares none."""
    _, _, cmd = node_image.container_summary(info)
    for flag in RANK_FLAGS:
        if (value := node_image.flag_value(cmd, flag)) is not None:
            return value
    labels = (info.get("Config") or {}).get("Labels") or {}
    if isinstance(labels, dict):
        for key, value in labels.items():
            if str(key).endswith(RANK_LABEL_SUFFIX):
                return str(value)
    if "follow" in " ".join(cmd).split():
        return "1"
    return None


def _is_client(info: dict[str, Any]) -> bool:
    """True for a benchmark client such as `vllm bench serve`: not a server."""
    _, _, cmd = node_image.container_summary(info)
    return "bench" in " ".join(cmd).split()


def _other_model(info: dict[str, Any], models: set[str]) -> bool:
    """True when the container names a served model that is not ours."""
    _, _, cmd = node_image.container_summary(info)
    name = node_image.flag_value(cmd, "--served-model-name")
    return name is not None and name not in models


def head_images(env: dict[str, Any], spec: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(image IDs in the peer's store, image tags) of the head's serving image.

    The ID is the one `run.serving_image` read from the worker's rank-1
    container (`env.serving_image.rank1.id`). The IDs of one image differ
    between the two nodes' stores (`node_image`), so the head's own ID is no
    use here; its tag is.
    """
    ids: set[str] = set()
    record = env.get("serving_image")
    rank1 = record.get("rank1") if isinstance(record, dict) else None
    if isinstance(rank1, dict) and rank1.get("id"):
        ids.add(str(rank1["id"]))
    tags = {str(env[k]) for k in HEAD_IMAGE_KEYS if env.get(k)}
    if spec.get("image"):
        tags.add(str(spec["image"]))
    return ids, tags


def pick_peer_container(
    infos: list[dict[str, Any]],
    env: dict[str, Any],
    spec: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """The peer's serving container, or None and what was observed.

    One rule with `run.serving_image`: when it picked the worker's rank-1
    container, that container is the answer. Otherwise the candidates are
    the peer's containers that run the head's serving image. A client
    (`vllm bench`), a container that names another served model, and a
    rank-0 container are not candidates, even when alone. Rank-1 markers
    break a tie; a tie they cannot break is unknown, never a guess.
    """
    listed = f"{len(infos)} containers listed"
    record = env.get("serving_image")
    chosen = record.get("rank1_container") if isinstance(record, dict) else None
    if chosen:
        for info in infos:
            if str(info.get("Id", "")).startswith(str(chosen)):
                return info, ""
        return None, (
            f"the rank-1 container that serving_image read ({chosen}) is not "
            f"running on the peer ({listed})"
        )
    ids, tags = head_images(env, spec)
    if not ids and not tags:
        return None, (
            "the head's serving image is not recorded, so no peer container "
            f"can be matched to it ({listed})"
        )
    same = [
        i
        for i in infos
        if str(i.get("Image", "")) in ids or node_image.container_summary(i)[1] in tags
    ]
    if not same:
        return (
            None,
            f"no running container on the peer runs the head's image ({listed})",
        )
    models = {str(spec.get("model") or "")} - {""}
    head_name = node_image.flag_value(
        str(env.get("server_argv") or "").split(), "--served-model-name"
    )
    if head_name:
        models.add(head_name)
    fit = [
        i
        for i in same
        if not _is_client(i) and not _other_model(i, models) and _rank(i) != "0"
    ]
    if len(fit) == 1:
        return fit[0], ""
    if not fit:
        return None, (
            f"{len(same)} containers on the peer run the head's image, and each "
            f"is a client, rank 0, or serves another model ({listed})"
        )
    rank1 = [i for i in fit if _rank(i) == "1"]
    if len(rank1) == 1:
        return rank1[0], ""
    return None, (
        f"{len(fit)} containers on the peer run the head's image and "
        f"{len(rank1)} carry a rank-1 marker: cannot tell which serves ({listed})"
    )


def peer_container_settings(
    peer: str, env: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any]:
    """The worker's `server_env` and `server_cap_add`, read over ssh (#680).

    Finding 2, worker half: a two-node treatment can live in the worker's
    container env or CapAdd, so the head's inspect alone cannot show it. The
    filter is the head's own (`run.engine_settings_from`): engine variables
    only, secrets dropped by name, IPv4 masked. `server_env_image` names the
    chosen container's image, so a reader can audit the pick. When no
    container is picked, or ssh fails, `server_env_missing` says what was
    observed. The row gets a fixed phrase only; ssh's own text, which can
    name hosts and paths, goes to the log.
    """
    import run

    try:
        out = cluster_id.ssh(peer, node_image.PS_INSPECT)
    except cluster_id.ClusterVerificationError as exc:
        logger.warning("cannot read the peer's containers: %s", exc)
        if exc.returncode == 3:
            why = "docker ps failed on the peer, exit 3"
        elif exc.returncode is not None:
            why = f"the container listing failed on the peer, exit {exc.returncode}"
        else:
            why = exc.category
        return {"server_env_missing": f"cannot read the peer's containers: {why}"}
    info, why = pick_peer_container(node_image.parse_ps_inspect(out), env, spec)
    if info is None:
        return {"server_env_missing": why}
    got: dict[str, Any] = run.engine_settings_from(info)
    got["server_env_image"] = node_image.container_summary(info)[1]
    if "server_env" not in got:
        got["server_env_missing"] = (
            "the peer's serving container has no variables with a recorded prefix"
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
            **peer_container_settings(cluster_peer, env, spec),
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
