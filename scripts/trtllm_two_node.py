"""Serve one TensorRT-LLM model across both DGX Sparks, tensor parallel 2 (#383).

TensorRT-LLM runs a two-node server as one MPI job: `mpirun` on the head
starts a rank in each node's container, and the worker rank arrives over SSH.
So each container runs `sshd` (NVIDIA's entrypoint script) and nothing else,
and the server starts later, by `docker exec` on the head. This follows
NVIDIA's "Run on two Sparks" recipe (NVIDIA/dgx-spark-playbooks,
nvidia/trt-llm/README.md at 3088315054). It departs from the recipe here:

- **The entrypoint is pinned.** The recipe pipes the script from the
  playbooks repo's `main` branch at every start. This script reads a committed
  copy (`scripts/assets/trtllm-mn-entrypoint.sh`, from commit 3088315054),
  checks its sha256, and passes its text as the container command, so the
  worker needs no file. The script itself still runs `apt-get install
  openssh-server` in each container at start, unpinned, so each node needs
  internet access then.
- **`bash -c`, not `sh`**: the script's shebang is bash and it uses `compgen`.
- **NCCL uses every fabric path**, with the HCAs and GID index the vLLM
  launcher uses (`scripts/vllm_two_node.py`). `mpirun -x` exports each value
  to both ranks: a rank that SSH starts does not inherit the container's `-e`
  environment.
- **MPI's own TCP traffic, and its out-of-band channel, use one interface**
  (`MPI_IFACE`, the one that carries 10.0.0.x). Each fabric port has its own
  subnet, so a list of all four could pair unreachable addresses.
- **Open MPI 5 placement.** The image ships Open MPI v5.0.10rc2 (PRRTE), where
  the recipe's `OMPI_MCA_rmaps_ppr_n_pernode` does not exist and a hostfile
  line without `slots=` gives one slot per core. So the hostfile says
  `slots=1`, and mpirun gets `-np 2 --map-by ppr:1:node --bind-to none` and
  `--hostfile` explicitly.
- **`--ipc host`**, and the weights mount read-only with the hub offline.
- **No `--rm`.** A container that dies stays, so its `docker logs` can be
  read; `stop` removes it.
- **Serve flags:** `--host 0.0.0.0` (the default is localhost), the glm47
  tool and reasoning parsers named explicitly (`auto` fails for
  `glm5_next`), `--backend pytorch` dropped (deprecated; it is the default),
  and chunked prefill on: without it rc4 refuses any prompt longer than
  `--max_num_tokens` with HTTP 400.
- **Reasoning effort low, set on the server.** rc4 has no serving extension
  for `glm5_next`, so it drops a request's `reasoning_effort`, and the
  snapshot's template then defaults to "max". The launcher serves a copy of
  the snapshot's template whose default is "low" (`--chat_template`).
- **Each node is checked first** (`refusals`): nothing on the sshd or API
  port, no `trtllm-*` container, no GPU compute process, enough free memory,
  and the same checkpoint on both nodes.
- **The server log goes to a file on the head** (`--log-dir`), because the
  server is not the container's main process and `docker logs` shows only
  `sshd`. The launch argv goes beside it as JSON, for the row's provenance.
- **`start` waits for `/v1/models`** and logs "started" only after it
  answers. On any failure after the worker container starts, it tails the
  serve log, removes both containers, and exits non-zero.

    uv run python scripts/trtllm_two_node.py start \
        --model-dir ~/.cache/huggingface/hub/models--nvidia--GLM-5.3-Flash-NVFP4/snapshots/<sha> \
        --image nvcr.io/nvidia/tensorrt-llm/release:1.4.0rc4 \
        --name glm-5.3-flash-nvfp4 --dry-run
    uv run python scripts/trtllm_two_node.py stop --name glm-5.3-flash-nvfp4

Everything after `--` goes to `trtllm-serve` unchanged, after this script's
own flags, so a later flag (for example `--tool_parser`) replaces this
script's value. `--dry-run` logs every check and command in order and runs
none.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import pathlib
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

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
#: Seconds `start` waits for /v1/models: 190 GiB of weights to load, plus
#: kernel builds on first use.
READY_WAIT_S = 2400
POLL_S = 10

HF_CACHE = "~/.cache/huggingface"
CONTAINER_HF_CACHE = "/root/.cache/huggingface"
HOSTFILE = "/etc/openmpi-hostfile"
LLM_API_YAML = "/tmp/extra-llm-api-config.yml"
CONTAINER_TEMPLATE = "/tmp/chat_template-effort-low.jinja"
#: mpirun's pid, written by the shell that execs it, so `start` can tell a
#: dead server from a slow one.
SERVE_PIDFILE = "/tmp/trtllm-serve.pid"
CONTAINER_LOG_DIR = "/logs"
#: The fabric interface that carries 10.0.0.1 and 10.0.0.2: MPI's own TCP
#: traffic (control only; NCCL carries the tensors) stays on that one link.
MPI_IFACE = "enp1s0f1np1"
#: A docker container name, which is also the served model name here.
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: The snapshot template's default effort (chat_template.jinja line 2 of
#: nvidia/GLM-5.3-Flash-NVFP4 @1d007386), and the default served instead. A
#: request's "low" or "high" would still win, if rc4 passed it on.
TEMPLATE_DEFAULT = "in ['low', 'high'] else 'max' -%}"
TEMPLATE_LOW = "in ['low', 'high'] else 'low' -%}"

#: The least MemAvailable a node may have before a launch: each node holds
#: 95.2 GiB of the 190.4 GiB checkpoint, and the KV cache is sized from what
#: is free after that, so a node with less serves a smaller cache than the
#: backend describes, or fails while it loads.
MIN_MEM_AVAILABLE_GIB = 105
#: Seconds one command on a node may take; ssh gives up connecting sooner.
NODE_TIMEOUT_S = 120
SSH_CONNECT_TIMEOUT_S = 10

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
#: mpirun's own settings, read on the head only. Open MPI 5's mpirun is
#: PRRTE; its ompi personality (src/mca/schizo/ompi/schizo_ompi.c at prrte
#: 643db1bd, the open-mpi/ompi v5.0.10rc2 submodule) maps OMPI_MCA_oob_* to
#: PRTE_MCA_oob_*, and allow_run_as_root() accepts OMPI_ALLOW_RUN_AS_ROOT=1
#: with OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1, or `--allow-run-as-root`.
MPI_ENV = {
    "OMPI_MCA_btl_tcp_if_include": MPI_IFACE,
    "OMPI_MCA_oob_tcp_if_include": MPI_IFACE,
    "OMPI_ALLOW_RUN_AS_ROOT": "1",
    "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM": "1",
}
#: One rank per node, each free to use every core: `-np 2` and `ppr:1:node`
#: with `slots=1` per hostfile line, so the count does not come from cores.
MPI_PLACEMENT = [
    "--allow-run-as-root", "-np", "2", "--hostfile", HOSTFILE,
    "--map-by", "ppr:1:node", "--bind-to", "none",
]  # fmt: skip

# The `trtllm-serve` flags below are the names in
# tensorrt_llm/commands/serve.py at tag v1.4.0rc4 (commit ab2dfeba8e69):
# --served_model_name, --host, --port, --tp_size (alias of
# --tensor_parallel_size), --max_num_tokens, --max_batch_size, --max_seq_len,
# --free_gpu_memory_fraction, --extra_llm_api_options (alias of --config),
# --enable_chunked_prefill (a flag, default off), --chat_template (a file
# path or a template string), --tool_parser, --reasoning_parser. `--host`
# defaults to localhost there. Without chunked prefill,
# tensorrt_llm/llmapi/llm.py `_check_arguments` refuses a prompt longer than
# max_num_tokens. `glm47` is a key in both factories at that tag:
# tensorrt_llm/serve/tool_parser/tool_parser_factory.py (`"glm47":
# Glm47ToolParser`) and tensorrt_llm/llmapi/reasoning_parser.py
# (`@register_reasoning_parser("glm47", reasoning_at_start=True)`). The
# checkpoint's model_type `glm5_next` is in neither auto-map, so `auto` fails.
DEFAULT_PARSER = "glm47"


class LaunchFailed(Exception):
    """A step after the worker container started did not succeed."""


@dataclasses.dataclass(frozen=True)
class Step:
    """One action of `start` or `stop`.

    `argv` runs on the head (the worker through ssh in the argv), fed `stdin`
    if given. `action` names a step that is not a command: "sshd0"/"sshd1"
    (wait for that rank's sshd), "write" (`path` gets `text` on the head), or
    "ready" (wait for /v1/models).
    """

    what: str
    argv: list[str] | None = None
    stdin: str | None = None
    action: str | None = None
    path: str = ""
    text: str = ""
    #: What the log shows instead, when argv carries the entrypoint.
    shown: str | None = None

    def line(self) -> str:
        if self.shown is not None:
            return self.shown
        if self.argv is not None:
            fed = f" < ({len(self.stdin)} bytes on stdin)" if self.stdin else ""
            return shell_line(self.argv) + fed
        if self.action == "write":
            return f"write {self.path}"
        return self.text


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


def low_effort_template(text: str) -> str:
    """The snapshot's chat template with "low" as its default reasoning effort.

    Refuses a template that does not hold the default exactly once, so a
    different template cannot silently serve "max".
    """
    if text.count(TEMPLATE_DEFAULT) != 1:
        raise SystemExit(
            f"the chat template holds {TEMPLATE_DEFAULT!r} "
            f"{text.count(TEMPLATE_DEFAULT)} times, not once: it is not the "
            "template this launcher was written for"
        )
    return text.replace(TEMPLATE_DEFAULT, TEMPLATE_LOW)


def model_rel(model_dir: str) -> str:
    """`model_dir` relative to the hub cache, which both nodes hold."""
    root = pathlib.Path(HF_CACHE).expanduser()
    path = pathlib.Path(model_dir).expanduser()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        raise SystemExit(
            f"--model-dir must sit under {HF_CACHE} (mounted at "
            f"{CONTAINER_HF_CACHE} on both nodes); got {model_dir}"
        ) from None


def container_model_path(model_dir: str) -> str:
    """Where the head and worker containers see `model_dir`.

    The whole hub cache is mounted, not the snapshot: a snapshot's files are
    symlinks into `../../blobs`, which a snapshot-only mount would break.
    """
    return f"{CONTAINER_HF_CACHE}/{model_rel(model_dir)}"


def docker_run(
    *, rank: int, name: str, image: str, script: str, log_dir: str = ""
) -> list[str]:
    """The `docker run` argv for one node. Both run only sshd.

    Only the head mounts `log_dir`: the server and its log live there.
    """
    argv = [
        "docker", "run", "-d", "--name", container(name, rank),
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


def hostfile_text() -> str:
    """One line per node. Without `slots=`, Open MPI 5 counts every core."""
    return f"{HEAD_IP} slots=1\n{WORKER_IP} slots=1\n"


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
    argv = ["mpirun", *MPI_PLACEMENT]
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
        # A prompt longer than max_num_tokens is refused without it.
        "--enable_chunked_prefill",
        # Also on the command line: an explicit flag wins over the YAML file,
        # so the fraction holds whatever the merge rule for defaults is.
        "--free_gpu_memory_fraction", str(args.kv_free_fraction),
        "--extra_llm_api_options", LLM_API_YAML,
        "--chat_template", CONTAINER_TEMPLATE,
        "--tool_parser", args.tool_parser,
        "--reasoning_parser", args.reasoning_parser,
    ]  # fmt: skip
    return argv + list(args.serve_args)


def log_file(name: str) -> str:
    return f"{container(name, 0)}-serve.log"


def argv_file(name: str) -> str:
    return f"{container(name, 0)}-serve-argv.json"


def argv_record(args: argparse.Namespace, template: str) -> str:
    """What the server was told, for rows to cite: no argv reader reads the
    process inside the container."""
    return (
        json.dumps(
            {
                "server_argv": serve_argv(args),
                "image": args.image,
                "entrypoint_sha256": ENTRYPOINT_SHA256,
                "chat_template_sha256": hashlib.sha256(template.encode()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )


#: Stands for the entrypoint's text in logged commands: 50 lines of sshd setup,
#: twice, buried every other step of a dry run.
SCRIPT_SHOWN = f"<{ENTRYPOINT.name}, sha256 {ENTRYPOINT_SHA256[:12]}>"


def start_plan(args: argparse.Namespace, script: str, template: str) -> list[Step]:
    """Every action `start` takes, in order: worker, then head, then serve.

    The logged line of each step names the entrypoint instead of quoting it.
    """
    real = _start_steps(args, script, template)
    shown = _start_steps(args, SCRIPT_SHOWN, template)
    return [
        step
        if step.line() == view.line()
        else dataclasses.replace(step, shown=view.line())
        for step, view in zip(real, shown, strict=True)
    ]


def _start_steps(args: argparse.Namespace, script: str, template: str) -> list[Step]:
    head, worker = container(args.name, 0), container(args.name, 1)
    log_dir = args.log_dir
    run_worker = docker_run(rank=1, name=args.name, image=args.image, script=script)
    run_head = docker_run(
        rank=0, name=args.name, image=args.image, script=script, log_dir=log_dir
    )
    exec_head = ["docker", "exec", head, "bash", "-c"]
    hostfile = f"printf '%s' {shlex.quote(hostfile_text())} > {HOSTFILE}"
    yaml_cmd = (
        f"printf '%s' {shlex.quote(llm_api_yaml(args.kv_free_fraction))}"
        f" > {LLM_API_YAML}"
    )
    serve = (
        f"echo $$ > {SERVE_PIDFILE}; exec {shlex.join(serve_argv(args))}"
        f" > {CONTAINER_LOG_DIR}/{log_file(args.name)} 2>&1"
    )
    ssh = ["ssh", "-o", "BatchMode=yes", WORKER_IP]
    ready = (
        f"poll http://127.0.0.1:{args.port}/v1/models every {POLL_S} s, up to "
        f"{args.ready_timeout} s, checking that {head} and mpirun still run"
    )
    return [
        Step(f"start {worker} on the worker", ssh + [shell_line(run_worker)]),
        Step("make the log directory on the head", ["mkdir", "-p", log_dir]),
        Step(f"start {head} on the head", ["bash", "-c", shell_line(run_head)]),
        Step("wait for sshd in the worker container", action="sshd1",
             text=f"wait up to {SSHD_WAIT_S} s for {WORKER_IP}:{SSH_PORT}, checking that {worker} runs"),
        Step("wait for sshd in the head container", action="sshd0",
             text=f"wait up to {SSHD_WAIT_S} s for {HEAD_IP}:{SSH_PORT}, checking that {head} runs"),
        Step("write the MPI hostfile", [*exec_head, hostfile]),
        Step("write the LLM API options", [*exec_head, yaml_cmd]),
        Step("write the low-effort chat template",
             ["docker", "exec", "-i", head, "bash", "-c", f"cat > {CONTAINER_TEMPLATE}"],
             stdin=template),
        Step("check the template holds the low default",
             ["docker", "exec", head, "grep", "-qF", TEMPLATE_LOW, CONTAINER_TEMPLATE]),
        Step("record the serve argv", action="write",
             path=str(pathlib.Path(log_dir) / argv_file(args.name)),
             text=argv_record(args, template)),
        Step("launch trtllm-serve, detached", ["docker", "exec", "-d", head, "bash", "-c", serve]),
        Step("wait for the API", action="ready", text=ready),
    ]  # fmt: skip


def stop_plan(name: str) -> list[Step]:
    """Remove the head container, then the worker container."""
    head, worker = container(name, 0), container(name, 1)
    return [
        Step(f"remove {head} on the head", ["docker", "rm", "-f", head]),
        Step(
            f"remove {worker} on the worker",
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                WORKER_IP,
                shlex.join(["docker", "rm", "-f", worker]),
            ],
        ),
    ]


def on_node(rank: int, command: str) -> subprocess.CompletedProcess[str]:
    """Run a shell line on the node of `rank`: the head is local.

    A node that does not answer fails in seconds, not at the TCP timeout.
    """
    argv = (
        ["bash", "-c", command]
        if rank == 0
        else [
            "ssh", "-n", "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}", WORKER_IP, command,
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


#: What `refusals` checks, in words, for a dry run to log.
CHECKS = (
    f"nothing listens on :{SSH_PORT} or the API port",
    "no trtllm-* container exists, running or stopped",
    "no GPU compute process runs (nvidia-smi)",
    f"MemAvailable is at least {MIN_MEM_AVAILABLE_GIB} GiB",
    (
        "the snapshot holds config.json, and the worker holds as many "
        ".safetensors files as the head"
    ),
)

_FACTS_SHELL = r"""
M="$HOME/.cache/huggingface/@REL@"
echo "mem_kib=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
ss -ltnH | awk '{print $4}' | grep -E ':(@SSH@|@PORT@)$' | sed 's/^/listen=/'
docker ps -a --format '{{.Names}}' | grep '^trtllm-' | sed 's/^/container=/'
nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>/dev/null |
  grep -E '^[0-9]' | sed 's/^/gpu_app=/'
if [ -f "$M/config.json" ]; then echo model=1; else echo model=0; fi
echo "shards=$(ls "$M" 2>/dev/null | grep -c '\.safetensors$')"
"""


def facts_shell(rel: str, port: int) -> str:
    """A read-only probe of one node, one `key=value` per line."""
    subs = {"@REL@": rel, "@SSH@": str(SSH_PORT), "@PORT@": str(port)}
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


def refusals(facts: dict[str, list[str]], head_shards: str = "") -> list[str]:
    """Every reason this node must not start; empty means go.

    `head_shards` is the head's shard count, given when checking the worker.
    """
    why = []
    for addr in facts.get("listen", []):
        why.append(f"{addr} is already listening")
    for name in facts.get("container", []):
        why.append(f"container {name} exists; run `stop` first")
    for app in facts.get("gpu_app", []):
        why.append(f"a GPU process is running: {app}")
    mem = _one(facts, "mem_kib")
    gib = int(mem) / 2**20 if mem.isdigit() else 0.0
    if gib < MIN_MEM_AVAILABLE_GIB:
        why.append(
            f"MemAvailable {gib:.1f} GiB is under {MIN_MEM_AVAILABLE_GIB} GiB: "
            "something else holds memory"
        )
    if _one(facts, "model") != "1":
        why.append("the snapshot has no config.json: the weights are not there")
    shards = _one(facts, "shards")
    if not shards.isdigit() or shards == "0":
        why.append("the snapshot holds no .safetensors file")
    elif head_shards and shards != head_shards:
        why.append(
            f"the snapshot holds {shards} .safetensors files; the head has {head_shards}"
        )
    return why


def check_nodes(args: argparse.Namespace) -> bool:
    """Probe both nodes; log every refusal. True means both may start."""
    rel = model_rel(args.model_dir)
    facts: dict[int, dict[str, list[str]]] = {}
    ok = True
    for rank in (0, 1):
        r = on_node(rank, facts_shell(rel, args.port))
        facts[rank] = parse_facts(r.stdout)
        head_shards = _one(facts[0], "shards") if rank == 1 else ""
        why = refusals(facts[rank], head_shards)
        if r.returncode and not facts[rank]:
            why = [f"the probe failed: {r.stderr.strip()}"]
        for reason in why:
            logger.error("%s refuses: %s", "worker" if rank else "head", reason)
        ok = ok and not why
    return ok


def container_running(rank: int, name: str) -> bool:
    r = on_node(
        rank, f"docker inspect -f '{{{{.State.Running}}}}' {container(name, rank)}"
    )
    return r.stdout.strip() == "true"


def container_logs(rank: int, name: str) -> str:
    r = on_node(rank, f"docker logs --tail 30 {container(name, rank)} 2>&1")
    return r.stdout.strip() or r.stderr.strip()


def wait_sshd(rank: int, name: str) -> None:
    """Wait for that node's sshd; fail at once if its container stopped."""
    host = HEAD_IP if rank == 0 else WORKER_IP
    deadline = time.monotonic() + SSHD_WAIT_S
    while True:
        try:
            with socket.create_connection((host, SSH_PORT), timeout=5):
                return
        except OSError:
            pass
        if not container_running(rank, name):
            raise LaunchFailed(
                f"{container(name, rank)} stopped before sshd listened; its log:\n"
                + container_logs(rank, name)
            )
        if time.monotonic() > deadline:
            raise LaunchFailed(f"{host}:{SSH_PORT} did not accept in {SSHD_WAIT_S} s")
        time.sleep(POLL_S)


def models_answer(port: int, name: str) -> bool:
    """True once /v1/models lists the served name."""
    url = f"http://127.0.0.1:{port}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode(errors="replace")
    except (urllib.error.URLError, OSError):
        return False
    return name in body


def wait_ready(args: argparse.Namespace) -> None:
    """Wait for /v1/models; fail at once if the container or mpirun stopped."""
    head = container(args.name, 0)
    deadline = time.monotonic() + args.ready_timeout
    alive = f'kill -0 "$(cat {SERVE_PIDFILE})"'
    while not models_answer(args.port, args.name):
        if not container_running(0, args.name):
            raise LaunchFailed(f"{head} stopped while the server loaded")
        if on_node(0, f"docker exec {head} bash -c {shlex.quote(alive)}").returncode:
            raise LaunchFailed("mpirun exited before /v1/models answered")
        if time.monotonic() > deadline:
            raise LaunchFailed(f"/v1/models did not answer in {args.ready_timeout} s")
        time.sleep(POLL_S)


def run_step(step: Step, args: argparse.Namespace) -> None:
    if step.argv is not None:
        r = subprocess.run(step.argv, input=step.stdin, text=True, check=False)
        if r.returncode:
            raise LaunchFailed(f"{step.what} exited {r.returncode}")
    elif step.action in ("sshd0", "sshd1"):
        wait_sshd(int(step.action[-1]), args.name)
    elif step.action == "write":
        pathlib.Path(step.path).write_text(step.text)
    elif step.action == "ready":
        wait_ready(args)


def tail(path: pathlib.Path, lines: int = 40) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError as exc:
        return f"(cannot read {path}: {exc})"


def read_template(args: argparse.Namespace) -> str:
    """The low-effort template from the head's snapshot.

    A dry run without the snapshot stands a placeholder in; a real start
    refuses.
    """
    src = pathlib.Path(args.model_dir).expanduser() / "chat_template.jinja"
    try:
        return low_effort_template(src.read_text())
    except OSError:
        if not args.dry_run:
            raise SystemExit(f"cannot read {src}") from None
        logger.info("dry run: %s is not readable here; a start would refuse", src)
        return "<the snapshot's chat_template.jinja, default effort low>"


def start(args: argparse.Namespace) -> int:
    script = entrypoint_text()
    template = read_template(args)
    plan = start_plan(args, script, template)
    if args.dry_run:
        for check in CHECKS:
            logger.info("check on both nodes: %s", check)
        for n, step in enumerate(plan, 1):
            logger.info("%d. %s: %s", n, step.what, step.line())
        logger.info("dry run, nothing ran")
        return 0
    if not check_nodes(args):
        return 1
    log = pathlib.Path(args.log_dir) / log_file(args.name)
    for n, step in enumerate(plan, 1):
        logger.info("%d. %s: %s", n, step.what, step.line())
        try:
            run_step(step, args)
        except (LaunchFailed, OSError) as exc:
            logger.error("%s failed: %s", step.what, exc)
            if n == 1:
                return 1  # no container of ours exists yet
            logger.error("serve log %s, last lines:\n%s", log, tail(log))
            logger.error("removing both containers")
            run_stop(args.name)
            return 1
    logger.info("started: %s answers on :%d; log %s", args.name, args.port, log)
    return 0


def run_stop(name: str) -> None:
    # check=False: a container that is already gone is not an error here.
    for step in stop_plan(name):
        logger.info("%s: %s", step.what, step.line())
        assert step.argv is not None
        subprocess.run(step.argv, check=False)


def stop(args: argparse.Namespace) -> int:
    if args.dry_run:
        for step in stop_plan(args.name):
            logger.info("%s: %s", step.what, step.line())
        logger.info("dry run, nothing ran")
        return 0
    run_stop(args.name)
    return 0


def container_name(value: str) -> str:
    if not NAME_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"{value!r}: use letters, digits, '.', '_' and '-' only"
        )
    return value


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
    p_start.add_argument(
        "--name", required=True, type=container_name, help="the served model name"
    )
    p_start.add_argument("--port", type=int, default=8888)
    p_start.add_argument("--max-num-tokens", type=int, default=32768)
    p_start.add_argument("--max-batch-size", type=int, default=4)
    p_start.add_argument("--max-seq-len", type=int, default=262144)
    p_start.add_argument("--kv-free-fraction", type=float, default=0.8)
    p_start.add_argument("--tool-parser", default=DEFAULT_PARSER)
    p_start.add_argument("--reasoning-parser", default=DEFAULT_PARSER)
    p_start.add_argument("--log-dir", default=str(pathlib.Path.home() / "bench-logs"))
    p_start.add_argument("--ready-timeout", type=int, default=READY_WAIT_S)
    p_start.add_argument("--dry-run", action="store_true")
    p_start.add_argument("serve_args", nargs=argparse.REMAINDER)
    p_stop = sub.add_parser("stop", help="remove both containers")
    p_stop.add_argument("--name", required=True, type=container_name)
    p_stop.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "start":
        if args.serve_args[:1] == ["--"]:
            args.serve_args = args.serve_args[1:]
        # Absolute: docker reads a relative -v source as a volume name.
        args.log_dir = str(pathlib.Path(args.log_dir).expanduser().resolve())
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    return start(args) if args.cmd == "start" else stop(args)


if __name__ == "__main__":
    logs.configure()
    sys.exit(main())
