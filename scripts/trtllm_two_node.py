"""Serve one TensorRT-LLM model across both DGX Sparks, tensor parallel 2 (#383).

TensorRT-LLM runs a two-node server as one MPI job: `mpirun` on the head
starts a rank in each node's container, and the worker rank arrives over SSH.
So each container runs `sshd` (NVIDIA's entrypoint script) and nothing else,
and the server starts later, by `docker exec` on the head. This follows
NVIDIA's "Run on two Sparks" recipe (NVIDIA/dgx-spark-playbooks,
nvidia/trt-llm/README.md at 3088315054), with four changes:

- **The entrypoint is pinned.** The recipe pipes the script from the
  playbooks repo's `main` branch at every start. This script reads a committed
  copy (`scripts/assets/trtllm-mn-entrypoint.sh`, from commit 3088315054),
  checks its sha256, and passes its text as the container command. Nothing is
  fetched at start, and the worker needs no file.
- **NCCL uses every fabric path**, with the HCAs and GID index the vLLM
  launcher uses (`scripts/vllm_two_node.py`). `mpirun -x` exports each value
  to both ranks: a rank that SSH starts does not inherit the container's `-e`
  environment.
- **The weights mount read-only**, and the hub stays offline.
- **The server log goes to a file on the head** (`--log-dir`), because the
  server is not the container's main process and `docker logs` shows only
  `sshd`.

    uv run python scripts/trtllm_two_node.py start \
        --model-dir ~/.cache/huggingface/hub/models--nvidia--GLM-5.3-Flash-NVFP4/snapshots/<sha> \
        --image nvcr.io/nvidia/tensorrt-llm/release:1.4.0rc4 \
        --name glm-5.3-flash-nvfp4 --dry-run
    uv run python scripts/trtllm_two_node.py stop --name glm-5.3-flash-nvfp4

Everything after `--` goes to `trtllm-serve` unchanged, after this script's
own flags, so a later flag (for example `--tool_parser`) replaces this
script's value. `--dry-run` logs every command in order and runs none.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import logging
import pathlib
import re
import shlex
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

from vllm_two_node import HEAD_IP, IB_GID_INDEX, IB_HCAS, IFACES, WORKER_IP

import logs

logger = logging.getLogger(__name__)

#: The entrypoint at NVIDIA/dgx-spark-playbooks commit 3088315054 (full sha
#: below), nvidia/trt-llm/assets/trtllm-mn-entrypoint.sh, byte for byte. It
#: installs openssh-server, copies the mounted ~/.ssh to /root/.ssh, moves
#: sshd to port 2233 with root key login, and runs `sshd -D` as the main
#: process. A changed copy is refused, not run.
ENTRYPOINT = pathlib.Path(__file__).resolve().parent / "assets/trtllm-mn-entrypoint.sh"
ENTRYPOINT_COMMIT = "30883150541743d580c9c347700c6ac99fc396d2"
ENTRYPOINT_SHA256 = "4658182a62a1b1932b6507e23f8b956450f9bfd4d1aa80b67c50ef15f1b1bd28"
#: The entrypoint's own default (`SSH_PORT`). mpirun reaches the worker here.
SSH_PORT = 2233
#: The entrypoint runs `apt-get install openssh-server` first, so sshd can
#: take minutes to listen.
SSHD_WAIT_S = 900

HF_CACHE = "~/.cache/huggingface"
CONTAINER_HF_CACHE = "/root/.cache/huggingface"
HOSTFILE = "/etc/openmpi-hostfile"
LLM_API_YAML = "/tmp/extra-llm-api-config.yml"
CONTAINER_LOG_DIR = "/logs"
#: The fabric interface that carries 10.0.0.1 and 10.0.0.2: MPI's own TCP
#: traffic (control only; NCCL carries the tensors) stays on that one link.
MPI_IFACE = "enp1s0f1np1"

#: Container environment. Every key here also goes to `mpirun -x`, so both
#: ranks see the same value.
RANK_ENV = {
    "NCCL_SOCKET_IFNAME": IFACES,
    "NCCL_IB_DISABLE": "0",
    "NCCL_IB_HCA": IB_HCAS,
    "NCCL_IB_GID_INDEX": str(IB_GID_INDEX),
    "NCCL_IB_AUTO_DETECT": "0",
    "NCCL_DEBUG": "WARN",
    "UCX_NET_DEVICES": IFACES,
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    # The hub cache mounts read-only; remote-code modules go to scratch.
    "HF_MODULES_CACHE": "/tmp/hf-modules",
    "CPATH": "/usr/local/cuda/include",
    "TRITON_PTXAS_PATH": "/usr/local/cuda/bin/ptxas",
}
#: mpirun's own settings, read on the head only (from the recipe).
MPI_ENV = {
    "OMPI_MCA_btl_tcp_if_include": MPI_IFACE,
    "OMPI_MCA_oob_tcp_if_include": MPI_IFACE,
    "OMPI_MCA_orte_default_hostfile": HOSTFILE,
    "OMPI_MCA_rmaps_ppr_n_pernode": "1",
    "OMPI_ALLOW_RUN_AS_ROOT": "1",
    "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM": "1",
}

# The `trtllm-serve` flags below are the names in
# tensorrt_llm/commands/serve.py at tag v1.4.0rc4 (commit ab2dfeba8e69):
# --served_model_name, --host, --port, --tp_size (alias of
# --tensor_parallel_size), --max_num_tokens, --max_batch_size, --max_seq_len,
# --free_gpu_memory_fraction, --extra_llm_api_options (alias of --config),
# --tool_parser, --reasoning_parser. `--host` defaults to localhost there.
# `glm47` is a key in both factories at that tag:
# tensorrt_llm/serve/tool_parser/tool_parser_factory.py (`"glm47":
# Glm47ToolParser`) and tensorrt_llm/llmapi/reasoning_parser.py
# (`@register_reasoning_parser("glm47", reasoning_at_start=True)`). The
# checkpoint's model_type `glm5_next` is in neither auto-map, so `auto` fails.
DEFAULT_PARSER = "glm47"


@dataclasses.dataclass(frozen=True)
class Step:
    """One action: a command on a host, or a wait for a port."""

    what: str
    argv: list[str] | None = None
    wait_for: tuple[str, int] | None = None
    #: What the log shows instead of `argv`, when argv carries the entrypoint.
    shown: str | None = None

    def line(self) -> str:
        if self.shown is not None:
            return self.shown
        if self.argv is not None:
            return shell_line(self.argv)
        assert self.wait_for is not None
        host, port = self.wait_for
        return f"wait up to {SSHD_WAIT_S} s for {host}:{port} to accept"


_HOME_PATH = re.compile(r"~/[A-Za-z0-9_./:+@-]*")


def shell_line(argv: list[str]) -> str:
    """Quote an argv for a shell, but leave `~/...` paths for it to expand.

    Every mount names a path in the operator's home, and the worker's home is
    known only to the worker's shell.
    """
    return " ".join(
        arg if _HOME_PATH.fullmatch(arg) else shlex.quote(arg) for arg in argv
    )


def container(name: str, rank: int) -> str:
    return f"trtllm-{name}-rank{rank}"


def entrypoint_text(path: pathlib.Path = ENTRYPOINT) -> str:
    """The pinned entrypoint, or SystemExit if the copy is not that file."""
    data = path.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != ENTRYPOINT_SHA256:
        raise SystemExit(
            f"refusing to start: {path} has sha256 {got}, not {ENTRYPOINT_SHA256} "
            f"(NVIDIA/dgx-spark-playbooks@{ENTRYPOINT_COMMIT}). Restore the file "
            "from that commit; do not update the hash to match an edit."
        )
    return data.decode()


def container_model_path(model_dir: str) -> str:
    """Where the head and worker containers see `model_dir`.

    The whole hub cache is mounted, not the snapshot: a snapshot's files are
    symlinks into `../../blobs`, which a snapshot-only mount would break.
    """
    root = pathlib.Path(HF_CACHE).expanduser()
    path = pathlib.Path(model_dir).expanduser()
    try:
        rel = path.relative_to(root)
    except ValueError:
        raise SystemExit(
            f"--model-dir must sit under {HF_CACHE} (mounted at "
            f"{CONTAINER_HF_CACHE} on both nodes); got {model_dir}"
        ) from None
    return f"{CONTAINER_HF_CACHE}/{rel.as_posix()}"


def docker_run(
    *, rank: int, name: str, image: str, script: str, log_dir: str = ""
) -> list[str]:
    """The `docker run` argv for one node. Both run only sshd.

    Only the head mounts `log_dir`: the server and its log live there.
    """
    argv = [
        "docker", "run", "-d", "--rm", "--name", container(name, rank),
        "--gpus", "all", "--network", "host", "--ipc", "host",
        "--ulimit", "memlock=-1", "--ulimit", "stack=67108864",
        "--device", "/dev/infiniband:/dev/infiniband",
        "-v", f"{HF_CACHE}:{CONTAINER_HF_CACHE}:ro",
        "-v", "~/.ssh:/tmp/.ssh:ro",
    ]  # fmt: skip
    if rank == 0:
        argv += ["-v", f"{log_dir}:{CONTAINER_LOG_DIR}"]
    env = {**RANK_ENV, **(MPI_ENV if rank == 0 else {})}
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    argv += [image, "bash", "-c", script]
    return argv


def llm_api_yaml(kv_free_fraction: float) -> str:
    """The recipe's extra-llm-api-options file."""
    return (
        "print_iter_log: false\n"
        "kv_cache_config:\n"
        '  dtype: "auto"\n'
        f"  free_gpu_memory_fraction: {kv_free_fraction}\n"
        "cuda_graph_config:\n"
        "  enable_padding: true\n"
    )


def serve_argv(args: argparse.Namespace) -> list[str]:
    """`mpirun ... trtllm-serve ...`, run inside the head container."""
    argv = ["mpirun"]
    for key in RANK_ENV:
        argv += ["-x", key]
    argv += [
        "trtllm-llmapi-launch", "trtllm-serve", container_model_path(args.model_dir),
        "--served_model_name", args.name,
        "--host", "0.0.0.0", "--port", str(args.port),
        "--tp_size", "2",
        "--max_num_tokens", str(args.max_num_tokens),
        "--max_batch_size", str(args.max_batch_size),
        "--max_seq_len", str(args.max_seq_len),
        # Also on the command line: an explicit flag wins over the YAML file,
        # so the fraction holds whatever the merge rule for defaults is.
        "--free_gpu_memory_fraction", str(args.kv_free_fraction),
        "--extra_llm_api_options", LLM_API_YAML,
        "--tool_parser", args.tool_parser,
        "--reasoning_parser", args.reasoning_parser,
    ]  # fmt: skip
    return argv + list(args.serve_args)


def log_file(name: str) -> str:
    return f"{container(name, 0)}-serve.log"


#: Stands for the entrypoint's text in logged commands: 50 lines of sshd setup,
#: twice, buried every other step of a dry run.
SCRIPT_SHOWN = f"<{ENTRYPOINT.name}, sha256 {ENTRYPOINT_SHA256[:12]}>"


def start_plan(args: argparse.Namespace, script: str) -> list[Step]:
    """Every action `start` takes, in order: worker, then head, then serve.

    The logged line of each step names the entrypoint instead of quoting it.
    """
    real, shown = _start_steps(args, script), _start_steps(args, SCRIPT_SHOWN)
    return [
        step
        if step.line() == view.line()
        else dataclasses.replace(step, shown=view.line())
        for step, view in zip(real, shown, strict=True)
    ]


def _start_steps(args: argparse.Namespace, script: str) -> list[Step]:
    head, worker = container(args.name, 0), container(args.name, 1)
    log_dir = args.log_dir
    run_worker = docker_run(rank=1, name=args.name, image=args.image, script=script)
    run_head = docker_run(
        rank=0, name=args.name, image=args.image, script=script, log_dir=log_dir
    )
    hostfile = f"printf '%s\\n' {HEAD_IP} {WORKER_IP} > {HOSTFILE}"
    yaml_cmd = (
        f"printf '%s' {shlex.quote(llm_api_yaml(args.kv_free_fraction))}"
        f" > {LLM_API_YAML}"
    )
    serve = (
        f"{shlex.join(serve_argv(args))}"
        f" > {CONTAINER_LOG_DIR}/{log_file(args.name)} 2>&1"
    )
    ssh = ["ssh", "-o", "BatchMode=yes", WORKER_IP]
    return [
        Step(f"start {worker} on the worker", ssh + [shell_line(run_worker)]),
        Step("make the log directory on the head", ["mkdir", "-p", log_dir]),
        Step(f"start {head} on the head", ["bash", "-c", shell_line(run_head)]),
        Step("wait for sshd in the worker container", wait_for=(WORKER_IP, SSH_PORT)),
        Step("wait for sshd in the head container", wait_for=(HEAD_IP, SSH_PORT)),
        Step("write the MPI hostfile", ["docker", "exec", head, "bash", "-c", hostfile]),
        Step("write the LLM API options", ["docker", "exec", head, "bash", "-c", yaml_cmd]),
        Step("launch trtllm-serve, detached", ["docker", "exec", "-d", head, "bash", "-c", serve]),
    ]  # fmt: skip


def stop_plan(name: str) -> list[Step]:
    """Remove the head container, then the worker container."""
    head, worker = container(name, 0), container(name, 1)
    return [
        Step(f"remove {head} on the head", ["docker", "rm", "-f", head]),
        Step(
            f"remove {worker} on the worker",
            ["ssh", "-o", "BatchMode=yes", WORKER_IP, f"docker rm -f {worker}"],
        ),
    ]


def wait_for_port(host: str, port: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with socket.create_connection((host, port), timeout=5):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise SystemExit(
                    f"{host}:{port} did not accept within {timeout_s:.0f} s; "
                    "read `docker logs` of that node's container"
                ) from None
            time.sleep(5)


def execute(steps: list[Step], *, dry_run: bool, check: bool = True) -> None:
    for n, step in enumerate(steps, 1):
        logger.info("%d. %s: %s", n, step.what, step.line())
        if dry_run:
            continue
        if step.argv is not None:
            subprocess.run(step.argv, check=check)
        elif step.wait_for is not None:
            wait_for_port(*step.wait_for, SSHD_WAIT_S)


def start(args: argparse.Namespace) -> int:
    script = entrypoint_text()
    if not args.dry_run:
        model = pathlib.Path(args.model_dir).expanduser()
        if not (model / "config.json").is_file():
            raise SystemExit(f"no config.json in {model}")
    execute(start_plan(args, script), dry_run=args.dry_run)
    log = pathlib.Path(args.log_dir) / log_file(args.name)
    logger.info(
        "%s: serving %s on :%d once loaded; tail %s",
        "dry run, nothing ran" if args.dry_run else "started",
        args.name,
        args.port,
        log,
    )
    return 0


def stop(args: argparse.Namespace) -> int:
    # check=False: a container that is already gone is not an error here.
    execute(stop_plan(args.name), dry_run=args.dry_run, check=False)
    return 0


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser(
        "start", help="start the worker, then the head, then serve"
    )
    p_start.add_argument(
        "--model-dir", required=True, help=f"a snapshot under {HF_CACHE}"
    )
    p_start.add_argument("--image", required=True)
    p_start.add_argument("--name", required=True, help="the served model name")
    p_start.add_argument("--port", type=int, default=8888)
    p_start.add_argument("--max-num-tokens", type=int, default=32768)
    p_start.add_argument("--max-batch-size", type=int, default=4)
    p_start.add_argument("--max-seq-len", type=int, default=262144)
    p_start.add_argument("--kv-free-fraction", type=float, default=0.8)
    p_start.add_argument("--tool-parser", default=DEFAULT_PARSER)
    p_start.add_argument("--reasoning-parser", default=DEFAULT_PARSER)
    p_start.add_argument("--log-dir", default=str(pathlib.Path.home() / "bench-logs"))
    p_start.add_argument("--dry-run", action="store_true")
    p_start.add_argument("serve_args", nargs=argparse.REMAINDER)
    p_stop = sub.add_parser("stop", help="remove both containers")
    p_stop.add_argument("--name", required=True)
    p_stop.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "start" and args.serve_args[:1] == ["--"]:
        args.serve_args = args.serve_args[1:]
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    return start(args) if args.cmd == "start" else stop(args)


if __name__ == "__main__":
    logs.configure()
    sys.exit(main())
