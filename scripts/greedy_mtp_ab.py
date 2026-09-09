#!/usr/bin/env python3
"""The first ds4 MTP arm that can actually draft, against its own control.

#151, #39. Port of `greedy_mtp_ab.sh` (stage 4 of #235).

Every MTP row this project published before 2026-09-08 was taken on an arm that
never speculated. ds4 reaches its Qwen MTP path only at `temperature <= 0.0f`
(`ds4.c:80120 at ds4-metal ba01f5d`); above it the speculative call does one
plain eval and returns (`ds4.c:80216 at ds4-metal ba01f5d`). A request with no
temperature gets `DS4_DEFAULT_TEMPERATURE`, `1.0f`
(`ds4.h:56 at ds4-metal ba01f5d`), and OpenCode's config declares
`"temperature": false` for this model, so it sends none.

**Two arms, not one.** Pinning the temperature is itself a change of regime, so
a greedy MTP arm alone cannot separate speculation from greedy decoding:

    qwen38fnds4mtp7greedy   temperature 0, MTP on
    qwen38fnds4greedy       temperature 0, MTP off   <- what greedy costs alone

Both talk to a second shim on :8102 with `SHIM_TEMPERATURE=0`. :8101 is left
exactly as it is, so the 262 existing rows keep comparing.

The server restarts between arms because the MTP and non-MTP KV formats are
incompatible and ds4 rejects the other's checkpoints. A shared directory leaves
one arm re-prefilling, and the only symptom is that it looks slower.

    uv run python scripts/greedy_mtp_ab.py
    TRIALS=2 ROUNDS=2 uv run python scripts/greedy_mtp_ab.py

## What the shell version could not do, and this must

The shell driver failed on 2026-09-08 in a way that cost an arm: an empty
`mtp_args` array is an *unbound variable* under `set -u` on bash 3.2, and only
the control arm's array is empty. The treatment arm ran all 15 tasks and its
pair never launched. Here the flags are a list, and a list that is sometimes
empty is not a special case.

Teardown is the other half. The shell chained EXIT traps by parsing `trap -p`
with sed, because a second bare trap would discard the one releasing the run
lock. Here the lock, the shim and each server are nested context managers, and
the order cannot be got wrong.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import logging
import os
import pathlib
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import ds4_server
import preflight
import unitctl

logger = logging.getLogger(__name__)

MODELS = pathlib.Path.home() / "models" / "qwen3.8-flash-next-ds4-q4"
DS4_MODEL = MODELS / (
    "Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
)
DS4_PLE = MODELS / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_MTP = MODELS / "qwen3.8-flash-next-q4-mtp.gguf"
DS4_TREE = pathlib.Path.home() / "git" / "ds4-metal"
KV_MTP = pathlib.Path.home() / ".ds4" / "server-kv-mtp"
KV_PLAIN = pathlib.Path.home() / ".ds4" / "server-kv"

MODEL_ID = "qwen3.8-flash-next-q4"
SHIM_UNIT = "greedy-shim-8102"
SHIM_PORT = 8102
SERVER_PORT = 8000
LOCK_WHAT = "greedy_mtp_ab.py (#151/#39)"

TREATMENT = "qwen38fnds4mtp7greedy"
CONTROL = "qwen38fnds4greedy"


def arm_order(round_number: int) -> list[tuple[str, str]]:
    """(kind, backend) for one round, alternating which arm goes first.

    Whichever arm runs first is faster in 9 of 12 reps, median +0.9% and +5.9%
    on the first rep of a cold session (#130, #201). Alternation cancels that
    only over an even number of rounds, which `main` refuses to skip.
    """
    pair = [("mtp", TREATMENT), ("plain", CONTROL)]
    return pair if round_number % 2 == 1 else list(reversed(pair))


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
def greedy_shim(log: pathlib.Path, timeout: float = 20.0) -> Iterator[unitctl.Unit]:
    """The temperature-pinning shim on its own port, stopped on every exit.

    A leftover shim pinning temperature would silently make a later run greedy,
    which is why the shell stopped it from an EXIT trap. The shell also found
    it with `pkill -f 'qwen_tool_shim.py --port 8102'`; this records the pid.

    Readiness is a real connection, not `sleep 3`. The shell slept and then
    checked that a process existed, which is true of a process that is about to
    fail to bind.
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
        env={"SHIM_TEMPERATURE": "0"},
    )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_answers(SHIM_PORT):
                break
            if unitctl.state(unitctl.read(SHIM_UNIT)) != unitctl.RUNNING:
                raise RuntimeError(f"the greedy shim exited; see {log}")
            time.sleep(0.25)
        else:
            raise RuntimeError(f"the greedy shim did not answer on :{SHIM_PORT}")
        logger.info("greedy shim ready on :%d (pid %d)", SHIM_PORT, unit.pid)
        yield unit
    finally:
        unitctl.stop(SHIM_UNIT)


def server_command(want_mtp: bool, kv_dir: pathlib.Path) -> list[str]:
    """The ds4-server argv for one arm."""
    return ds4_server.argv(
        DS4_MODEL,
        DS4_PLE,
        kv_dir,
        binary=DS4_TREE / "ds4-server",
        port=SERVER_PORT,
        mtp_model=DS4_MTP if want_mtp else None,
        mtp_draft=7 if want_mtp else None,
        mtp_timing=want_mtp,
    )


def arm_argv(
    backend: str, tag: str, logdir: pathlib.Path, batch: str, trials: int
) -> list[str]:
    """The `run.py` command line for one arm.

    `--no-lock` is load-bearing. This driver holds the machine lock for the
    whole run; without it every arm would try to claim it again and refuse
    against the driver's own claim.
    """
    return [
        "uv",
        "run",
        "python",
        "benchmarks/agent/run.py",
        "--backend",
        backend,
        "--client",
        "opencode",
        "--trials",
        str(trials),
        "--no-lock",
        "--batch",
        batch,
        "--server-log",
        str(logdir / f"ds4server-{tag}.log"),
        "--draft-log-engine",
        "ds4",
    ]


def run_arm(
    backend: str, tag: str, logdir: pathlib.Path, batch: str, trials: int
) -> int:
    """One arm's sweep. Returns run.py's exit code."""
    out = logdir / f"run-{tag}.log"
    argv = arm_argv(backend, tag, logdir, batch, trials)
    with out.open("wb") as handle:
        done = subprocess.run(
            argv, cwd=REPO, stdout=handle, stderr=subprocess.STDOUT, check=False
        )
    logger.info("arm %s finished rc=%d; log %s", tag, done.returncode, out)
    return done.returncode


def sweep(
    rounds: int, trials: int, batch: str, logdir: pathlib.Path, owner_pid: int
) -> int:
    """The whole run. Returns a process exit code: 0 only if every arm ran.

    A failed arm does not stop the sweep -- the remaining arms are still worth
    having -- but it must not be reported as a clean run either. The shell
    piped each arm through `tee` and lost the exit status to the pipe, so a
    driver that lost an arm exited 0 and the loss surfaced hours later at
    read-out. That is the same shape as the failure that opened #235.
    """
    logdir.mkdir(parents=True, exist_ok=True)
    logger.info("logs in: %s", logdir)
    failed: list[str] = []
    with run_lock(owner_pid), greedy_shim(logdir / "shim-8102.log"):
        for round_number in range(1, rounds + 1):
            logger.info("=== round %d of %d ===", round_number, rounds)
            for kind, backend in arm_order(round_number):
                tag = f"r{round_number}-{kind}"
                want_mtp = kind == "mtp"
                kv_dir = KV_MTP if want_mtp else KV_PLAIN
                kv_dir.mkdir(parents=True, exist_ok=True)
                with ds4_server.serving(
                    server_command(want_mtp, kv_dir),
                    logdir / f"ds4server-{tag}.log",
                    cwd=DS4_TREE,
                    model_id=MODEL_ID,
                    want_mtp=want_mtp,
                    port=SERVER_PORT,
                ):
                    logger.info("round %d arm %s (%s)", round_number, kind, backend)
                    if run_arm(backend, tag, logdir, batch, trials) != 0:
                        failed.append(tag)
    logger.info("complete -- %s", logdir)
    logger.info(
        "Read the MTP arm's rows for drafting_share before reading any wall "
        "time: an arm that emitted no cycle is not an MTP arm, whatever it "
        "declared."
    )
    if failed:
        logger.error(
            "INCOMPLETE: %d of %d arms failed (%s). The rows that exist are "
            "still rows, but this is not a paired run -- read it as such.",
            len(failed),
            rounds * 2,
            ", ".join(failed),
        )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    # Local time, and aware: the log directory is read by a person against a
    # wall clock, and `docs/` records times in New York.
    stamp = datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    default_logdir = pathlib.Path.home() / "bench-logs" / f"greedy-mtp-ab-{stamp}"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rounds", type=int, default=int(os.environ.get("ROUNDS", "2")))
    p.add_argument("--trials", type=int, default=int(os.environ.get("TRIALS", "1")))
    p.add_argument("--batch", default=os.environ.get("BATCH", "greedy-mtp-ab"))
    p.add_argument(
        "--logdir",
        type=pathlib.Path,
        default=pathlib.Path(os.environ.get("LOGDIR") or default_logdir),
    )
    p.add_argument(
        "--allow-odd-rounds",
        action="store_true",
        default=os.environ.get("ALLOW_ODD_ROUNDS") == "1",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.rounds % 2 and not args.allow_odd_rounds:
        logger.error(
            "REFUSING: rounds=%d is odd. Alternation cancels position bias only "
            "on an even count (#130, #201). --allow-odd-rounds overrides.",
            args.rounds,
        )
        return 2

    try:
        return sweep(args.rounds, args.trials, args.batch, args.logdir, os.getpid())
    except (
        RuntimeError,
        ds4_server.ServerNeverStarted,
        ds4_server.GraphMismatch,
        ds4_server.NotReady,
    ) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
