#!/usr/bin/env python3
"""A fresh, transcript-capturing run of the `qwen38fnds4shim` cell for #112.

#112 is the tool-call degeneration loop: once a tool error enters the
conversation the model stops calling tools and narrates about the format,
emitting stacked bare `<tool_call>` opens with no function name. The shim
declines them (correctly), the turn ends empty, and the row reads
`solution_empty: true` with nothing recording what the model actually did.

The open question is a **context-poisoning cascade**: does the probability that
a turn's tool call is malformed rise with the number of tool errors already in
the conversation? The 2026-09-11 read-out established this is *not* testable on
the held ledger -- all 41 `qwen38fnds4shim` `solution_empty` rows have no
surviving transcript, so the turn-level evidence is gone.

This driver produces that missing evidence. It runs the cell exactly as the
262 existing rows were taken -- `ds4-metal` `ba01f5d`, the Q4 fast-pack, MTP
off, the tool-format shim on :8101 with no temperature pinning -- and captures
both the OpenCode transcript (`--client-log`) and the shim's own request and
response log into one directory. `scripts/degeneration_cascade.py` then reads
those transcripts and measures the conditional.

One arm, so there is no alternation to get wrong; the server is brought up once
and held for the whole batch (the cell is stable across a session, and a
restart between trials would itself reset the conversation state the cascade
hypothesis is about). The lock, the shim and the server are nested context
managers, released on every exit path.

    uv run python scripts/degeneration_cascade_run.py
    TRIALS=4 uv run python scripts/degeneration_cascade_run.py
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import pathlib
import socket
import sys
import time
from collections.abc import Iterator, Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import child
import ds4_server
import preflight
import unitctl

import logs

logger = logging.getLogger(__name__)

MODELS = pathlib.Path.home() / "models" / "qwen3.8-flash-next-ds4-q4"
DS4_MODEL = MODELS / (
    "Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
)
DS4_PLE = MODELS / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_TREE = pathlib.Path.home() / "git" / "ds4-metal"
KV_PLAIN = pathlib.Path.home() / ".ds4" / "server-kv"

MODEL_ID = "qwen3.8-flash-next-q4"
BACKEND = "qwen38fnds4shim"
SHIM_UNIT = "cascade-shim-8101"
SHIM_PORT = 8101
SERVER_PORT = 8000
LOCK_WHAT = "degeneration_cascade_run.py (#112)"


def port_answers(port: int, timeout: float = 1.0) -> bool:
    """Whether something is listening on `port`."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


@contextlib.contextmanager
def run_lock(owner_pid: int) -> Iterator[None]:
    """Hold the machine lock for the whole run, and release it on every exit."""
    taken, why = preflight.acquire_lock(LOCK_WHAT, pid=owner_pid)
    if not taken:
        raise RuntimeError(f"could not claim the machine: {why}")
    logger.info("machine lock held: %s", why)
    try:
        yield
    finally:
        released, why = preflight.release_lock(pid=owner_pid)
        logger.info("machine lock released=%s: %s", released, why)


@contextlib.contextmanager
def tool_shim(log: pathlib.Path, timeout: float = 20.0) -> Iterator[unitctl.Unit]:
    """The tool-format shim on :8101, with NO temperature pinning.

    This is the realistic cell #112 was found on: OpenCode sends its own
    temperature and the shim passes it through. It is *not* the greedy shim on
    :8102 (which pins temperature to 0 for the MTP arms); pinning would change
    the regime and the degeneration behaviour with it.
    """
    unit = unitctl.start(
        SHIM_UNIT,
        [
            "uv",
            "run",
            "python",
            "ds4_qwen_tool_shim.py",
            "--port",
            str(SHIM_PORT),
            "--upstream",
            f"http://127.0.0.1:{SERVER_PORT}",
        ],
        log=log,
        cwd=REPO,
    )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_answers(SHIM_PORT):
                break
            if unitctl.state(unitctl.read(SHIM_UNIT)) != unitctl.RUNNING:
                raise RuntimeError(f"the tool shim exited; see {log}")
            time.sleep(0.25)
        else:
            raise RuntimeError(f"the tool shim did not answer on :{SHIM_PORT}")
        logger.info("tool shim ready on :%d (pid %d)", SHIM_PORT, unit.pid)
        yield unit
    finally:
        unitctl.stop(SHIM_UNIT)


def server_command() -> list[str]:
    """The ds4-server argv: the Q4 fast-pack, MTP off."""
    KV_PLAIN.mkdir(parents=True, exist_ok=True)
    return ds4_server.argv(
        DS4_MODEL,
        DS4_PLE,
        KV_PLAIN,
        binary=DS4_TREE / "ds4-server",
        port=SERVER_PORT,
        mtp_model=None,
    )


def run_argv(
    logdir: pathlib.Path, batch: str, trials: int, server_log: pathlib.Path
) -> list[str]:
    """The `run.py` command line.

    `--no-lock` is load-bearing: this driver holds the machine lock for the
    whole run, so run.py must not try to claim it again against the driver's own
    claim. `--client-log` captures the OpenCode transcript into `logdir`, which
    is the whole point -- the 2026-09-11 read-out lost exactly this evidence.
    """
    return [
        "uv",
        "run",
        "python",
        "benchmarks/agent/run.py",
        "--backend",
        BACKEND,
        "--client",
        "opencode",
        "--trials",
        str(trials),
        "--no-lock",
        "--batch",
        batch,
        "--client-log",
        str(logdir / "transcripts"),
        "--server-log",
        str(server_log),
    ]


def sweep(trials: int, batch: str, logdir: pathlib.Path, owner_pid: int) -> int:
    """The whole run. Returns run.py's exit code."""
    logdir.mkdir(parents=True, exist_ok=True)
    (logdir / "transcripts").mkdir(parents=True, exist_ok=True)
    logger.info("logs in: %s", logdir)
    server_log = logdir / "ds4server.log"
    out = logdir / "run.log"

    with (
        run_lock(owner_pid),
        tool_shim(logdir / "shim-8101.log"),
        ds4_server.serving(
            server_command(),
            server_log,
            cwd=DS4_TREE,
            model_id=MODEL_ID,
            want_mtp=False,
            port=SERVER_PORT,
        ),
    ):
        rc = child.run(run_argv(logdir, batch, trials, server_log), cwd=REPO, log=out)
    logger.info("run finished rc=%d; log %s", rc, out)
    logger.info(
        "Next: uv run python scripts/degeneration_cascade.py %s -- measure "
        "P(malformed call | prior tool errors) from the captured transcripts.",
        logdir / "transcripts",
    )
    return rc


def main(argv: Sequence[str] | None = None) -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    default_logdir = pathlib.Path.home() / "bench-logs" / f"112-cascade-{stamp}"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trials", type=int, default=int(os.environ.get("TRIALS", "3")))
    p.add_argument("--batch", default=os.environ.get("BATCH", "112-cascade"))
    p.add_argument(
        "--logdir",
        type=pathlib.Path,
        default=pathlib.Path(os.environ.get("LOGDIR") or default_logdir),
    )
    args = p.parse_args(argv)

    logs.configure()
    try:
        return sweep(args.trials, args.batch, args.logdir, os.getpid())
    except (
        RuntimeError,
        ValueError,
        ds4_server.ServerNeverStarted,
        ds4_server.GraphMismatch,
        ds4_server.NotReady,
    ) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
