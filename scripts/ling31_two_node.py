"""Serve Ling-3.1-flash-1019 EXL3 on both DGX Sparks with bertholomus's TensorFold TP2.

The engine is bertholomus/TensorFold, branch `ling-3.1-tp2`, at the commit the
recipe bertholomus/ling-3.1-tensorfold-tp2-2xgb10 v0.2.0 names (#1309): the
`bailing_v3` family on TensorFold v0.6.6's Python CUDA engine. It is not the
native release binary that `tensorfold_native_two_node.py` runs, so it has its
own checkout and its own build directory and touches no shared install.

The recipe gives a command line, not a launcher. This script runs that command
in the NGC PyTorch image, one rank per Spark, with five local choices:

- **Thinking is off on the server** (`--no-thinking`, operator 2026-11-27).
  The author got clean runs only with thinking off; with it on, long thinking
  hung the serve. A request turns thinking back on when it sends
  `reasoning_effort` or `chat_template_kwargs.reasoning_effort` (other than
  "none"), `chat_template_kwargs.enable_thinking`, or
  `chat_template_kwargs.thinking` (`thinking_fields` in
  `tensorfold/server/request_options.py` at 72f16e48). So the backend sets
  `client_effort = "unset"`, preflight refuses an OpenCode entry that sends
  one, and `check-thinking` proves afterwards that no request thought.
- **Each rank is a `systemd-run --user --property=KillMode=process` unit**
  whose main process is the foreground `docker run`, its output appended to
  `~/bench-logs/<name>-rank<N>.log` (#691).
- **NCCL names all four fabric interfaces and RoCE devices, GID index 3.** The
  recipe names one HCA and GID index 5; our fabric uses index 3 (#930).
- **Rank 0 binds 0.0.0.0:8888** (the runbook's port and bind rule); the recipe
  binds 127.0.0.1:8322. Rank 1 serves no HTTP.
- **The recipe's own guards run inside each container:** `evict_page_cache.sh`
  keeps the rank folder out of the page cache, and `watchdog.sh` kills the
  server when the node's MemAvailable falls under 1.5 GiB, before earlyoom
  (1 GiB) picks a victim. Before a start, both nodes drop the page cache
  (the recipe's step 2), which needs passwordless sudo.

    uv run python scripts/ling31_two_node.py prepare --rank 0
    uv run python scripts/ling31_two_node.py prepare --rank 1   # from the head too
    uv run python scripts/ling31_two_node.py start --name ling31 --arm mtp
    uv run python scripts/ling31_two_node.py status --name ling31
    uv run python scripts/ling31_two_node.py stop --name ling31
    uv run python scripts/ling31_two_node.py check-thinking --arm mtp \
        --log ~/bench-logs/ling31-rank0.log --results <ledger>

`prepare` runs once per node: it checks the split, writes the node's rank
folder from the verified checkpoint (`~/models/<MODEL>`, a full copy on each
node), and builds the CUDA extensions with no model loaded (the recipe's
`serve/prebuild.py`). `start` takes no server arguments: the arm is
`SERVE_ARGS` plus the flags of one named arm (`--arm`, required, see `ARMS`),
and a passthrough would change it while the row still pooled (argparse keeps
the last value, so `-- --thinking` turned thinking on with `--no-thinking`
still in the recorded argv). A new setting is a new arm and a new backend.
Each arm gets its own `--name`, so its rank-0 log holds only its own starts:

    uv run python scripts/ling31_two_node.py start --name ling31nomtp --arm nomtp
    uv run python scripts/ling31_two_node.py check-thinking --arm nomtp \
        --log ~/bench-logs/ling31nomtp-rank0.log --results <ledger>

Both containers run as the calling user, so nothing they write is owned by
root. Rank 0 logs `READY_LINE` when it is about to serve; wait on a real
completion, not on that line.
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

#: bertholomus/TensorFold `ling-3.1-tp2` at the recipe v0.2.0 engine (README:
#: "Engine: commit 72f16e48"). It was the branch tip on 2026-11-27.
ENGINE_SHA = "72f16e48fc501f31d0d1239459cb53cd5e43d36a"
#: bertholomus/ling-3.1-tensorfold-tp2-2xgb10 v0.2.0, its tip on 2026-11-27.
RECIPE_SHA = "6088f47241c434e4263043a97a25784b03b0d1df"
ENGINE_TREE = "ling31-tensorfold"
RECIPE_TREE = "ling31-tf-recipe"
#: TORCH_EXTENSIONS_DIR and TRITON_CACHE_DIR, outside the checkout.
BUILD_DIR = f"engines/ling31-tf-{ENGINE_SHA[:8]}"
#: `prepare` writes this into BUILD_DIR after a clean prebuild.
PREBUILD_MARK = "prebuild.ok"
#: The approved checkpoint (#1309), downloaded at this revision.
MODEL = "bertholomus--Ling-3.1-flash-1019-EXL3-3.03bpw-15d305cc66"
#: The checkpoint's weight shards at 15d305cc66.
MODEL_SHARDS = 35
IMAGE = "nvcr.io/nvidia/pytorch:26.07-py3"
PORT = 8888
#: The recipe's rendezvous port.
MASTER_PORT = 29577
HEAD_IP = "10.0.0.1"
WORKER = "spark-b-cx7"
IFACES = "enp1s0f1np1,enP2p1s0f1np1,enp1s0f0np0,enP2p1s0f0np0"
IB_HCAS = "rocep1s0f1,roceP2p1s0f1,rocep1s0f0,roceP2p1s0f0"
IB_GID_INDEX = 3
SERVED_NAME = "ling-3.1-flash-1019"
#: The recipe's serving settings (README, "Run it", step 4), verbatim.
RECIPE_ENV = {
    "TENSORFOLD_MEMORY_RESERVE_GIB": "4",
    "TF_LING_CACHE_GIB": "0",
    "TF_LING_LATENT_MB": "270",
    "TF_LING_FILLED": "1",
    "TF_GLM_UNPACK_CACHE_MB": "0",
    "TF_LING_SHARED_SCRATCH": "1",
    "TF_LING_EMBED_SPLIT": "1",
    "NCCL_BUFFSIZE": "1048576",
    "NCCL_MAX_NCHANNELS": "4",
    "MALLOC_ARENA_MAX": "2",
    "TORCH_ALLOW_TF32_CUBLAS_OVERRIDE": "0",
}
#: The recipe's flags, plus thinking off, the port, and the sampler named
#: explicitly so the row's argv records it (#84). The sampler is the
#: inclusionAI/Ling-3.1-flash-1019 card's recommendation and the engine's own
#: default (tensorfold/cuda/server.py, App.__init__, at 72f16e48); the
#: checkpoint ships no generation_config.json. `--max-tokens` is only the reply
#: limit of a request that names none (OpenCode sends 32,768), and the answer
#: reserve acts only on a reply that thinks, so it does nothing here.
SERVE_ARGS = (
    "--backend", "cuda", "--tp", "2",
    "--master", HEAD_IP, "--master-port", str(MASTER_PORT),
    "--port", str(PORT),
    "--context", "262144",
    "--max-tokens", "4096",
    "--answer-reserve", "0.25", "--answer-reserve-min", "1024",
    "--temperature", "1.0", "--top-k", "20", "--top-p", "0.95",
    "--name", SERVED_NAME,
    "--no-thinking",
    "--no-update-check",
)  # fmt: skip
#: The arms `start --arm` serves: each adds its flags to `SERVE_ARGS` on both
#: ranks, so the row's recorded argv names the arm (#680 finding 2: two arms
#: with one argv cannot be told apart). `mtp` is the recipe's MTP drafting at
#: adaptive depth. `nomtp` (#1309, operator 2026-11-27) decodes one token a
#: round: `--no-drafts` (tensorfold/cli_args.py:82 at 72f16e48) reaches the
#: engine as `serial_only` (families/bailing_v3/__init__.py:108), which sets
#: the policy to "0" (cuda/engine.py:230) and overrides a request's
#: `tf_policy` (cuda/engine.py:570). The MTP head still loads (`mtp_head`
#: reads only TF_LING_MTP, cuda/engine.py:92-98), so memory is the same arm's.
ARMS: dict[str, tuple[str, ...]] = {
    "mtp": (),
    "nomtp": ("--no-drafts",),
}
#: The backend in tasks.toml that each arm's rows belong to.
BACKENDS: dict[str, str] = {
    "mtp": "ling31f1019exl3tfbertnothinkdual2xrc",
    "nomtp": "ling31f1019exl3tfbertnothinknomtpdual2xrc",
}
#: The recipe's watchdog floor in GiB; earlyoom acts at 1 GiB.
WATCHDOG_FLOOR_GIB = "1.5"
#: The least MemAvailable a node may have before a start. The engine's own
#: admission check needed 109.09 GiB on 2026-11-27 (the head had 109.26 after
#: the page-cache drop), plus a few GiB for the decode growth seen under load.
#: Refusing here comes before the caches are dropped and the units start.
MIN_MEM_AVAILABLE_GIB = 114
#: Modules that `prebuild.py` builds at 72f16e48: each prints "ready in". Both
#: nodes logged 6 on 2026-11-27; two more print "no _ext".
PREBUILD_READY = 6
GPU_MONITORS = frozenset({"dcgm", "dcgm-exporter"})
NODE_TIMEOUT_S = 120
SSH_CONNECT_TIMEOUT_S = 10
#: `prepare` reads ~205 GiB and builds extensions; allow it hours.
PREPARE_TIMEOUT_S = 4 * 3600
#: Rank 0 prints this when it is about to serve (tensorfold/cli.py).
READY_LINE = f"at http://0.0.0.0:{PORT}/v1 on CUDA"
STOP_FILE = "/tmp/ling31-stop"


def rank_dir(rank: int) -> str:
    """The node's rank folder under ~/models."""
    return f"{MODEL}-tp2-rank{rank}"


def container_env() -> dict[str, str]:
    """The recipe's environment plus the fabric, offline, and the build dirs."""
    return {
        **RECIPE_ENV,
        "NCCL_SOCKET_IFNAME": IFACES,
        "NCCL_IB_HCA": IB_HCAS,
        "NCCL_IB_GID_INDEX": str(IB_GID_INDEX),
        "PYTHONPATH": "/work/engine/src",
        "TORCH_EXTENSIONS_DIR": "/work/build/torch_extensions",
        "TRITON_CACHE_DIR": "/work/build/triton",
        "TENSORFOLD_NO_UPDATE_CHECK": "1",
        # The row's record of the engine build, under the TENSORFOLD_ prefix
        # that server_facts.py reads from the container (#1309 review).
        "TENSORFOLD_ENGINE_COMMIT": ENGINE_SHA,
        "HF_HUB_OFFLINE": "1",
        # The containers run as the calling user (`--user`), who has no home
        # in the image; torch, Triton and getpass need one and a name.
        "HOME": "/work/build",
        "USER": "ling31",
    }


def serve_argv(rank: int, arm: str) -> list[str]:
    """`tensorfold serve` for one rank. Only rank 0 listens on the LAN.

    Both ranks get the arm's flags: the engine refuses to start when the
    ranks differ in drafts (cuda/engine.py:261-270 at 72f16e48).
    """
    host = "0.0.0.0" if rank == 0 else "127.0.0.1"
    return [
        "python3", "-m", "tensorfold", "serve", "/model",
        *SERVE_ARGS, *ARMS[arm], "--rank", str(rank), "--host", host,
    ]  # fmt: skip


def rank_shell() -> str:
    """The container's script: the guards, then the server given as "$@".

    The server's argv goes to bash as positional arguments, not inside the
    script, so the container's command lists `serve` and `--name <model>` as
    their own tokens. `run.serving_tensorfold` matches on those tokens and
    records the whole command as the row's `server_argv` (#213), so each row
    carries `--no-thinking`. The trap passes the container's SIGTERM on to the
    server and stops both guards; bash would otherwise leave the server to
    docker's SIGKILL.
    """
    evict = shlex.join(
        ["bash", "/work/recipe/serve/evict_page_cache.sh", "/model/*.safetensors"]
    )
    watchdog = ["bash", "/work/recipe/serve/watchdog.sh"]
    return (
        f"rm -f {STOP_FILE}; cd /work/engine; "
        f"{evict} {STOP_FILE} & "
        '"$@" & pid=$!; '
        f"trap 'touch {STOP_FILE}; kill -TERM $pid' TERM INT; "
        f"{shlex.join(watchdog)} $pid {STOP_FILE} /dev/stderr "
        f"{WATCHDOG_FLOOR_GIB} & "
        f"wait $pid; rc=$?; wait $pid 2>/dev/null; touch {STOP_FILE}; exit $rc"
    )


def _mounts(home: str) -> list[str]:
    return [
        "-v", f"{home}/{ENGINE_TREE}:/work/engine:ro",
        "-v", f"{home}/{RECIPE_TREE}:/work/recipe:ro",
        "-v", f"{home}/{BUILD_DIR}:/work/build",
    ]  # fmt: skip


def docker_run(*, rank: int, name: str, home: str, user: str, arm: str) -> list[str]:
    """The foreground `docker run` argv for one rank (the recipe's flags).

    `user` is the node's `uid:gid`: the build dir and the logs stay the
    user's, so no later step needs sudo to clean or rebuild them.
    """
    argv = [
        "docker", "run", "--name", f"{name}-r{rank}", "--user", user,
        "--gpus", "all", "--network", "host", "--ipc=host",
        "--device", "/dev/infiniband", "--ulimit", "memlock=-1",
        *_mounts(home),
        "-v", f"{home}/models/{rank_dir(rank)}:/model:ro",
    ]  # fmt: skip
    for key, value in container_env().items():
        argv += ["-e", f"{key}={value}"]
    argv += ["--entrypoint", "bash", IMAGE, "-c", rank_shell(), "ling31"]
    argv += serve_argv(rank, arm)
    return argv


def unit_name(name: str, rank: int) -> str:
    return f"{name}-rank{rank}"


def log_path(home: str, name: str, rank: int) -> str:
    return f"{home}/bench-logs/{unit_name(name, rank)}.log"


def systemd_unit(*, rank: int, name: str, home: str, user: str, arm: str) -> list[str]:
    """`systemd-run` wrapping one rank's `docker run` as the unit's main process."""
    log = log_path(home, name, rank)
    return [
        "systemd-run", "--user", f"--unit={unit_name(name, rank)}",
        "--property=KillMode=process",
        f"--property=StandardOutput=append:{log}",
        f"--property=StandardError=append:{log}",
        *docker_run(rank=rank, name=name, home=home, user=user, arm=arm),
    ]  # fmt: skip


PREBUILD_SCRIPT = "/work/recipe/serve/prebuild.py"


def prepare_shell(rank: int) -> str:
    """The container's command for `prepare`: check, split, then prebuild.

    The split skips a rank file that already exists, so a half-written folder
    would be served as if whole. It writes into `<dir>.partial` and renames
    only on success, and refuses to resume a `.partial` it did not finish.

    `prebuild.py` catches a failed module and goes on, and a crash before its
    loop exits nonzero. So the shell runs with `pipefail` (a `| tee` hid that
    exit, and the mark was written after a "No CUDA GPUs" crash), keeps
    stderr in the log, and writes the mark only when PREBUILD_READY modules
    say "ready in" and none says "failed".
    """
    out = f"/models/{rank_dir(rank)}"
    log = "/work/build/prebuild.log"
    split = (
        f"TF_TP_WORLD=2 python3 -m tensorfold.families.glm5_next.cuda.split "
        f"/models/{MODEL} --rank {rank} {out}.partial"
    )
    return (
        f"set -eo pipefail; rm -f /work/build/{PREBUILD_MARK}; cd /work/engine; "
        f'if [ -e {out}.partial ]; then echo "{out}.partial exists: an earlier '
        'split stopped; remove it by hand and rerun" >&2; exit 1; fi; '
        f"if [ -d {out} ]; then echo 'rank folder present: {out}'; else "
        f"python3 tools/ling_split_check.py /models/{MODEL} --world 2; "
        f"{split}; mv {out}.partial {out}; fi; "
        "mkdir -p /work/build/torch_extensions /work/build/triton; "
        f"python3 {PREBUILD_SCRIPT} 2>&1 | tee {log}; "
        f"if grep -q 'failed' {log}; then echo 'a module failed to build' >&2; "
        "exit 1; fi; "
        f"n=$(grep -c 'ready in' {log} || true); "
        f'if [ "$n" -ne {PREBUILD_READY} ]; then echo "$n modules ready, '
        f'not {PREBUILD_READY}" >&2; exit 1; fi; '
        f"echo {ENGINE_SHA} > /work/build/{PREBUILD_MARK}"
    )


def prepare_run(*, rank: int, home: str, user: str) -> list[str]:
    """The `docker run --rm` for `prepare` on one node, as the user."""
    argv = [
        "docker", "run", "--rm", "--name", f"ling31-prepare-r{rank}",
        "--user", user, "--gpus", "all", *_mounts(home),
        "-v", f"{home}/models:/models",
    ]  # fmt: skip
    for key, value in container_env().items():
        argv += ["-e", f"{key}={value}"]
    argv += ["--entrypoint", "bash", IMAGE, "-c", prepare_shell(rank)]
    return argv


_FACTS_SHELL = r"""
E="$HOME/@TREE@"; R="$HOME/@RTREE@"; B="$HOME/@BUILD@"; M="$HOME/models/@RANKDIR@"
systemctl --user reset-failed @UNIT@ >/dev/null 2>&1 || true
echo "home=$HOME"
echo "user=$(id -u):$(id -g)"
echo "engine_head=$(git -C "$E" rev-parse HEAD 2>/dev/null)"
echo "engine_dirty=$(git -C "$E" status --porcelain 2>/dev/null | wc -l)"
echo "recipe_head=$(git -C "$R" rev-parse HEAD 2>/dev/null)"
echo "recipe_dirty=$(git -C "$R" status --porcelain 2>/dev/null | wc -l)"
echo "prebuild=$(cat "$B/@MARK@" 2>/dev/null)"
echo "root_owned=$(find "$B" "$M" -maxdepth 1 ! -user "$(id -u)" 2>/dev/null | wc -l)"
echo "rank_shards=$(ls "$M"/*.rank@RANK@.safetensors 2>/dev/null | wc -l)"
[ -e "$M.partial" ] && echo partial=1
echo "mem_kib=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
ss -ltnH | awk '{print $4}' | grep -E ':(@PORT@|@MPORT@)$' | sed 's/^/listen=/'
echo "unit_active=$(systemctl --user is-active @UNIT@ 2>/dev/null)"
docker ps -q | xargs -r docker inspect \
  --format '{{.Name}} {{if .HostConfig.DeviceRequests}}gpu{{end}}' |
  awk '$2 == "gpu" {sub(/^\//, "", $1); print "gpu_container=" $1}'
nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>/dev/null |
  grep -E '^[0-9]' | sed 's/^/gpu_app=/'
"""


def facts_shell(name: str, rank: int) -> str:
    """A read-only probe of one node, one `key=value` per line.

    Its only write is `reset-failed` on this rank's unit: a failed transient
    unit keeps its name and blocks the next `systemd-run`.
    """
    subs = {
        "@TREE@": ENGINE_TREE,
        "@RTREE@": RECIPE_TREE,
        "@BUILD@": BUILD_DIR,
        "@MARK@": PREBUILD_MARK,
        "@RANKDIR@": rank_dir(rank),
        "@RANK@": str(rank),
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
        why.append(
            f"engine checkout ~/{ENGINE_TREE} has edits or untracked files "
            "(git status --porcelain)"
        )
    recipe = _one(facts, "recipe_head")
    if recipe != RECIPE_SHA:
        why.append(f"recipe checkout at {recipe or 'nothing'}, not {RECIPE_SHA}")
    if _one(facts, "recipe_dirty") != "0":
        why.append(
            f"recipe checkout ~/{RECIPE_TREE} has edits or untracked files "
            "(git status --porcelain)"
        )
    if _one(facts, "prebuild") != ENGINE_SHA:
        why.append(f"no prebuild of {ENGINE_SHA[:8]} in ~/{BUILD_DIR}: run prepare")
    if _one(facts, "root_owned") not in ("", "0"):
        why.append(
            f"~/{BUILD_DIR} or ~/models/{rank_dir(rank)} holds files another "
            "user owns; the container runs as you and cannot write them"
        )
    if not _one(facts, "user"):
        why.append("the probe did not report the user's uid:gid")
    shards = _one(facts, "rank_shards")
    if shards != str(MODEL_SHARDS):
        why.append(
            f"~/models/{rank_dir(rank)} holds {shards or 0} rank shards, "
            f"not {MODEL_SHARDS}: run prepare"
        )
    if _one(facts, "partial"):
        why.append(f"~/models/{rank_dir(rank)}.partial exists: a split stopped")
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


#: The recipe's step 2: the GPU allocates from the page cache's memory.
DROP_CACHES = "sudo -n sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'"


def setup_shell(name: str, rank: int) -> str:
    """After the checks pass: the log dir, no stale container, a cold cache."""
    return (
        'mkdir -p "$HOME/bench-logs"; '
        f"docker rm -f {shlex.quote(name)}-r{rank} >/dev/null 2>&1 || true; "
        f"{DROP_CACHES}"
    )


def on_node(
    rank: int, command: str, timeout: int = NODE_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    """Run a shell line on the node of `rank`: the head is local."""
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
            argv, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, 124, "", f"no answer from rank {rank}'s node in {timeout} s"
        )


def node_identity(rank: int) -> tuple[str, str]:
    """The node's `$HOME` and `uid:gid`, or empty strings."""
    out = on_node(rank, 'echo "$HOME"; echo "$(id -u):$(id -g)"').stdout.split()
    return (out[0], out[1]) if len(out) == 2 else ("", "")


def prepare(args: argparse.Namespace) -> int:
    home, user = node_identity(args.rank)
    if not home or not user:
        logger.error("rank %d's node did not answer", args.rank)
        return 1
    line = shlex.join(prepare_run(rank=args.rank, home=home, user=user))
    logger.info("rank %d prepare: %s", args.rank, line)
    r = on_node(args.rank, line, timeout=PREPARE_TIMEOUT_S)
    for text in (r.stdout, r.stderr):
        for out in text.strip().splitlines()[-40:]:
            logger.info("rank %d: %s", args.rank, out)
    if r.returncode:
        logger.error("rank %d prepare failed (exit %d)", args.rank, r.returncode)
        return 1
    logger.info("rank %d prepared", args.rank)
    return 0


def start(args: argparse.Namespace) -> int:
    facts: dict[int, dict[str, list[str]]] = {}
    refused = False
    for rank in (0, 1):
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
    users = {rank: _one(facts[rank], "user") for rank in (0, 1)}
    for rank in (0, 1):
        r = on_node(rank, setup_shell(args.name, rank))
        if r.returncode:
            logger.error("rank %d setup failed: %s", rank, r.stderr.strip())
            return 1
    # Rank 0 first: it hosts the rendezvous store on MASTER_PORT, and the
    # recipe starts the two within a second of each other.
    for rank in (0, 1):
        line = shlex.join(
            systemd_unit(
                rank=rank,
                name=args.name,
                home=homes[rank],
                user=users[rank],
                arm=args.arm,
            )
        )
        logger.info("rank %d: %s", rank, line)
        r = on_node(rank, line)
        if r.returncode:
            logger.error("rank %d did not start: %s", rank, r.stderr.strip())
            if rank == 1:  # do not leave rank 0 waiting for its peer
                on_node(0, stop_shell(args.name, 0))
                logger.error("stopped rank 0 because rank 1 did not start")
            return 1
        logger.info("rank %d log: %s", rank, log_path(homes[rank], args.name, rank))
    logger.info(
        "started %s (arm %s, backend %s); rank 0 serves :%d; ready line %r",
        args.name,
        args.arm,
        BACKENDS[args.arm],
        PORT,
        READY_LINE,
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


#: The engine logs one line per finished request (tensorfold/cuda/server.py,
#: "[tensorfold] done req-... thinking=<bool> ... accepted=<A>/<D>", at
#: 72f16e48, print_done at server.py:691-694).
DONE_PREFIX = "[tensorfold] done req-"
#: Rank 0 logs this banner once per start, with "drafts: off" or "drafts: on"
#: (tensorfold/cli.py:329-330 at 72f16e48). The units append to one log per
#: `--name`, so only the lines after the last banner belong to this start.
SERVING_PREFIX = "[tensorfold] serving "
#: A serial reply logs no drafts. With `--no-drafts` the policy is "0"
#: (cuda/engine.py:570), which `encode_policy` makes kind 0 and
#: `decode_policy` returns as None (engine.py:58-70), so the reply decodes in
#: `serial_decode` and its stats get no drafted or accepted count
#: (engine.py:539-556); `print_done` prints the default 0 for each.
SERIAL_ACCEPTED = " accepted=0/0"
BACKEND = BACKENDS["mtp"]
#: Every arm flag. A row's recorded argv must carry exactly its arm's flags.
ARM_FLAGS = frozenset(flag for flags in ARMS.values() for flag in flags)


def _this_start(log_text: str) -> tuple[str | None, list[str]]:
    """The last serving banner in the log and the lines after it."""
    lines = log_text.splitlines()
    at = [i for i, ln in enumerate(lines) if SERVING_PREFIX in ln]
    if not at:
        return None, []
    return lines[at[-1]], lines[at[-1] + 1 :]


def _row_argv(row: dict[str, object]) -> str | None:
    env = row.get("env")
    argv = env.get("server_argv") if isinstance(env, dict) else None
    return argv if isinstance(argv, str) and argv.strip() else None


def thinking_problems(
    log_text: str, rows: list[dict[str, object]], arm: str
) -> list[str]:
    """Why a run of this arm is not proven thinking-off; empty means proven.

    The arm is thinking off (#1309). A request that sends an effort or a
    thinking switch turns it back on per request, and the row would still
    claim the arm. So the check reads only this start's part of the rank-0
    log (after the last serving banner) and only the rows of this arm's
    backend, and it needs both: requests, none with thinking=True, and at
    least one row, each with 0 reasoning tokens. An empty log, ledger or
    selection proves nothing.

    It also proves the arm (#680 finding 2): the banner says "drafts: off"
    exactly for an arm with `--no-drafts`, each request of such an arm logs
    no drafts, and each row's recorded server_argv carries exactly its arm's
    flags, so a row labeled with one arm and served by the other fails.

    A timed-out row has no reasoning_tokens; run.py puts the count from the
    partial transcript in partial_reasoning_tokens (results.py). The check
    reads that one for such a row, so a timeout does not fail it on its own
    (the 2026-11-27 screen: 3 timeouts, each with 0). A row with neither
    count still fails: a missing count is not 0.
    """
    why = []
    serial = "--no-drafts" in ARMS[arm]
    banner, after = _this_start(log_text)
    if banner is None:
        why.append("the rank-0 log has no serving banner: no start is proven")
    else:
        want = "drafts: off" if serial else "drafts: on"
        if want not in banner:
            why.append(f"the last serving banner does not say {want!r}: {banner}")
    done = [ln for ln in after if DONE_PREFIX in ln]
    if not done:
        why.append(
            "the rank-0 log has no finished requests after its last serving "
            "banner: nothing is proven"
        )
    on = [ln for ln in done if " thinking=True " in ln]
    if on:
        why.append(f"{len(on)} of {len(done)} requests ran with thinking=True")
    odd = [ln for ln in done if " thinking=" not in ln]
    if odd:
        why.append(f"{len(odd)} request lines carry no thinking= field")
    if serial:
        drafted = [ln for ln in done if SERIAL_ACCEPTED not in ln]
        if drafted:
            why.append(
                f"{len(drafted)} of {len(done)} requests of the {arm} arm do not "
                f"log{SERIAL_ACCEPTED}: they drafted"
            )
    backend = BACKENDS[arm]
    mine = [r for r in rows if r.get("backend") == backend]
    if not mine:
        why.append(f"none of the {len(rows)} rows is a {backend} row")
    bad = [r for r in mine if _reasoning_count(r) != 0]
    if bad:
        why.append(
            f"{len(bad)} of {len(mine)} {backend} rows have reasoning_tokens "
            "(or, for a timeout, partial_reasoning_tokens) other than 0"
        )
    want_flags = set(ARMS[arm])
    no_argv = [r for r in mine if _row_argv(r) is None]
    if no_argv:
        why.append(
            f"{len(no_argv)} of {len(mine)} {backend} rows record no server_argv"
        )
    wrong = [
        r
        for r in mine
        if (argv := _row_argv(r)) is not None
        and {t for t in argv.split() if t in ARM_FLAGS} != want_flags
    ]
    if wrong:
        why.append(
            f"{len(wrong)} of {len(mine)} {backend} rows record a server_argv "
            f"whose arm flags are not {sorted(want_flags)}: another arm served them"
        )
    return why


def _reasoning_count(row: dict[str, object]) -> object:
    """The row's reasoning count: the full one, else a timeout's partial one."""
    if row.get("reasoning_tokens") is not None:
        return row["reasoning_tokens"]
    return row.get("partial_reasoning_tokens")


def check_thinking(args: argparse.Namespace) -> int:
    import json

    log_text = pathlib.Path(args.log).expanduser().read_text(errors="replace")
    rows: list[dict[str, object]] = []
    for path in args.results:
        for line in pathlib.Path(path).expanduser().read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    why = thinking_problems(log_text, rows, args.arm)
    for reason in why:
        logger.error("thinking check: %s", reason)
    if why:
        return 1
    logger.info(
        "thinking check: every request and %s row of the %s arm ran with "
        "thinking off and the arm's drafts",
        BACKENDS[args.arm],
        args.arm,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_prep = sub.add_parser(
        "prepare", help="split this node's rank folder and build the extensions"
    )
    p_prep.add_argument("--rank", type=int, choices=(0, 1), required=True)
    p_start = sub.add_parser(
        "start", help="start rank 0 here, then rank 1 (takes no server arguments)"
    )
    p_start.add_argument("--name", default="ling31")
    p_start.add_argument(
        "--arm",
        choices=sorted(ARMS),
        required=True,
        help="mtp: the recipe's MTP drafting; nomtp: --no-drafts (see ARMS)",
    )
    for cmd, text in (("stop", "stop both ranks"), ("status", "show both ranks")):
        p = sub.add_parser(cmd, help=text)
        p.add_argument("--name", default="ling31")
    p_check = sub.add_parser(
        "check-thinking", help="prove no request or row ran with thinking on"
    )
    p_check.add_argument(
        "--arm",
        choices=sorted(ARMS),
        required=True,
        help="the arm the log and rows must prove (see ARMS and BACKENDS)",
    )
    p_check.add_argument("--log", required=True, help="the rank-0 log")
    p_check.add_argument(
        "--results", action="append", default=[], help="a ledger (repeatable)"
    )
    args = parser.parse_args(argv)
    if args.cmd == "prepare":
        return prepare(args)
    if args.cmd == "start":
        return start(args)
    if args.cmd == "check-thinking":
        return check_thinking(args)
    return stop(args) if args.cmd == "stop" else status(args)


if __name__ == "__main__":
    logs.configure()
    sys.exit(main())
