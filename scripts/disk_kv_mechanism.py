#!/usr/bin/env python3
"""Is the disk-KV budget what makes arm A decline by trial 3? #112

Port of `scripts/disk_kv_mechanism_test.sh` (#235).

The name drops `_test`. `testpaths = ["."]`, so pytest collects every
`*_test.py` in the tree: as `disk_kv_mechanism_test.py` this script was
collected, its `test()` entry point was read as a test function, and the suite
errored with `fixture 'trials' not found`. A measurement script is not a test,
and a filename that says otherwise will keep being believed.

Arm A without restart-between-trials was 13/13/10 (36/45); with restart it is
14/14/14 (42/45). Server state is the confirmed cause; **which piece** is not.
The leading hypothesis is the disk KV budget: `--kv-disk-space-mb 8192` is
sized for DeepSeek (~560 MiB entries) and Qwen's entries are larger, so the
default may evict every turn and re-prefill.

This raises the budget to 32768 and re-runs arm A on a **single continuous
server**, no restart between trials. If the trial-3 decline reappears the
effect is elsewhere; if it does not, disk KV is confirmed and the fix is one
flag.

Prerequisites, all refused rather than assumed:

* the `qwen38fnds4shim` backend on ds4-metal, serving `qwen3.8-flash-next-q4`;
* the tool-format shim already on :8101 -- this script neither starts nor stops
  it, because it belongs to whatever else is using the machine;
* clean checkouts of `~/git/gmail-archive` and `~/git/monitor` at their pinned
  commits, which `run.py` refuses without and which is the guard relied on
  here.

    uv run python scripts/disk_kv_mechanism.py

One `run.py` invocation, `--trials 3`, ~90 minutes.

## What the port changes

The shim check was `pgrep -f qwen_tool_shim`, which says a process exists
somewhere and nothing about :8101. `tool_shim.require_running` finds the port's
holder and confirms what it is -- a bare ds4-server on :8101 answers a
connection and strips nothing.

The server teardown was an EXIT trap (`ds4_arm_stop_trap`), added for #145
after this script left a server resident. `ds4_server.serving()` is a context
manager, so the stop happens on the exception path too.
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import child
import ds4_server
import tool_shim

import logs

logger = logging.getLogger(__name__)

MODELS = pathlib.Path.home() / "models" / "qwen3.8-flash-next-ds4-q4"
DS4_MODEL = MODELS / (
    "Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
)
DS4_PLE = MODELS / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_KV = pathlib.Path.home() / ".ds4" / "server-kv"
DS4_TREE = pathlib.Path.home() / "git" / "ds4-metal"

BACKEND = "qwen38fnds4shim"
MODEL_ID = "qwen3.8-flash-next-q4"
SERVER_PORT = 8000
SHIM_PORT = 8101
COLLECT_INTO = "112-kv32768"

#: The whole experiment. 8192 is the DeepSeek-sized default this doubles-plus.
KV_MB = 32768

BASELINES = (
    "  no restart, kv 8192:  13/13/10 (36/45)",
    "  restart,    kv 8192:  14/14/14 (42/45)",
    "  no restart, kv 32768: <this run>",
)


def server_command(kv_mb: int = KV_MB) -> list[str]:
    """One continuous server for all three trials, with the raised budget."""
    return ds4_server.argv(
        DS4_MODEL,
        DS4_PLE,
        DS4_KV,
        binary=DS4_TREE / "ds4-server",
        port=SERVER_PORT,
        kv_disk_mb=kv_mb,
    )


def run_argv(trials: int) -> list[str]:
    """`run.py` for arm A.

    No `--no-lock`: this script does not hold the machine lock itself, so
    `run.py` takes it. That is the shell's behaviour and the right one for a
    single-invocation test -- there is nothing here for a second claim to
    protect against.
    """
    return [
        "uv",
        "run",
        "python",
        "benchmarks/agent/run.py",
        "--backend",
        BACKEND,
        "--trials",
        str(trials),
        "--client",
        "opencode",
    ]


def collect(bench_logs: pathlib.Path, since: float) -> int:
    """Move this run's transcripts under `112-kv32768/`. Returns the count.

    Filtered to files written after this run started. A run killed before its
    own move leaves transcripts behind, and the next run of the same arm claims
    them: `old-sweep1` once held 22 transcripts for a 15-task sweep.
    """
    destination = bench_logs / COLLECT_INTO
    destination.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in sorted(bench_logs.glob(f"*{BACKEND}-opencode-*")):
        if path.is_dir() or path.stat().st_mtime <= since:
            continue
        shutil.move(str(path), str(destination / path.name))
        moved += 1
    return moved


def measure(
    trials: int, kv_mb: int, logdir: pathlib.Path, bench_logs: pathlib.Path
) -> int:
    logdir.mkdir(parents=True, exist_ok=True)
    logger.info("logs in: %s", logdir)
    logger.info("%s", tool_shim.require_running(SHIM_PORT))
    since = time.time()

    log = logdir / f"armA-kv{kv_mb}.log"
    with ds4_server.serving(
        server_command(kv_mb),
        logdir / "ds4server.log",
        cwd=DS4_TREE,
        model_id=MODEL_ID,
        want_mtp=False,
        port=SERVER_PORT,
    ):
        logger.info(
            "starting arm A, %d trials, single continuous server, kv-disk-space-mb=%d",
            trials,
            kv_mb,
        )
        # child.run, not subprocess.run: run.py re-spawns `opencode`, and a
        # signal to this driver reaches neither (#268).
        rc = child.run(run_argv(trials), cwd=REPO, log=log)
    logger.info("run done rc=%d; log %s", rc, log)
    logger.info(
        "collected %d transcripts to %s/", collect(bench_logs, since), COLLECT_INTO
    )
    logger.info("Compare per-trial pass rates against the arm A baselines:")
    for line in BASELINES:
        logger.info("%s", line)
    return rc


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--kv-disk-space-mb", type=int, default=KV_MB)
    p.add_argument("--logdir", type=pathlib.Path, default=None)
    args = p.parse_args(argv)

    logs.configure()
    logdir = args.logdir or pathlib.Path(tempfile.mkdtemp(prefix="disk-kv-"))
    bench_logs = pathlib.Path(
        os.environ.get("BENCH_LOGS") or pathlib.Path.home() / "bench-logs"
    )
    try:
        return measure(args.trials, args.kv_disk_space_mb, logdir, bench_logs)
    except (
        tool_shim.NotServing,
        ds4_server.ForeignServer,
        ds4_server.ServerNeverStarted,
        ds4_server.GraphMismatch,
        ds4_server.NotReady,
    ) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
