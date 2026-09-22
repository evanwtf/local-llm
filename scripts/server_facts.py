#!/usr/bin/env python3
"""Write the facts a remote client needs about this server. #562

Run on the machine that serves the model, then copy the file to the client and
point `LOCAL_LLM_SERVER_FACTS` at it. The client's rows then describe this
machine's hardware and land in this machine's ledger, and carry the server's
launch argv for the #213 pooling guard, which the client cannot read from its
own process table.

    uv run python scripts/server_facts.py --backend <name> --out /tmp/server-facts.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import cluster_id
import hardware_id
import preflight


def collect(
    backend: str, tasks_file: pathlib.Path, cluster_peer: str | None = None
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
    return {
        "directory": directory,
        "backend": backend,
        "facts": preflight.machine_facts(),
        "env": run.capture_versions(
            cfg, {backend: cfg["backend"][backend]}, allow_unstamped=True
        ),
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
    args = p.parse_args(argv)
    data = collect(args.backend, args.tasks_file, args.cluster_peer)
    args.out.write_text(json.dumps(data, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {
                "directory": data["directory"],
                "backend": data["backend"],
                "server_argv": bool(data["env"].get("server_argv")),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
