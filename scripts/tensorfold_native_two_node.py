"""Serve GLM-5.3-Flash on both DGX Sparks with TensorFold's native release binary.

TensorFold 1.1.x ships a native (Zig) binary that serves GLM-5.3-Flash on two
CUDA ranks from an EXL3 checkpoint. Three things about it are easy to get
wrong, and each cost a failed start on 2026-10-24 (#1095):

- **NCCL is not in the archive.** The binary loads `libnccl.so.2` at runtime and
  stops with `NcclUnavailable` when the host has none. The Sparks have no host
  NCCL, so `LD_LIBRARY_PATH` points at a copy taken from the vLLM image
  (`--nccl-dir`).
- **Rank 0 starts first.** Rank 0 hosts the `NCCL_COMM_ID` rendezvous. A rank 1
  started before it exits at once with "rank 0 stopped (NcclUnavailable)". This
  is the reverse of the Python 0.6 engine's order.
- **The defaults are not an agent's.** With no flags the server gives an
  8,192-token context to 8 streams. Pass the serving flags after `--`.

    uv run python scripts/tensorfold_native_two_node.py start \
        --binary ~/tensorfold-native/tensorfold-1.1.3-linux-aarch64/bin/tensorfold-native \
        --nccl-dir ~/tensorfold-native/nccl --model <checkpoint dir> --name glm \
        -- --port 8888 --name GLM-5.3-Flash-EXL3 --context 262144 --parallel 4
    uv run python scripts/tensorfold_native_two_node.py stop --name glm

The binary, the NCCL copy and the checkpoint must sit at the same paths on
both nodes. Each rank runs under `timeout`, so a forgotten server stops itself.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import shlex
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

HEAD_IP = "10.0.0.1"
WORKER_IP = "10.0.0.2"
#: Every fabric interface and HCA (see scripts/cluster_allreduce.py).
IFACES = "enp1s0f1np1,enP2p1s0f1np1,enp1s0f0np0,enP2p1s0f0np0"
IB_HCAS = "rocep1s0f1,roceP2p1s0f1,rocep1s0f0,roceP2p1s0f0"
IB_GID_INDEX = 3
COMM_PORT = 29591
#: Seconds rank 1 waits for rank 0 to open the rendezvous.
RANK0_HEAD_START_S = 5
#: Seconds a rank may live before `timeout` stops it.
LIFETIME_S = 14400


def rank_env(rank: int, nccl_dir: str) -> dict[str, str]:
    """The environment one rank needs. Both ranks name rank 0's address."""
    return {
        "TENSORFOLD_NO_UPDATE_CHECK": "1",
        "HF_HUB_OFFLINE": "1",
        "LD_LIBRARY_PATH": nccl_dir,
        "TF_GLM_WORLD": "2",
        "TF_GLM_RANK": str(rank),
        "NCCL_COMM_ID": f"{HEAD_IP}:{COMM_PORT}",
        "NCCL_SOCKET_IFNAME": IFACES,
        "NCCL_IB_HCA": IB_HCAS,
        "NCCL_IB_GID_INDEX": str(IB_GID_INDEX),
    }


def rank_command(
    *, rank: int, binary: str, nccl_dir: str, model: str, serve_args: list[str]
) -> str:
    """The shell line for one rank. Rank 0 listens on every address."""
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in rank_env(rank, nccl_dir).items())
    host = "0.0.0.0" if rank == 0 else "127.0.0.1"
    argv = [binary, "serve", model, *serve_args, "--host", host]
    return f"{env} timeout {LIFETIME_S} {shlex.join(argv)}"


def pid_file(name: str, rank: int) -> str:
    return f"/tmp/tf-native-{name}-rank{rank}.pid"


def detached(command: str, log: str, pidfile: str) -> str:
    """Run `command` in its own session, record its pid, return at once."""
    return (
        f"setsid nohup sh -c {shlex.quote(command)} > {shlex.quote(log)} 2>&1 "
        f"< /dev/null & echo $! > {shlex.quote(pidfile)}"
    )


def start(args: argparse.Namespace) -> int:
    common = {
        "binary": args.binary,
        "nccl_dir": args.nccl_dir,
        "model": args.model,
        "serve_args": args.serve_args,
    }
    head = detached(
        rank_command(rank=0, **common), args.log_dir + f"/{args.name}-rank0.log",
        pid_file(args.name, 0),
    )  # fmt: skip
    logger.info("rank 0 (head, first): %s", head)
    subprocess.run(["bash", "-c", head], check=True)
    time.sleep(RANK0_HEAD_START_S)
    # A relative path: ssh starts in the worker's home, and a quoted ~ would
    # not expand.
    worker = detached(
        rank_command(rank=1, **common), f"{args.name}-rank1.log",
        pid_file(args.name, 1),
    )  # fmt: skip
    logger.info("rank 1 (worker): %s", worker)
    subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", WORKER_IP, worker], check=True)
    logger.info("started %s on both ranks; rank 0 serves HTTP", args.name)
    return 0


def stop_command(pidfile: str) -> str:
    """Signal every process group in the recorded session, then wait for it.

    The pid file holds the session leader. GNU `timeout` moves itself and the
    server into a group of their own, so signalling the leader's group alone
    missed the server on 2026-10-24 and left it holding the GPU. The pid file
    stays until the session is empty, so a miss stays visible.
    """
    pf = shlex.quote(pidfile)
    return (
        f"[ -f {pf} ] || exit 0; sid=$(cat {pf}); "
        "for g in $(ps -o pgid= --sid $sid | sort -u); do kill -TERM -- -$g; done; "
        'for i in $(seq 1 30); do [ -z "$(ps -o pid= --sid $sid)" ] && break; '
        "sleep 1; done; "
        f'if [ -z "$(ps -o pid= --sid $sid)" ]; then rm -f {pf}; '
        'else echo "session $sid still running" >&2; exit 1; fi'
    )


def stop(args: argparse.Namespace) -> int:
    status = 0
    for host, rank in (("localhost", 0), (WORKER_IP, 1)):
        cmd = stop_command(pid_file(args.name, rank))
        r = subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", host, cmd], check=False)
        if r.returncode:
            logger.error("%s rank %d on %s did not stop", args.name, rank, host)
            status = 1
        else:
            logger.info("stopped %s rank %d on %s", args.name, rank, host)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser("start", help="start rank 0 here, then rank 1")
    p_start.add_argument("--binary", required=True)
    p_start.add_argument("--nccl-dir", required=True)
    p_start.add_argument("--model", required=True)
    p_start.add_argument("--name", required=True)
    p_start.add_argument("--log-dir", default=str(pathlib.Path.home() / "bench-logs"))
    p_start.add_argument("serve_args", nargs=argparse.REMAINDER)
    p_stop = sub.add_parser("stop", help="stop both ranks by recorded pid")
    p_stop.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "start":
        if args.serve_args[:1] == ["--"]:
            args.serve_args = args.serve_args[1:]
        return start(args)
    return stop(args)


if __name__ == "__main__":
    logs.configure()
    sys.exit(main())
