"""Serve Inkling 2 Small EXL3 on both DGX Sparks with the inkling2-exl3 engine.

The engine is oracle9i88/inkling2-exl3 at a pinned commit (#1435): an
Inkling 2 overlay for exllamav3 v1.6.6, run with torchrun inside the NGC
PyTorch image, one rank per Spark. The fork's own `spark/launch_2spark.sh`
starts detached containers from a `cluster.env` that names LAN addresses.
This launcher builds the same container commands, with four local changes:

- **Each rank is a `systemd-run --user --property=KillMode=process` unit**
  whose main process is the foreground `docker run`, with its output appended
  to `~/bench-logs/<name>-rank<N>.log` (#691). Stopping the unit stops the
  docker client, which passes the signal to the container.
- **NCCL names all four fabric interfaces and all four RoCE devices.** The
  fork sends the bootstrap over one LAN interface; here it goes over the
  fabric, with rank 0 at 10.0.0.1, so no LAN name is in the repo.
- **Reasoning effort is low on the server** (operator, 2026-09-22). The
  server has no flag for it: a request without `reasoning_effort` gets the
  chat template's default, 0.9. So the launcher writes a copy of the pinned
  template whose default is "low" (0.2) and passes it with `--chat-template`.
  A request that sends its own `reasoning_effort` still wins.
- **Each node is checked first** (`refusals`). The launch refuses unless both
  nodes hold a clean engine checkout at `ENGINE_SHA`, a built extension, every
  file in the pack's SHA256SUMS with no unfinished download, at least
  `MIN_MEM_AVAILABLE_GIB` free, nothing on the API or store port, and no
  container or process holding the GPU (the dcgm monitors excepted).

    uv run python scripts/inkling2_two_node.py start --name inkling2
    uv run python scripts/inkling2_two_node.py start --nccl-debug INFO   # first launch: confirm NET/IB
    uv run python scripts/inkling2_two_node.py status --name inkling2
    uv run python scripts/inkling2_two_node.py stop --name inkling2

Arguments after `--` go to `server/i2_server.py` after the defaults in
`SERVE_ARGS`; the server's argparse keeps the last value of a repeated flag.
The worker rank starts first. Rank 0 logs `READY_LINE` when it is about to
serve; wait on a real completion, not on that line or `/health`.

Layout on each node (docs/external-repos.md): the engine checkout at
`~/git/inkling2-exl3`, the patched exllamav3 with its extension built and the
pinned wheels under `~/engines/inkling2-exl3-<sha8>`, and the weights at
`~/models/<model>`, a full copy on each node (each rank reads its own shard).
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

#: The approved engine revision (#1435). Another revision needs a new approval.
ENGINE_SHA = "299eeaf3ae4474a79c29a9b5dda000a5f0dccad0"
ENGINE_TREE = "git/inkling2-exl3"
BUILD_DIR = f"engines/inkling2-exl3-{ENGINE_SHA[:8]}"
#: The extension `python setup.py build_ext --inplace` leaves in exllamav3.
EXTENSION_GLOB = "build/exllamav3/exllamav3_ext*.so"
MODEL = "Evanguo12--Inkling-2-Small-EXL3-2.55bpw-2Spark-0265bcd759"
#: The image the extension was built in (the fork's README, "Build").
IMAGE = "nvcr.io/nvidia/pytorch:26.07-py3"
PORT = 18100
MASTER_PORT = 29620
HEAD_IP = "10.0.0.1"
WORKER = "spark-b-cx7"
#: Every fabric interface and RoCE device. Naming one path of a QSFP cage
#: walls the link at about 112 Gb/s (see scripts/cluster_allreduce.py).
IFACES = "enp1s0f1np1,enP2p1s0f1np1,enp1s0f0np0,enP2p1s0f0np0"
IB_HCAS = "rocep1s0f1,roceP2p1s0f1,rocep1s0f0,roceP2p1s0f0"
IB_GID_INDEX = 3
#: GiB the server keeps free when it sizes the KV cache. The fork's two-node
#: default (spark/launch_2spark.sh): about 4 GiB stays free after the decode
#: graphs are captured.
RESERVE_GB = 6
#: The wheels the fork's launcher installs offline at container start.
WHEELS = (
    "transformers==5.23.0",
    "fastapi==0.163.0",
    "uvicorn==0.54.0",
    "marisa-trie==1.4.1",
)
TEMPLATE = "server/chat_template-2ad1157.jinja"
LOW_TEMPLATE = "chat_template-2ad1157-effort-low.jinja"
#: The template's default effort when a request sends none, and the default
#: this launcher serves instead.
TEMPLATE_DEFAULT = "reasoning_effort is not none else 0.9 -%}"
TEMPLATE_LOW = 'reasoning_effort is not none else "low" -%}'
#: The fork's two-Spark serving command (README, "Run"), except the template.
SERVE_ARGS = (
    "--slots", "4",
    "--max-model-len", "524288",
    "--default-max-tokens", "32768",
    "--default-thinking-budget", "60000",
    "--served-name", "inkling-2-small",
    "--spec-mode", "auto",
    "--chat-template", f"/work/{LOW_TEMPLATE}",
    "--prefix-snapshots", "24",
    "--prefix-every", "16384",
    "--chunk", "2048",
)  # fmt: skip
#: Lines in the pack's SHA256SUMS at 0265bcd7: every weight and config file.
SUMS_ENTRIES = 132
#: The least MemAvailable a node may have before a launch. The server sizes
#: its KV cache from what is free, so a node with less serves a smaller cache
#: than the backend describes, or runs out while loading ~100 GB of weights.
MIN_MEM_AVAILABLE_GIB = 105
#: Containers that request the GPU only to read its counters; they hold no
#: memory to speak of and stay up during every run.
GPU_MONITORS = frozenset({"dcgm", "dcgm-exporter"})
#: Seconds one command on a node may take; ssh gives up connecting sooner.
NODE_TIMEOUT_S = 120
SSH_CONNECT_TIMEOUT_S = 10
#: Rank 0 prints this just before uvicorn starts (server/i2_server.py).
READY_LINE = f"serving on http://0.0.0.0:{PORT}"


def low_effort_template(text: str) -> str:
    """The pinned chat template with "low" as its default reasoning effort.

    Refuses a template that does not hold the default exactly once, so a
    changed template cannot silently serve the old default.
    """
    if text.count(TEMPLATE_DEFAULT) != 1:
        raise SystemExit(
            f"the chat template does not hold {TEMPLATE_DEFAULT!r} exactly once; "
            "the engine is not at the pinned revision"
        )
    return text.replace(TEMPLATE_DEFAULT, TEMPLATE_LOW)


def container_env(nccl_debug: str = "WARN") -> dict[str, str]:
    """The fork's container environment, with the fabric named in full.

    `nccl_debug="INFO"` also logs NCCL's init and network choice, so the
    first launch can confirm `NET/IB : Using` (RoCE, not TCP) in both logs.
    """
    env = {
        "NCCL_SOCKET_IFNAME": IFACES,
        "GLOO_SOCKET_IFNAME": IFACES,
        "NCCL_IB_HCA": IB_HCAS,
        "NCCL_IB_GID_INDEX": str(IB_GID_INDEX),
        "NCCL_IB_SUBNET_PREFIX_LEN": "30",
        "NCCL_IB_SUBNET_AWARE_ROUTING": "1",
        "NCCL_CROSS_NIC": "1",
        "NCCL_IB_MERGE_NICS": "0",
        "NCCL_NET_PLUGIN": "none",
        "NCCL_CUMEM_ENABLE": "0",
        "NCCL_MIN_NCHANNELS": "4",
        "NCCL_MAX_NCHANNELS": "4",
        "NCCL_P2P_DISABLE": "1",
        "NCCL_SHM_DISABLE": "1",
        "TORCH_NCCL_USE_COMM_NONBLOCKING": "0",
        "NCCL_DEBUG": nccl_debug,
        "I2_P2P_AR": "1",
        "MALLOC_MMAP_THRESHOLD_": "262144",
        "MALLOC_TRIM_THRESHOLD_": "33554432",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    if nccl_debug == "INFO":
        env["NCCL_DEBUG_SUBSYS"] = "INIT,NET"
    return env


def rank_shell(rank: int, serve_args: list[str]) -> str:
    """The command the container runs for one rank."""
    torchrun = [
        "torchrun", "--nnodes", "2", "--nproc_per_node", "1",
        "--node_rank", str(rank),
        "--master_addr", HEAD_IP, "--master_port", str(MASTER_PORT),
        "server/i2_server.py", "--model", "/model", "--port", str(PORT),
        "--reserve-gb", str(RESERVE_GB), *SERVE_ARGS, *serve_args,
    ]  # fmt: skip
    pip = ["pip", "install", "-q", "--no-index", "--find-links", "/work/wheels"]
    return (
        f"{shlex.join([*pip, *WHEELS])}; cd /work/engine && "
        f"PYTHONPATH=/work/build/exllamav3 exec {shlex.join(torchrun)}"
    )


def docker_run(
    *,
    rank: int,
    name: str,
    home: str,
    serve_args: list[str],
    nccl_debug: str = "WARN",
) -> list[str]:
    """The foreground `docker run` argv for one rank, with absolute paths."""
    build = f"{home}/{BUILD_DIR}"
    argv = [
        "docker", "run", "--name", f"{name}-r{rank}",
        "--gpus", "all", "--network", "host", "--ipc=host", "--shm-size", "16g",
        "--device", "/dev/infiniband", "--cap-add", "IPC_LOCK",
        "--ulimit", "memlock=-1", "--ulimit", "stack=67108864",
        "-v", f"{build}:/work",
        "-v", f"{home}/{ENGINE_TREE}:/work/engine:ro",
        "-v", f"{home}/models/{MODEL}:/model:ro",
        "-v", f"{home}/models:/models:ro",
    ]  # fmt: skip
    for key, value in container_env(nccl_debug).items():
        argv += ["-e", f"{key}={value}"]
    argv += ["--entrypoint", "bash", IMAGE, "-c", rank_shell(rank, serve_args)]
    return argv


def unit_name(name: str, rank: int) -> str:
    return f"{name}-rank{rank}"


def log_path(home: str, name: str, rank: int) -> str:
    return f"{home}/bench-logs/{unit_name(name, rank)}.log"


def systemd_unit(
    *,
    rank: int,
    name: str,
    home: str,
    serve_args: list[str],
    nccl_debug: str = "WARN",
) -> list[str]:
    """`systemd-run` wrapping one rank's `docker run` as the unit's main process."""
    log = log_path(home, name, rank)
    return [
        "systemd-run", "--user", f"--unit={unit_name(name, rank)}",
        "--property=KillMode=process",
        f"--property=StandardOutput=append:{log}",
        f"--property=StandardError=append:{log}",
        *docker_run(
            rank=rank,
            name=name,
            home=home,
            serve_args=serve_args,
            nccl_debug=nccl_debug,
        ),
    ]  # fmt: skip


_FACTS_SHELL = r"""
M="$HOME/models/@MODEL@"; B="$HOME/@BUILD@"; E="$HOME/@TREE@"
systemctl --user reset-failed @UNIT@ >/dev/null 2>&1 || true
echo "home=$HOME"
echo "engine_head=$(git -C "$E" rev-parse HEAD 2>/dev/null)"
if git -C "$E" diff --quiet HEAD 2>/dev/null; then echo engine_dirty=0; else echo engine_dirty=1; fi
echo "extension=$(sha256sum "$B"/@EXT@ 2>/dev/null | awk '{print $1}' | head -1)"
if [ -f "$M/SHA256SUMS" ]; then
  echo "sums=$(wc -l < "$M/SHA256SUMS")"
  awk '{sub(/^\*/, "", $2); print $2}' "$M/SHA256SUMS" |
    while IFS= read -r f; do [ -e "$M/$f" ] || echo "missing=$f"; done
else
  echo sums=none
fi
echo "incomplete=$(find "$M/.cache/huggingface/download" -name '*.incomplete' 2>/dev/null | wc -l)"
echo "mem_kib=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
ss -ltnH | awk '{print $4}' | grep -E ':(@PORT@|@MPORT@)$' | sed 's/^/listen=/'
echo "unit_active=$(systemctl --user is-active @UNIT@ 2>/dev/null)"
docker ps -q | xargs -r docker inspect --format '{{.Name}} {{if .HostConfig.DeviceRequests}}gpu{{end}}' |
  awk '$2 == "gpu" || $1 ~ /^\/i2spark-r/ {sub(/^\//, "", $1); print "gpu_container=" $1}'
nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>/dev/null |
  grep -E '^[0-9]' | sed 's/^/gpu_app=/'
"""


def facts_shell(name: str, rank: int) -> str:
    """A read-only probe of one node, one `key=value` per line.

    Its only write is `reset-failed` on this rank's unit: a failed transient
    unit keeps its name and blocks the next `systemd-run`.
    """
    subs = {
        "@MODEL@": MODEL,
        "@BUILD@": BUILD_DIR,
        "@TREE@": ENGINE_TREE,
        "@EXT@": EXTENSION_GLOB,
        "@UNIT@": unit_name(name, rank),
        "@PORT@": str(PORT),
        "@MPORT@": str(MASTER_PORT),
    }
    text = _FACTS_SHELL
    for key, value in subs.items():
        text = text.replace(key, value)
    return text


def parse_facts(text: str) -> dict[str, list[str]]:
    """`key=value` lines to {key: [values]}; other lines are ignored."""
    facts: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.isidentifier():
            facts.setdefault(key, []).append(value.strip())
    return facts


def _one(facts: dict[str, list[str]], key: str) -> str:
    return (facts.get(key) or [""])[0]


def refusals(facts: dict[str, list[str]], name: str, rank: int) -> list[str]:
    """Every reason this node must not start its rank; empty means go."""
    why = []
    head = _one(facts, "engine_head")
    if head != ENGINE_SHA:
        why.append(f"engine checkout at {head or 'nothing'}, not {ENGINE_SHA}")
    if _one(facts, "engine_dirty") != "0":
        why.append(f"engine checkout ~/{ENGINE_TREE} has uncommitted changes")
    if not _one(facts, "extension"):
        why.append(f"no built exllamav3 extension in ~/{BUILD_DIR}")
    sums = _one(facts, "sums")
    if not sums.isdigit():
        why.append(f"no SHA256SUMS in ~/models/{MODEL}: the weights are not there")
    elif int(sums) != SUMS_ENTRIES:
        why.append(f"SHA256SUMS lists {sums} files, not {SUMS_ENTRIES}")
    missing = facts.get("missing", [])
    if missing:
        why.append(f"{len(missing)} files in SHA256SUMS are absent, e.g. {missing[:3]}")
    incomplete = _one(facts, "incomplete")
    if incomplete not in ("", "0"):
        why.append(f"{incomplete} *.incomplete files: a download is unfinished")
    mem = _one(facts, "mem_kib")
    gib = int(mem) / 2**20 if mem.isdigit() else 0.0
    if gib < MIN_MEM_AVAILABLE_GIB:
        why.append(
            f"MemAvailable {gib:.1f} GiB is under {MIN_MEM_AVAILABLE_GIB} GiB: "
            "something else holds memory"
        )
    for addr in facts.get("listen", []):
        why.append(f"{addr} is already listening")
    if _one(facts, "unit_active") == "active":
        why.append(f"unit {unit_name(name, rank)} is already active")
    holders = [c for c in facts.get("gpu_container", []) if c not in GPU_MONITORS]
    if holders:
        why.append(f"running containers hold the GPU: {holders}")
    for app in facts.get("gpu_app", []):
        why.append(f"a GPU process is running: {app}")
    return why


def prepare_shell(name: str) -> str:
    """After the checks pass: the log directory, and no stale container."""
    return (
        'mkdir -p "$HOME/bench-logs"; '
        f"docker rm -f {shlex.quote(name)}-r$RANK >/dev/null 2>&1 || true"
    )


def on_node(rank: int, command: str) -> subprocess.CompletedProcess[str]:
    """Run a shell line on the node of `rank`: the head is local.

    A node that does not answer fails in seconds, not at the TCP timeout.
    """
    argv = (
        ["bash", "-c", command]
        if rank == 0
        else [
            "ssh", "-n", "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}", WORKER, command,
        ]
    )  # fmt: skip
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=NODE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, 124, "", f"no answer from rank {rank}'s node in {NODE_TIMEOUT_S} s"
        )


def write_low_template(home: str) -> None:
    """Write the low-effort template into the head's build dir (rank 0 reads it)."""
    src = pathlib.Path(home) / ENGINE_TREE / TEMPLATE
    dst = pathlib.Path(home) / BUILD_DIR / LOW_TEMPLATE
    dst.write_text(low_effort_template(src.read_text()))
    if TEMPLATE_LOW not in dst.read_text():
        raise SystemExit(f"{dst} does not hold the low-effort default")
    logger.info("wrote %s (default reasoning effort: low)", dst)


def start(args: argparse.Namespace) -> int:
    facts: dict[int, dict[str, list[str]]] = {}
    refused = False
    for rank in (1, 0):
        r = on_node(rank, facts_shell(args.name, rank))
        facts[rank] = parse_facts(r.stdout)
        why = refusals(facts[rank], args.name, rank)
        if r.returncode and not facts[rank]:
            why = [f"the probe failed: {r.stderr.strip()}"]
        for reason in why:
            logger.error("rank %d refuses: %s", rank, reason)
        refused = refused or bool(why)
    if refused:
        return 1
    homes = {rank: _one(facts[rank], "home") for rank in (0, 1)}
    for rank in (1, 0):
        r = on_node(rank, f"RANK={rank}; {prepare_shell(args.name)}")
        if r.returncode:
            logger.error("rank %d prepare failed: %s", rank, r.stderr.strip())
            return 1
    write_low_template(homes[0])
    for rank in (1, 0):  # the worker first; rank 0 holds the store it waits for
        argv = systemd_unit(
            rank=rank,
            name=args.name,
            home=homes[rank],
            serve_args=args.serve_args,
            nccl_debug=args.nccl_debug,
        )
        line = shlex.join(argv)
        # Separate builds of the extension differ byte for byte; record each.
        logger.info(
            "rank %d (extension sha256 %s): %s",
            rank,
            _one(facts[rank], "extension"),
            line,
        )
        r = on_node(rank, line)
        if r.returncode:
            logger.error("rank %d did not start: %s", rank, r.stderr.strip())
            if rank == 0:  # do not leave the worker waiting for a store
                on_node(1, stop_shell(args.name, 1))
                logger.error("stopped rank 1 because rank 0 did not start")
            return 1
        logger.info("rank %d log: %s", rank, log_path(homes[rank], args.name, rank))
    logger.info(
        "started %s; rank 0 serves :%d; ready line %r", args.name, PORT, READY_LINE
    )
    return 0


def stop_shell(name: str, rank: int) -> str:
    """Stop the unit (the docker client passes SIGTERM on), then the container."""
    unit = unit_name(name, rank)
    return (
        f"systemctl --user stop {unit} 2>/dev/null; "
        f"docker rm -f {name}-r{rank} >/dev/null 2>&1; "
        f"systemctl --user reset-failed {unit} 2>/dev/null; "
        f"if docker ps -q --filter name=^{name}-r{rank}$ | grep -q .; "
        f'then echo "{name}-r{rank} still running" >&2; exit 1; fi'
    )


def stop(args: argparse.Namespace) -> int:
    status = 0
    for rank in (0, 1):
        r = on_node(rank, stop_shell(args.name, rank))
        if r.returncode:
            logger.error("rank %d did not stop: %s", rank, r.stderr.strip())
            status = 1
        else:
            logger.info("stopped %s rank %d", args.name, rank)
    return status


def status(args: argparse.Namespace) -> int:
    for rank in (0, 1):
        unit = unit_name(args.name, rank)
        r = on_node(
            rank,
            f"echo unit=$(systemctl --user is-active {unit}) "
            f'container="$(docker ps --filter name=^{args.name}-r{rank}$ '
            "--format '{{.Status}}')\" "
            "$(awk '/MemAvailable/ {printf \"MemAvailable=%.1fGiB\", $2/1048576}' "
            "/proc/meminfo)",
        )
        logger.info("rank %d: %s", rank, r.stdout.strip() or r.stderr.strip())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser(
        "start", help="start rank 1 on the worker, then rank 0 here"
    )
    p_start.add_argument("--name", default="inkling2")
    p_start.add_argument(
        "--nccl-debug",
        choices=("WARN", "INFO"),
        default="WARN",
        help="INFO also sets NCCL_DEBUG_SUBSYS=INIT,NET, to confirm RoCE in the logs",
    )
    p_start.add_argument("serve_args", nargs=argparse.REMAINDER)
    for cmd, text in (("stop", "stop both ranks"), ("status", "show both ranks")):
        p = sub.add_parser(cmd, help=text)
        p.add_argument("--name", default="inkling2")
    args = parser.parse_args(argv)
    if args.cmd == "start":
        if args.serve_args[:1] == ["--"]:
            args.serve_args = args.serve_args[1:]
        return start(args)
    return stop(args) if args.cmd == "stop" else status(args)


if __name__ == "__main__":
    logs.configure()
    sys.exit(main())
