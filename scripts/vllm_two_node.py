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

FlashInfer's autotune cache is kept across starts (#1514). Autotune picks
kernels by timing. Its result went to the container's own `/root/.cache`, so
every start paid it again and could pick different kernels. The #1503 smoke
(2026-11-26) estimated HY4 Flash's autotune at about 10.5 min of an 803 s
start. Measured on 2026-11-27, one start each: engine init took 306.41 s with
an empty cache and 134.87 s with the cache, and the first completion came
433 s and 273 s after launch. Now each node mounts `~/.cache/local-llm/flashinfer-autotune/<key>` writable at
`/flashinfer-autotune` and points `VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR` at it.
The key is the served model name and a hash of the head's image ID, the
checkpoint, the `vllm serve` arguments and the `--env` pairs: see `cache_key`.
Before the start the launcher checks that both nodes hold the same image
layers, creates the directory on both nodes, refuses one it cannot write, and
labels both containers with the key and, per node, whether a finished cache
file existed before the start (`found`) or not (`new`). `run.container_settings_from`
reads the labels into `env.autotune_cache`, and from there every row carries
it. `--no-autotune-cache` turns the cache off (label `off`): autotune then runs
at every start, as before. Serve arguments that turn autotune off
(`--no-enable-flashinfer-autotune`, `-O0`) get no cache and the label
`autotune-disabled`; pass them only when the model card does (#1011's E192).
`stop` gives the cache files back to the launching user (the container writes
them as root) before it removes the containers, so the user can delete one.
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

#: The FlashInfer autotune cache (#1514): host directory (under $HOME on each
#: node), container mount point, the vLLM variable that moves the cache, and
#: the container label prefix that run.container_settings_from reads.
AUTOTUNE_SUBDIR = ".cache/local-llm/flashinfer-autotune"
AUTOTUNE_MOUNT = "/flashinfer-autotune"
AUTOTUNE_ENV = "VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR"
AUTOTUNE_LABEL = "local-llm.autotune-cache"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
#: The only argv tokens the remote shell sees unquoted, so `~` expands there:
#: the two `-v` mounts. Any other `~/` token (in the serve args) stays quoted.
_MODELS_MOUNT = f"{MODELS_DIR}:/models:ro"
_CACHE_MOUNT = re.compile(
    re.escape(f"~/{AUTOTUNE_SUBDIR}/")
    + r"[A-Za-z0-9._-]+"
    + re.escape(f":{AUTOTUNE_MOUNT}")
)
#: Serve arguments that turn FlashInfer autotune off. Such a start tunes
#: nothing, so it gets no cache and says so in its label.
_AUTOTUNE_OFF_FLAGS = frozenset({"--no-enable-flashinfer-autotune", "-O0", "-O=0"})


@dataclasses.dataclass(frozen=True)
class AutotuneCache:
    """The kept autotune cache: its key, and per rank "found" or "new"."""

    key: str
    states: tuple[str, str]

    def mount(self) -> str:
        return f"~/{AUTOTUNE_SUBDIR}/{self.key}:{AUTOTUNE_MOUNT}"

    def labels(self) -> dict[str, str]:
        return {
            f"{AUTOTUNE_LABEL}.key": self.key,
            f"{AUTOTUNE_LABEL}.rank0": self.states[0],
            f"{AUTOTUNE_LABEL}.rank1": self.states[1],
        }


def served_name(vllm_args: list[str]) -> str | None:
    """The `--served-model-name` in the serve arguments, or None."""
    for i, arg in enumerate(vllm_args):
        if arg.startswith("--served-model-name="):
            return arg.split("=", 1)[1]
        if arg == "--served-model-name" and i + 1 < len(vllm_args):
            return vllm_args[i + 1]
    return None


def cache_key(
    *, image_id: str, model: str, vllm_args: list[str], extra_env: dict[str, str]
) -> str:
    """The autotune cache directory name for one launch (#1514).

    The image ID is in the key because vLLM drops FlashInfer's version and the
    GPU arch from the cache path once `VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR` is
    set; a cache from another image could hold another FlashInfer's tactics.
    The ID, not the tag, so a rebuilt image under the same tag tunes afresh.
    The checkpoint, the serve arguments and the `--env` pairs are in it too,
    so two arms never share kernel choices: vLLM's own config hash leaves out
    some variables that steer autotune (`VLLM_FLASHINFER_AUTOTUNE_SKIP_OPS`).
    The served name leads the key, so a person can read the directory list.
    """
    payload = json.dumps(
        {
            "image_id": image_id,
            "model": model,
            "vllm_args": vllm_args,
            "env": sorted(extra_env.items()),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    slug = _UNSAFE.sub("-", served_name(vllm_args) or model).strip("-.")[:48]
    return f"{slug or 'model'}-{digest}"


def autotune_disabled(vllm_args: list[str]) -> bool:
    """True if the serve arguments turn FlashInfer autotune off."""
    for i, arg in enumerate(vllm_args):
        if arg in _AUTOTUNE_OFF_FLAGS:
            return True
        if arg == "-O" and vllm_args[i + 1 : i + 2] == ["0"]:
            return True
    return False


def node_shell(rank: int, script: str) -> subprocess.CompletedProcess[str]:
    """Run a shell script on the head (rank 0) or on the worker (rank 1)."""
    argv = (
        ["bash", "-c", script]
        if rank == 0
        else ["ssh", "-o", "BatchMode=yes", WORKER_IP, script]
    )
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def head_image_id(image: str) -> str:
    """The image ID on the head, or SystemExit if the head does not have it.

    The head's ID only. The two nodes use different image stores: the head's
    overlay2 store reports the config digest as `.Id`, and the worker's
    containerd store reports the manifest digest. So one image has two IDs,
    and the worker's would split it into two keys. `check_same_image`
    compares the layers instead.
    """
    got = node_shell(
        0, f"docker image inspect --format '{{{{.Id}}}}' {shlex.quote(image)}"
    )
    image_id = got.stdout.strip()
    if got.returncode != 0 or not image_id:
        raise SystemExit(
            f"vllm_two_node: the head has no image {image!r}, so the autotune "
            "cache has no key (#1514). Load the image on both nodes first, or "
            "pass --no-autotune-cache."
        )
    return image_id


def image_layers(rank: int, image: str) -> str | None:
    """The image's `RootFS.Layers` on one node, or None if it is not there."""
    got = node_shell(
        rank,
        f"docker image inspect --format '{{{{json .RootFS.Layers}}}}' "
        f"{shlex.quote(image)}",
    )
    layers = got.stdout.strip()
    return layers if got.returncode == 0 and layers else None


def check_same_image(image: str) -> None:
    """Refuse (SystemExit) unless both nodes hold the same image layers.

    The IDs cannot be compared across the two image stores, but the layer
    digests can. When the images differ, FlashInfer's cache metadata differs:
    both nodes re-tune at every start, the worker's sparse-MLA cache load
    fails silently, and the labels still say `found`.
    """
    head, worker = image_layers(0, image), image_layers(1, image)
    if worker is None:
        raise SystemExit(
            f"vllm_two_node: the worker has no image {image!r} (#1514). Load "
            "it on the worker first, or pass --no-autotune-cache."
        )
    if head != worker:
        raise SystemExit(
            f"vllm_two_node: {image!r} has different layers on the head and "
            "the worker (#1514), so the two nodes would run different code "
            "and cannot share autotune results. Copy the head's image to the "
            "worker."
        )


#: Exit 3: mkdir failed. Exit 4: the directory is not writable. Otherwise it
#: prints how many finished cache files it holds; vLLM writes each one to a
#: dot-prefixed `.tmp` file first and renames it, so those are not counted.
_PREPARE = """d="$HOME/{subdir}/{key}"
mkdir -p "$d" 2>/dev/null || exit 3
test -w "$d" || exit 4
find "$d" -type f -name '*.json' | wc -l
"""


def prepare_cache(rank: int, key: str) -> str:
    """Create the cache directory on one node; "found" or "new" (#1514).

    Refuses (SystemExit) when the node cannot create it, cannot write it, or
    does not answer. Docker runs as root here, so the container can write
    anything the launching user can, and a read-only directory or filesystem
    fails both. "found" means a finished `*.json` existed before the start.
    It does not prove the start loads every shape: a partial file from a
    failed tune makes this start tune the missing shapes.
    """
    where = f"rank {rank} ({'head' if rank == 0 else WORKER_IP})"
    path = f"~/{AUTOTUNE_SUBDIR}/{key}"
    got = node_shell(rank, _PREPARE.format(subdir=AUTOTUNE_SUBDIR, key=key))
    if got.returncode == 3:
        raise SystemExit(f"vllm_two_node: {where} cannot create {path} (#1514)")
    if got.returncode == 4:
        raise SystemExit(f"vllm_two_node: {path} on {where} is not writable (#1514)")
    try:
        files = int(got.stdout.strip())
    except ValueError:
        files = -1
    if got.returncode != 0 or files < 0:
        raise SystemExit(
            f"vllm_two_node: cannot check {path} on {where} (exit "
            f"{got.returncode}): {got.stderr.strip()} (#1514)"
        )
    return "found" if files else "new"


def check_env(extra_env: dict[str, str], reserved: set[str]) -> None:
    """Refuse an `--env` key that is malformed, steers the fabric, or is ours."""
    for key in extra_env:
        if not ENV_KEY.fullmatch(key):
            raise ValueError(f"--env {key!r} is not a valid variable name")
        if key == AUTOTUNE_ENV:
            raise ValueError(
                f"--env {key}: the launcher sets it; use --no-autotune-cache "
                "to turn the cache off (#1514)"
            )
        if key in reserved or key.startswith(FABRIC_PREFIXES):
            raise ValueError(f"--env {key} would change the fabric setup")


def base_env(host_ip: str) -> dict[str, str]:
    """The environment the launcher sets on one node; `--env` may not touch it."""
    return {
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


def docker_run(
    *,
    rank: int,
    name: str,
    image: str,
    model: str,
    port: int,
    vllm_args: list[str],
    extra_env: dict[str, str] | None = None,
    autotune: AutotuneCache | None = None,
    autotune_mode: str = "off",
) -> list[str]:
    """The `docker run` argv for one node. Rank 0 serves the API.

    `autotune` None means no cache: no mount, and the label is
    `autotune_mode` ("off" or "autotune-disabled").
    """
    env = base_env(HEAD_IP if rank == 0 else WORKER_IP)
    check_env(extra_env or {}, set(env))
    env.update(extra_env or {})
    if autotune is not None:
        env[AUTOTUNE_ENV] = AUTOTUNE_MOUNT
    argv = [
        "docker", "run", "-d", "--name", f"{name}-rank{rank}",
        "--gpus", "all", "--network", "host", "--ipc", "host",
        "--cap-add", "SYS_NICE",
        "--ulimit", "memlock=-1", "--ulimit", "stack=67108864",
        "--device", "/dev/infiniband:/dev/infiniband",
        "-v", f"{MODELS_DIR}:/models:ro",
    ]  # fmt: skip
    if autotune is None:
        argv += ["--label", f"{AUTOTUNE_LABEL}={autotune_mode}"]
    else:
        argv += ["-v", autotune.mount()]
        for key, value in autotune.labels().items():
            argv += ["--label", f"{key}={value}"]
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
    """Quote an argv for a remote shell, keeping the `~/` mounts expandable.

    Only a `-v` value that is the models mount or a cache mount stays bare, so
    the shell on each node expands it to that node's home. Every other token
    is quoted, a `~/` path in the serve arguments too.
    """

    def bare(i: int, arg: str) -> bool:
        if i == 0 or argv[i - 1] != "-v":
            return False
        return arg == _MODELS_MOUNT or bool(_CACHE_MOUNT.fullmatch(arg))

    return " ".join(
        arg if bare(i, arg) else shlex.quote(arg) for i, arg in enumerate(argv)
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
    extra_env = parse_env(args.env)
    # Refuse a bad --env before a directory is made on either node.
    check_env(extra_env, set(base_env(HEAD_IP)))
    autotune = None
    mode = "off"
    if autotune_disabled(args.vllm_args):
        mode = "autotune-disabled"
        logger.info("the serve arguments turn autotune off: no autotune cache")
    elif not args.no_autotune_cache:
        check_same_image(args.image)
        key = cache_key(
            image_id=head_image_id(args.image),
            model=model,
            vllm_args=args.vllm_args,
            extra_env=extra_env,
        )
        states = (prepare_cache(0, key), prepare_cache(1, key))
        autotune = AutotuneCache(key=key, states=states)
        logger.info("autotune cache %s: head %s, worker %s", key, states[0], states[1])
    common = {
        "name": args.name,
        "image": args.image,
        "model": model,
        "port": args.port,
        "vllm_args": args.vllm_args,
        "extra_env": extra_env,
        "autotune": autotune,
        "autotune_mode": mode,
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


_OWNER = '"$(id -u):$(id -g)"'


def give_back_cache(rank: int, container: str) -> None:
    """Chown a container's autotune cache to the launching user (#1514).

    The container writes the cache as root, and the user then cannot delete
    it. A running container does the chown itself. A dead one cannot, so a
    throwaway container from the same image does it instead. A container with
    no cache label has no cache mount, and is left alone.
    """
    q = shlex.quote(container)
    info = node_shell(
        rank,
        f'docker inspect -f \'{{{{index .Config.Labels "{AUTOTUNE_LABEL}.key"}}}}'
        f"|{{{{.Config.Image}}}}' {q}",
    )
    key, _, image = info.stdout.strip().partition("|")
    if info.returncode != 0 or not key or key == "<no value>":
        return
    got = node_shell(rank, f"docker exec {q} chown -R {_OWNER} {AUTOTUNE_MOUNT}")
    if got.returncode == 0:
        return
    logger.warning(
        "rank %d: chown in %s failed (%s); trying a throwaway container",
        rank,
        container,
        got.stderr.strip() or f"exit {got.returncode}",
    )
    got = node_shell(
        rank,
        f'docker run --rm --entrypoint chown -v "$HOME/{AUTOTUNE_SUBDIR}/{key}:/c" '
        f"{shlex.quote(image)} -R {_OWNER} /c",
    )
    if got.returncode != 0:
        logger.error(
            "rank %d: cannot chown ~/%s/%s (%s); its files stay root-owned",
            rank,
            AUTOTUNE_SUBDIR,
            key,
            got.stderr.strip() or f"exit {got.returncode}",
        )


def stop(args: argparse.Namespace) -> int:
    for rank in (0, 1):
        container = f"{args.name}-rank{rank}"
        give_back_cache(rank, container)
        node_shell(rank, f"docker rm -f {shlex.quote(container)}")
        logger.info("removed %s on rank %d", container, rank)
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
    p_start.add_argument(
        "--no-autotune-cache",
        action="store_true",
        help="do not keep FlashInfer's autotune cache across starts (#1514); "
        "autotune then runs at every start, as before",
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
