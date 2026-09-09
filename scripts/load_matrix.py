"""Load each of a set of gguf files with the PLE sidecar, serially, and
record the resident set. #158

The #991 PR head declares `qwen4-exp` / `qwen4-exp-ple` (hyphenated). The
question this answers is which of our weight files that build loads, and how
much memory each leaves resident under our 112 GiB Metal ceiling. The four
files we hold straddle the architecture split: the Q4KExperts and
Q40RoutedExperts files declare `qwen4-exp`, the imatrix file declares
`qwen4exp` (#155's wall), and the MTP and vision files are auxiliary.

Each load is a full ds4-server start: the model and PLE sidecar are read off
disk into memory, the server is probed with a real completion (the only honest
ready signal -- /health answers ok before the weights are resident), the
resident set of the server and its descendants is read, and the server is
killed before the next load. Never two resident: each load is torn down before
the next begins.

Usage:

    uv run python scripts/load_matrix.py --tree ~/git/ds4-pr991 [--out OUT.jsonl]

The four model files and the PLE sidecar are the defaults; pass --model to
override the list.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import time

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)
import memcap
import preflight
import wait_ready

# The agent identity comes from the environment, never from introspection.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib import agent_identity

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

MODELS = pathlib.Path.home() / "models" / "qwen3.8-flash-next-ds4-q4"
IMATRIX = pathlib.Path.home() / "models" / "qwen3.8-flash-next-ds4-q4k-imatrix"
PLE = MODELS / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf"

DEFAULT_MODELS = [
    MODELS
    / "Qwen3.8-Flash-Next-Q40RoutedExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf",
    IMATRIX
    / "Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf",
    MODELS / "qwen3.8-flash-next-q4-mtp.gguf",
    MODELS / "qwen3.8-flash-next-q4-vision.gguf",
]

PORT = 8000
CTX = 100000
READY_TIMEOUT = 600
LOAD_SETTLE = 5.0


def _tree_rss_gib(pid: int) -> float:
    """Resident set of `pid` and every descendant, in GiB."""
    table = memcap._rss_kib_by_pid()
    return memcap.tree_rss_gib(pid, table)


def _server_command(tree: pathlib.Path, model: pathlib.Path, port: int) -> list[str]:
    return [
        str(tree / "ds4-server"),
        "--metal",
        "-m",
        str(model),
        "--ple",
        str(PLE),
        "--ctx",
        str(CTX),
        "--warm-weights",
        "--kv-disk-dir",
        str(pathlib.Path.home() / ".ds4" / "load-matrix-kv"),
        "--kv-disk-space-mb",
        "8192",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def _tree_rev(tree: pathlib.Path) -> str:
    """The git rev of `tree`, or 'unknown' when it is not a git checkout."""
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tree,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"
    return rev or "unknown"


def _port_free(port: int) -> bool:
    """Whether nothing is listening on `port`."""
    import contextlib
    import socket

    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def load_one(tree: pathlib.Path, model: pathlib.Path, port: int) -> dict:
    """Load one model with the PLE sidecar, record resident GiB, tear down.

    Returns a row: {model, size_gib, resident_gib, ready, error}. `ready` is
    whether a real completion succeeded; `error` is the failure text when the
    load did not come up.
    """
    if not _port_free(port):
        return {
            "model": str(model),
            "size_gib": round(model.stat().st_size / 2**30, 1),
            "resident_gib": None,
            "ready": False,
            "error": f"port {port} is already held",
        }
    log = pathlib.Path.home() / "bench-logs" / f"load-matrix-{model.stem}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as sink:
        server = subprocess.Popen(
            _server_command(tree, model, port),
            cwd=tree,
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        # Poll the server process alongside the ready probe. A model the build
        # refuses (a metadata or architecture mismatch) exits in seconds; the
        # ready probe alone would poll for the full timeout against a dead
        # port. The first of the two to finish decides.
        deadline = time.monotonic() + READY_TIMEOUT
        ready = False
        while time.monotonic() < deadline:
            if server.poll() is not None:
                break
            if wait_ready.serves(
                f"http://127.0.0.1:{port}", model="qwen3.8-flash-next-q4", token="local"
            )[0]:
                ready = True
                break
            time.sleep(2)
        if server.poll() is not None:
            text = log.read_text(errors="replace")
            tail = "\n".join(text.splitlines()[-15:])
            return {
                "model": str(model),
                "size_gib": round(model.stat().st_size / 2**30, 1),
                "resident_gib": None,
                "ready": False,
                "error": f"server exited rc={server.returncode}; log tail:\n{tail}",
            }
        if not ready:
            text = log.read_text(errors="replace")
            tail = "\n".join(text.splitlines()[-15:])
            return {
                "model": str(model),
                "size_gib": round(model.stat().st_size / 2**30, 1),
                "resident_gib": None,
                "ready": False,
                "error": f"not ready in {READY_TIMEOUT}s; log tail:\n{tail}",
            }
        # Let the resident set settle after the ready probe.
        time.sleep(LOAD_SETTLE)
        resident = _tree_rss_gib(server.pid)
        return {
            "model": str(model),
            "size_gib": round(model.stat().st_size / 2**30, 1),
            "resident_gib": round(resident, 1),
            "ready": True,
            "error": None,
        }
    finally:
        if server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=pathlib.Path, required=True)
    parser.add_argument("--model", type=pathlib.Path, action="append", default=None)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args(argv)

    tree = args.tree
    server = tree / "ds4-server"
    if not os.access(server, os.X_OK):
        logger.error("no runnable ds4-server at %s -- build it first", server)
        return 1
    models = args.model or DEFAULT_MODELS
    for model in models:
        if not model.is_file():
            logger.error("missing model file: %s", model)
            return 1
    if not PLE.is_file():
        logger.error("missing PLE sidecar: %s", PLE)
        return 1

    # A load leaves ~100 GiB resident; refuse to run without the machine lock
    # rather than relying on the operator to remember (#160, peer review).
    if not agent_identity.is_identified():
        logger.error(
            "REFUSING to claim the machine: agent identity is %s -- set %s, %s, %s",
            agent_identity.log_label(),
            agent_identity.AGENT_VAR,
            agent_identity.MODEL_VAR,
            agent_identity.EFFORT_VAR,
        )
        return 2
    agent, model, effort = agent_identity.identity()
    ok, why = preflight.acquire_lock(
        f"load-matrix {tree.name}",
        path=preflight.LOCK_PATH,
        agent=agent,
        agent_model=model,
        agent_effort=effort,
    )
    if not ok:
        logger.error("cannot claim the machine: %s", why)
        return 1
    logger.info("machine claimed: %s", why)

    # The tree rev and a timestamp give each row a build identity, so an
    # accumulating jsonl can still answer which build loaded which file
    # (#160, peer review).
    tree_rev = _tree_rev(tree)
    rows = []
    for i, model in enumerate(models):
        logger.info("load %d/%d: %s", i + 1, len(models), model.name)
        row = load_one(tree, model, args.port)
        row["tree_rev"] = tree_rev
        row["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        rows.append(row)
        if row["ready"]:
            logger.info(
                "  resident %s GiB (model %s GiB)", row["resident_gib"], row["size_gib"]
            )
        else:
            logger.error("  load failed: %s", row["error"])

    out = args.out or pathlib.Path.home() / "bench-logs" / "load-matrix.jsonl"
    with out.open("a") as sink:
        for row in rows:
            sink.write(json.dumps(row) + "\n")
    logger.info("wrote %d row(s) to %s", len(rows), out)

    ok, why = preflight.release_lock(path=preflight.LOCK_PATH)
    if not ok:
        logger.error("cannot release the machine: %s", why)
        return 1
    logger.info("machine released: %s", why)
    return 0


if __name__ == "__main__":
    logs.configure()
    raise SystemExit(main())
