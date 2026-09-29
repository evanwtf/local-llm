"""Rerun #499's three stacks on macOS 27 once it has settled, in order. #834

#499 took the macOS 27 rows on first boot, while the upgrade's indexing
daemons used more than 3 CPU cores. The operator asked for a rerun once the
machine settled (2026-09-18). On 2026-09-29 the operator asked for it on
**fresh builds** of every engine ("Don't want to test stale code"), so this
follows always-latest: llama.cpp at ggml-org master, mlx-serve's latest
release, ds4 at antirez/ds4 main, and the current OpenCode. The rows carry
each engine's version, so the report can say which differences are the
engine's and which the machine's.

Each stack's servers start from the argv the first-boot rows recorded, with
only the build paths changed. run.py runs the same 15 tasks, and the servers
are stopped before the next stack.

Usage:

    uv run python scripts/macos_settled_rerun.py --dry-run     # print the plan
    uv run python scripts/macos_settled_rerun.py               # run all three
    uv run python scripts/macos_settled_rerun.py --only qwen38fnds4main
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import child

import logs

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parents[1]
HOME = pathlib.Path.home()
BENCH = HOME / ".local-llm-bench"

#: The 15 tasks of #499, from its dataset.json.
TASKS = (
    "mbox-quoting-both-halves",
    "mbox-scan",
    "mbox-strip-envelope",
    "parser-date",
    "parser-mbox-quoting",
    "parser-mbox-quoting-nodoc",
    "script-reverse",
    "script-transform",
    "storage-blob-put",
    "storage-put-and-sweep",
    "swift-chartaxis-spacing",
    "swift-csv-text",
    "swift-downsample-buckets",
    "swift-scaleladder-snap",
    "swift-sevensegment-glyphs",
)
TRIALS = 3

DS4_TREE = HOME / "git/ds4-mainline-0aaea5a2"
DS4_GGUF = DS4_TREE / "gguf/Qwen3.8-Flash-Next-Q4.gguf"


@dataclasses.dataclass(frozen=True)
class Unit:
    name: str
    argv: tuple[str, ...]
    port: int
    cwd: pathlib.Path = REPO
    # An HTTP path that returns 200 only once the model is loaded. None means
    # the port alone is the signal (the server loads before it listens).
    ready_path: str | None = None


@dataclasses.dataclass(frozen=True)
class Stack:
    backend: str
    units: tuple[Unit, ...]  # started in order, stopped in reverse
    env: dict[str, str] = dataclasses.field(default_factory=dict)  # for run.py


def _shim(script: str, name: str, port: int, upstream: str) -> Unit:
    return Unit(
        name,
        ("uv", "run", "python", script, "--upstream", upstream, "--port", str(port)),
        port,
    )


#: In #499's order. Server argv as the first-boot rows recorded it, with the
#: build paths moved to the fresh builds
#: (`env.server_argv`), or, for mlx-serve, as its log recorded the args.
PLAN = (
    Stack(
        "qwen38fnq3",
        (
            Unit(
                "834-llama-server",
                (
                    str(HOME / "git/llama.cpp/build/bin/llama-server"),
                    "-m",
                    str(
                        HOME / "models/Qwen3.8-Flash-Next-GGUF/UD-Q3_K_XL/"
                        "Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf"
                    ),
                    "-a",
                    "qwen3.8-flash-next-q3",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8020",
                    "-c",
                    "131072",
                    "-np",
                    "1",
                    "--metrics",
                    "--temp",
                    "1.0",
                    "--top-p",
                    "0.95",
                    "--top-k",
                    "20",
                    "--min-p",
                    "0.0",
                ),
                8020,
                # llama-server listens while it loads and answers 503 until
                # the model is in; the smoke gate hit that on 2026-09-29.
                ready_path="/health",
            ),
            # run.py's smoke gate speaks the Anthropic wire to base_url :11500.
            _shim(
                "ollama_claude_shim.py",
                "834-claude-shim",
                11500,
                "http://127.0.0.1:8020",
            ),
        ),
    ),
    Stack(
        "qwen38fnmlxserve",
        (
            Unit(
                "834-mlx-serve",
                (
                    str(
                        HOME / ".local/opt/mlx-serve-26.9.6/mlx-serve-macos-arm64/"
                        "mlx-serve"
                    ),
                    "--model",
                    str(
                        HOME / ".mlx-serve/models/ddalcu/"
                        "Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
                    ),
                    "--serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "11234",
                    "--ctx-size",
                    "100000",
                    "--kv-quant",
                    "off",
                ),
                11234,
            ),
        ),
    ),
    Stack(
        "qwen38fnds4main",
        (
            Unit(
                "834-ds4-server",
                (
                    "./ds4-server",
                    "--metal",
                    "-m",
                    str(DS4_GGUF),
                    "--ctx",
                    "100000",
                    "--warm-weights",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ),
                8000,
                DS4_TREE,
            ),
            _shim(
                "ds4_qwen_tool_shim.py", "834-qwen-shim", 8101, "http://127.0.0.1:8000"
            ),
        ),
        # run.py's #149 route gate reads these, not the backend's engine_tree.
        # Without them it checked ~/git/ds4-metal and refused as "stale".
        env={"DS4_TREE": str(DS4_TREE), "DS4_TEST_MODEL": str(DS4_GGUF)},
    ),
)


def run_argv(stack: Stack, batch: str) -> list[str]:
    """run.py for one stack: #499's tasks and trials, no early stop."""
    argv = [
        "uv",
        "run",
        "python",
        "benchmarks/agent/run.py",
        "--backend",
        stack.backend,
        "--client",
        "opencode",
        "--trials",
        str(TRIALS),
        "--no-early-stop",
        "--batch",
        batch,
    ]
    for task in TASKS:
        argv += ["--task", task]
    return argv


def listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def unitctl(*args: str) -> None:
    subprocess.run(
        ["uv", "run", "python", "scripts/unitctl.py", *args], cwd=REPO, check=True
    )


def answering(port: int, path: str | None) -> bool:
    """True once `path` returns 200, or at once when there is no path."""
    if path is None:
        return True
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return bool(r.status == 200)
    except (urllib.error.URLError, OSError):
        return False


def start(unit: Unit, stamp: str, ready_timeout: float = 900.0) -> None:
    if listening(unit.port):
        raise SystemExit(
            f"port {unit.port} is already in use; refusing to start {unit.name}"
        )
    log = BENCH / "logs" / f"{unit.name}-{stamp}.log"
    unitctl(
        "start", unit.name, "--log", str(log), "--cwd", str(unit.cwd), "--", *unit.argv
    )
    deadline = time.monotonic() + ready_timeout
    while not (listening(unit.port) and answering(unit.port, unit.ready_path)):
        if time.monotonic() > deadline:
            raise SystemExit(
                f"{unit.name} did not listen on :{unit.port} within {ready_timeout:.0f} s; see {log}"
            )
        time.sleep(5)
    logger.info("%s listening on :%d (log %s)", unit.name, unit.port, log)


def stop(unit: Unit) -> None:
    unitctl("stop", unit.name, "--timeout", "60")


def run_stack(stack: Stack, stamp: str) -> int:
    started: list[Unit] = []
    try:
        for unit in stack.units:
            start(unit, stamp)
            started.append(unit)
        batch = f"0929-834-{stack.backend}"
        log = BENCH / "logs" / f"834-{stack.backend}-{stamp}.log"
        logger.info("run.py for %s, batch %s, log %s", stack.backend, batch, log)
        code = child.run(run_argv(stack, batch), cwd=REPO, log=log, env=stack.env)
        logger.info("run.py for %s exited %d", stack.backend, code)
        return code
    finally:
        for unit in reversed(started):
            stop(unit)


def main() -> int:
    logs.configure()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan and exit"
    )
    parser.add_argument(
        "--only", action="append", default=[], help="run only this backend (repeatable)"
    )
    args = parser.parse_args()
    plan = [s for s in PLAN if not args.only or s.backend in args.only]
    for stack in plan:
        for unit in stack.units:
            logger.info(
                "plan %s: %s (cwd %s): %s",
                stack.backend,
                unit.name,
                unit.cwd,
                " ".join(unit.argv),
            )
        logger.info(
            "plan %s: %s",
            stack.backend,
            " ".join(run_argv(stack, "0929-834-" + stack.backend)),
        )
    if args.dry_run:
        return 0
    stamp = time.strftime("%Y%m%dT%H%M%S")
    failed = [s.backend for s in plan if run_stack(s, stamp) != 0]
    if failed:
        logger.error("run.py failed for %s", ", ".join(failed))
        return 1
    logger.info("all %d stacks finished", len(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
