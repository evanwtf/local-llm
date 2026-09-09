"""Restart-between-trials: does server state degrade a session? #112, #77.

Replaces **two** shell scripts, `restart_between_trials.sh` and
`restart_between_trials_armB.sh`. Both stay until a run agrees with this.

Both arms of #77 degrade monotonically across a session -- A 13/13/10 and B
10/9/6 -- with a fresh conversation each trial. That rules out model context.
The candidates are server-side state and machine state, and restarting the
server between trials separates them.

    uv run python scripts/restart_between_trials.py A
    uv run python scripts/restart_between_trials.py B

## Why one module and not two scripts

The two shell scripts differ by **an arm**: MTP flags, a KV directory, a
backend name, and a log prefix. Everything else -- the shim refusal, the lock
held across the whole cycle, the restart, the transcript collection, the KV
prefix audit -- is the same code written twice, and it has already drifted
once. From `stack_agent_ab.sh`, about a different pair:

> the repo has already paid for two lists that were supposed to be the same set

Arm B's own comment records what the drift cost:

> Until 2026-09-06 this script started a server with `--mtp-timing` and then
> never passed `--server-log`, so every counter the engine emitted was written
> to a file nothing read: the arm ran for three cycles asserting only that the
> flag had been passed.

Arm A had no counters to pass, so the bug could only exist in the copy -- and
did, for three cycles. Two arms of one experiment sharing one code path cannot
have that bug in one of them.

## The KV directories are not interchangeable

ds4 rejects the other configuration's checkpoints when an engine flag changes
the KV format, so arm B must use its own directory. A shared one makes one arm
re-prefill where the other hit cache, and the only symptom is that it looks
slower -- which is the quantity being measured.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import logging
import os
import pathlib
import socket
import subprocess
import sys
from collections.abc import Iterator, Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import ab_driver
import ds4_server
import preflight

import logs

logger = logging.getLogger(__name__)

MODELS = pathlib.Path.home() / "models" / "qwen3.8-flash-next-ds4-q4"
DS4_MODEL = MODELS / (
    "Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
)
DS4_PLE = MODELS / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_MTP = MODELS / "qwen3.8-flash-next-q4-mtp.gguf"
DS4_TREE = pathlib.Path.home() / "git" / "ds4-metal"

SHIM_PORT = 8101
SERVER_PORT = 8000
MODEL_ID = "qwen3.8-flash-next-q4"
TRIALS = 3


class Refusal(RuntimeError):
    """A reason not to start."""


@dataclasses.dataclass(frozen=True)
class ArmSpec:
    """What distinguishes the two arms, and nothing else."""

    name: str
    backend: str
    kv: pathlib.Path
    want_mtp: bool
    run_dir: str
    baseline: str


ARMS = {
    "A": ArmSpec(
        name="A",
        backend="qwen38fnds4shim",
        kv=pathlib.Path.home() / ".ds4" / "server-kv",
        want_mtp=False,
        run_dir="112-run",
        baseline="13/13/10 (the original run, no restarts between trials)",
    ),
    "B": ArmSpec(
        name="B",
        backend="qwen38fnds4mtp7shim",
        # Not interchangeable with arm A's: ds4 rejects the other
        # configuration's checkpoints when a flag changes the KV format.
        kv=pathlib.Path.home() / ".ds4" / "server-kv-mtp",
        want_mtp=True,
        # The shell's name, not a tidier one. ~/bench-logs already holds
        # 77-armB-run{1,2,3} from the shell's runs, and arm A's "112-run"
        # matches its shell exactly -- so "77-armB-restart-run" was a slip,
        # and it would have split one experiment's transcripts across two
        # directory families with nothing recording that they are the same
        # experiment. Nothing parses the name; the cost is provenance, which
        # is the cost that shows up months later.
        run_dir="77-armB-run",
        baseline="10/9/6 on one continuous server; arm A under restart is 42/45 (14/14/14)",
    ),
}


def port_answers(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def server_command(arm: ArmSpec) -> list[str]:
    """This arm's ds4-server argv."""
    return ds4_server.argv(
        DS4_MODEL,
        DS4_PLE,
        arm.kv,
        binary=DS4_TREE / "ds4-server",
        port=SERVER_PORT,
        mtp_model=DS4_MTP if arm.want_mtp else None,
        mtp_draft=7 if arm.want_mtp else None,
        mtp_timing=arm.want_mtp,
    )


def run_argv(arm: ArmSpec, server_log: pathlib.Path) -> list[str]:
    """The `run.py` command line for one trial.

    `--server-log` is here for **both** arms, not only the speculative one.
    Arm B's shell copy started a server with `--mtp-timing` and never passed
    `--server-log`, so every counter the engine emitted went to a file nothing
    read -- three cycles asserting only that the flag had been passed. A single
    code path cannot have that bug in one arm.
    """
    argv = [
        "uv",
        "run",
        "python",
        "benchmarks/agent/run.py",
        "--backend",
        arm.backend,
        "--trials",
        "1",
        "--client",
        "opencode",
        "--no-lock",
        "--server-log",
        str(server_log),
    ]
    if arm.want_mtp:
        argv += ["--draft-log-engine", "ds4"]
    return argv


@contextlib.contextmanager
def run_lock(what: str, owner_pid: int) -> Iterator[None]:
    """Hold the machine for the whole cycle, not per `run.py` call.

    #133: the window this lock exists for is precisely the gap BETWEEN runs,
    where ds4-server is deliberately down and a process scan truthfully reports
    "all clear" while the machine is committed for hours.
    """
    taken, why = preflight.acquire_lock(what, pid=owner_pid)
    if not taken:
        raise Refusal(f"could not claim the machine: {why}")
    logger.info("machine lock held: %s", why)
    try:
        yield
    finally:
        released, why = preflight.release_lock(pid=owner_pid)
        logger.info("machine lock released=%s: %s", released, why)


def collect_transcripts(bench_logs: pathlib.Path, arm: ArmSpec, n: int) -> int:
    """Move this trial's transcripts aside so the next run does not clobber them.

    Returns how many moved. Nothing to move is a valid state, not a failure: a
    `--no-client-log` run produces none.
    """
    dest = bench_logs / f"{arm.run_dir}{n}"
    dest.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in bench_logs.glob(f"*{arm.backend}-opencode-1*"):
        if path.is_file() or path.is_dir():
            path.rename(dest / path.name)
            moved += 1
    logger.info("collected %d transcripts to %s", moved, dest.name)
    return moved


def kv_prefix_audit(logdir: pathlib.Path) -> None:
    """Audit the KV prefix misses while the logs are in hand.

    #64: the server logs a line every time the live KV prefix misses, and a
    stalled prefix costs re-prefill on every turn -- 443,974 tokens across the
    four logs we happened to keep. Run here rather than hoping someone runs it
    later on a log that has been cleaned up. Never fatal: it is a read-out, and
    a cycle that completed must not report failure because its audit did not.
    """
    logs = sorted(logdir.glob("ds4server-*.log"))
    if not logs:
        return
    out = logdir / "kv-prefix-audit.txt"
    try:
        done = subprocess.run(
            [
                "uv",
                "run",
                "python",
                str(REPO / "scripts" / "kv_prefix_audit.py"),
                *[str(p) for p in logs],
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        logger.warning("kv prefix audit did not run: %s", exc)
        return
    out.write_text(done.stdout)
    logger.info("kv prefix audit -> %s\n%s", out, done.stdout.rstrip())


def cycle(
    arm: ArmSpec,
    logdir: pathlib.Path,
    bench_logs: pathlib.Path,
    owner_pid: int,
    trials: int = TRIALS,
) -> int:
    """The whole cycle: restart the server, run one trial, repeat."""
    logdir.mkdir(parents=True, exist_ok=True)
    logger.info("logs in: %s", logdir)
    arm.kv.mkdir(parents=True, exist_ok=True)

    # The upstream is otherwise indistinguishable from a working ds4 server,
    # and OpenCode would talk to the wrong thing.
    if not port_answers(SHIM_PORT):
        raise Refusal(
            f"nothing is answering on :{SHIM_PORT}. Start the shim first: "
            "uv run python ds4_qwen_tool_shim.py --port 8101 "
            "--upstream http://127.0.0.1:8000"
        )

    failed: list[str] = []
    with run_lock(
        f"restart_between_trials.py arm {arm.name} (#112, {trials} cycles)", owner_pid
    ):
        for n in range(1, trials + 1):
            tag = f"trial{n}"
            server_log = logdir / f"ds4server-{tag}.log"
            # A fresh server for every trial: that is the experiment, not a
            # precaution. `serving()` stops it on every exit path (#145).
            with ds4_server.serving(
                server_command(arm),
                server_log,
                cwd=DS4_TREE,
                model_id=MODEL_ID,
                want_mtp=arm.want_mtp,
                port=SERVER_PORT,
            ):
                logger.info("=== arm %s %s ===", arm.name, tag)
                out = logdir / f"arm{arm.name}-restart-run{n}.log"
                with out.open("wb") as handle:
                    done = subprocess.run(
                        run_argv(arm, server_log),
                        cwd=REPO,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                logger.info("%s rc=%d; log %s", tag, done.returncode, out)
                if done.returncode != 0:
                    failed.append(tag)
            collect_transcripts(bench_logs, arm, n)

    kv_prefix_audit(logdir)
    logger.info("cycle complete -- %s", logdir)
    logger.info(
        "Compare per-trial pass rates against the arm %s baseline: %s",
        arm.name,
        arm.baseline,
    )
    return ab_driver.report(failed, trials, 1)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("arm", choices=sorted(ARMS))
    p.add_argument("--trials", type=int, default=TRIALS)
    p.add_argument("--logdir", type=pathlib.Path, default=None)
    p.add_argument(
        "--bench-logs",
        type=pathlib.Path,
        default=pathlib.Path(
            os.environ.get("BENCH_LOGS") or pathlib.Path.home() / "bench-logs"
        ),
    )
    args = p.parse_args(argv)

    logs.configure()

    arm = ARMS[args.arm]
    logdir = args.logdir or ab_driver.logdir_for(
        pathlib.Path.home() / "bench-logs", f"112-restart-arm{arm.name}"
    )
    try:
        return cycle(arm, logdir, args.bench_logs, os.getpid(), args.trials)
    except (
        Refusal,
        ds4_server.ServerNeverStarted,
        ds4_server.GraphMismatch,
        ds4_server.NotReady,
        ds4_server.ForeignServer,
    ) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
