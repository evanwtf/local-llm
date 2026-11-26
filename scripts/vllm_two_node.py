"""Serve one vLLM model across both DGX Sparks, tensor parallel over the fabric.

A model card's DGX Spark recipe is a single-node command. Running it on the
cluster needs the same `vllm serve` arguments on both nodes, plus the parts a
single node never needs: `--nnodes 2`, a rank per node, a headless worker, and
NCCL pinned to every fabric interface and HCA. This builds both `docker run`
commands from one argument list, so the two nodes cannot drift apart (#1231).

The flags and environment come from the MiaAI-Lab two-node recipe that the
cluster already serves Qwen with (`~/qwen38-dual-latest/start.sh`, @cd839d0).

    uv run python scripts/vllm_two_node.py start \
        --model-dir ~/models/<checkpoint> --image vllm/vllm-openai:v0.32.0 \
        --name supervl -- --served-model-name nemotron-3.5-super-vl ...
    uv run python scripts/vllm_two_node.py stop --name supervl

Everything after `--` is passed to `vllm serve` on both nodes unchanged. Paths
under `~/models` are mounted at `/models` on both nodes, so a drafter path in
those arguments is written as `/models/<drafter>`.

`--env KEY=VALUE` (repeatable) adds an environment variable to both
containers, for a model card that needs one (#1011: GLM-5.3-Flash wants
`VLLM_USE_DEEP_GEMM=0`). The fabric setup must stay the same on every arm, so
the launcher refuses a key that starts with NCCL_, GLOO_, UCX_, TP_, NVSHMEM_
or MASTER_, and any key the launcher sets itself. A key must match
`[A-Za-z_][A-Za-z0-9_]*`.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

HEAD_IP = "10.0.0.1"
WORKER_IP = "10.0.0.2"
#: Every fabric interface and HCA. Naming one path of a QSFP cage walls the
#: link at about 112 Gb/s (see scripts/cluster_allreduce.py).
IFACES = "enp1s0f1np1,enP2p1s0f1np1,enp1s0f0np0,enP2p1s0f0np0"
IB_HCAS = "rocep1s0f1,roceP2p1s0f1,rocep1s0f0,roceP2p1s0f0"
IB_GID_INDEX = 3
MASTER_PORT = 50000
MODELS_DIR = "~/models"
#: `--env` may not set these: they steer the fabric, and every arm shares it.
FABRIC_PREFIXES = ("NCCL_", "GLOO_", "UCX_", "TP_", "NVSHMEM_", "MASTER_")
ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def docker_run(
    *,
    rank: int,
    name: str,
    image: str,
    model: str,
    port: int,
    vllm_args: list[str],
    extra_env: dict[str, str] | None = None,
) -> list[str]:
    """The `docker run` argv for one node. Rank 0 serves the API."""
    host_ip = HEAD_IP if rank == 0 else WORKER_IP
    env = {
        "GLOO_SOCKET_IFNAME": IFACES,
        "NCCL_SOCKET_IFNAME": IFACES,
        "TP_SOCKET_IFNAME": IFACES,
        "NCCL_IB_DISABLE": "0",
        "NCCL_IB_HCA": IB_HCAS,
        "NCCL_IB_GID_INDEX": str(IB_GID_INDEX),
        "NCCL_IB_AUTO_DETECT": "0",
        "NCCL_DEBUG": "WARN",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "VLLM_HOST_IP": host_ip,
    }
    for key, value in (extra_env or {}).items():
        if not ENV_KEY.fullmatch(key):
            raise ValueError(f"--env {key!r} is not a valid variable name")
        if key in env or key.startswith(FABRIC_PREFIXES):
            raise ValueError(f"--env {key} would change the fabric setup")
        env[key] = value
    argv = [
        "docker", "run", "-d", "--name", f"{name}-rank{rank}",
        "--gpus", "all", "--network", "host", "--ipc", "host",
        "--cap-add", "SYS_NICE",
        "--ulimit", "memlock=-1", "--ulimit", "stack=67108864",
        "--device", "/dev/infiniband:/dev/infiniband",
        "-v", f"{MODELS_DIR}:/models:ro",
    ]  # fmt: skip
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    argv += ["--entrypoint", "vllm", image, "serve", f"/models/{model}"]
    argv += vllm_args
    argv += [
        "--tensor-parallel-size", "2",
        "--distributed-executor-backend", "mp",
        "--nnodes", "2",
        "--master-addr", HEAD_IP,
        "--master-port", str(MASTER_PORT),
        "--node-rank", str(rank),
    ]  # fmt: skip
    if rank == 0:
        argv += ["--host", "0.0.0.0", "--port", str(port)]
    else:
        argv.append("--headless")
    return argv


def shell_line(argv: list[str]) -> str:
    """Quote an argv for a remote shell, keeping `~/models` expandable."""
    quoted = shlex.join(argv)
    return quoted.replace(
        shlex.quote(f"{MODELS_DIR}:/models:ro"), "~/models:/models:ro"
    )


def parse_env(pairs: list[str]) -> dict[str, str]:
    """`KEY=VALUE` strings to a dict; a pair with no `=` or no key is an error."""
    env: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"--env needs KEY=VALUE, got {pair!r}")
        env[key] = value
    return env


def start(args: argparse.Namespace) -> int:
    model = pathlib.Path(args.model_dir).expanduser().name
    common = {
        "name": args.name,
        "image": args.image,
        "model": model,
        "port": args.port,
        "vllm_args": args.vllm_args,
        "extra_env": parse_env(args.env),
    }
    worker = shell_line(docker_run(rank=1, **common))
    head = shell_line(docker_run(rank=0, **common))
    logger.info("worker: %s", worker)
    subprocess.run(["ssh", "-o", "BatchMode=yes", WORKER_IP, worker], check=True)
    logger.info("head: %s", head)
    subprocess.run(["bash", "-c", head], check=True)
    logger.info(
        "started %s-rank0 (API :%d) and %s-rank1", args.name, args.port, args.name
    )
    return 0


def stop(args: argparse.Namespace) -> int:
    for host, rank in (("localhost", 0), (WORKER_IP, 1)):
        cmd = f"docker rm -f {shlex.quote(f'{args.name}-rank{rank}')}"
        subprocess.run(["ssh", "-o", "BatchMode=yes", host, cmd], check=False)
        logger.info("removed %s-rank%d on %s", args.name, rank, host)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser(
        "start", help="start rank 1 on the worker, then rank 0 here"
    )
    p_start.add_argument("--model-dir", required=True)
    p_start.add_argument("--image", required=True)
    p_start.add_argument("--name", required=True)
    p_start.add_argument("--port", type=int, default=8888)
    p_start.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="extra environment for both containers (repeatable)",
    )
    p_start.add_argument("vllm_args", nargs=argparse.REMAINDER)
    p_stop = sub.add_parser("stop", help="remove both containers")
    p_stop.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "start":
        if args.vllm_args[:1] == ["--"]:
            args.vllm_args = args.vllm_args[1:]
        return start(args)
    return stop(args)


if __name__ == "__main__":
    logs.configure()
    sys.exit(main())
