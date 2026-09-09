"""Prove the MTP refusal fires, then take rows that carry the treatment (#210).

Port of `scripts/mtp_treatment_gate.sh` (#235 stage 4). The `.sh` stays until a
run agrees with it.

#210's `Done when` is a pair, and the pair is the point:

  1. a real batch writes `draft` on every row of an MTP backend, and
  2. one deliberately broken arm is shown to be refused.

Half 2 without half 1 is a gate nobody has run in anger. Half 1 without half 2
is a green light from a gate that may never fire. #210 exists because 119 MTP
rows were taken with neither.

**The broken arm is not a broken flag.** Omitting `--mtp-model` is the exact
failure the runbook records: `--mtp-draft 7 --mtp-timing` are accepted without
complaint, the argv reads as an MTP arm, and the only place the difference
shows is the server's own `Qwen graph allocated` line. `counters_on()` reads
the argv and *passes* on this configuration. Only the per-trial counters catch
it.

    uv run python scripts/mtp_treatment_gate.py bypass    # expects a REFUSAL
    uv run python scripts/mtp_treatment_gate.py treated   # ~50 min per sweep
    uv run python scripts/mtp_treatment_gate.py probe     # no trials, no rows

`silent` is not a retry. It is the escape the refusal message itself names
("Re-run without --require-draft to measure it deliberately"), and it is how
#151's zero-counter case gets rows instead of an exit code. Do not run it to
make `treated` go green.

## Two things this port fixes rather than reproduces

**The `replay` stage could leave a dumping shim behind.** It restarts the shim
with `SHIM_DUMP` and its own comment says it restores a plain one "on the way
out, so a later stage does not inherit a dumping one" -- but the empty-payload
check `exit 1`s *before* the restore. That path left a payload-dumping shim on
:8101 for every later stage, and nothing downstream would have said so. Here
the shim is a context manager and the restore is in a `finally`.

**The shim is managed, not assumed.** The shell checked `pgrep -f
qwen_tool_shim`, which matches the shell running the pgrep, and then killed
and replaced whatever it found. This owns its shim as a `unitctl` unit and
refuses a foreign one, consistent with #252/#253: we do not kill a process
this project did not start, and we do not run beside one either.
"""

from __future__ import annotations

import argparse
import contextlib
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
import unitctl

logger = logging.getLogger(__name__)

STAGES = ("bypass", "treated", "probe", "probe-shim", "replay", "silent")

MODELS = pathlib.Path.home() / "models" / "qwen3.8-flash-next-ds4-q4"
DS4_MODEL = MODELS / (
    "Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
)
DS4_PLE = MODELS / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_MTP = MODELS / "qwen3.8-flash-next-q4-mtp.gguf"
DS4_TREE = pathlib.Path.home() / "git" / "ds4-metal"

# The MTP and non-MTP KV formats are incompatible and ds4 rejects the other
# one's checkpoints. The bypass arm gets its own directory: pointing it at
# server-kv-mtp would leave rejected checkpoints behind and make the next real
# arm re-prefill, whose only symptom is that it looks slower.
KV_TREATED = pathlib.Path.home() / ".ds4" / "server-kv-mtp"
KV_BYPASS = pathlib.Path.home() / ".ds4" / "server-kv-210-bypass"

MODEL_ID = "qwen3.8-flash-next-q4"
BACKEND = "qwen38fnds4mtp7shim"
SERVER_PORT = 8000
SHIM_PORT = 8101
SHIM_UNIT = "qwen-tool-shim-8101"
SIDECAR_MARKER = "MTP sidecar loaded"

# mbox-scan is the cheapest task in the corpus (median 10.8 s over 8 rows).
# The bypass arm is a demonstration that an exit code fires, not a
# measurement, so it buys the shortest trial that still drives real traffic.
BYPASS_TASK = "mbox-scan"


class Refusal(RuntimeError):
    """A reason not to start, or not to trust what started."""


def port_answers(port: int, timeout: float = 1.0) -> bool:
    """Whether something is listening on `port`."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def server_command(want_mtp: bool, kv_dir: pathlib.Path) -> list[str]:
    """The ds4-server argv for one arm.

    The bypass arm is this command **minus `--mtp-model`** and nothing else.
    It is not a broken flag: `--mtp-draft 7 --mtp-timing` are accepted without
    complaint, the argv reads as an MTP arm, `counters_on()` passes on it, and
    the only place the difference shows is the graph line.

    **The draft flags go through `extra` on the bypass arm, deliberately.**
    `ds4_server.argv` refuses to emit `--mtp-draft` without `--mtp-model` --
    it warns and drops them, which is right for every other driver, because a
    half-MTP arm built by accident is exactly #151. This gate needs that arm
    on purpose: it is the subject of #210's half 2, and building it any other
    way would demonstrate a *different* broken arm -- one the argv check would
    also catch, which is precisely what must not be true.
    """
    if want_mtp:
        return ds4_server.argv(
            DS4_MODEL,
            DS4_PLE,
            kv_dir,
            binary=DS4_TREE / "ds4-server",
            port=SERVER_PORT,
            mtp_model=DS4_MTP,
            mtp_draft=7,
            mtp_timing=True,
        )
    return ds4_server.argv(
        DS4_MODEL,
        DS4_PLE,
        kv_dir,
        binary=DS4_TREE / "ds4-server",
        port=SERVER_PORT,
        extra=("--mtp-draft", "7", "--mtp-timing"),
    )


def assert_sidecar(log: pathlib.Path) -> str:
    """The treated arm must say the sidecar loaded, not merely that MTP is on.

    `ds4_server.assert_graph` covers the `MTP=off` half both ways. This is the
    extra check the gate needs and the library must not have: it is specific
    to a driver that knows there is a sidecar to load.
    """
    for line in log.read_text(errors="replace").splitlines():
        if SIDECAR_MARKER in line:
            return line
    raise Refusal(f"no {SIDECAR_MARKER!r} line in {log}")


@contextlib.contextmanager
def run_lock(what: str, owner_pid: int) -> Iterator[None]:
    """Claim the machine for the whole stage, and release it on every exit."""
    taken, why = preflight.acquire_lock(what, pid=owner_pid)
    if not taken:
        raise Refusal(f"could not claim the machine: {why}")
    logger.info("machine lock held: %s", why)
    try:
        yield
    finally:
        released, why = preflight.release_lock(pid=owner_pid)
        logger.info("machine lock released=%s: %s", released, why)


def shim_argv() -> list[str]:
    """The tool shim's command line.

    SHIM_DUMP is an environment variable, not a flag, so the replay stage's
    dumping shim and a plain one share this argv exactly.
    """
    return [
        "uv",
        "run",
        "python",
        "ds4_qwen_tool_shim.py",
        "--port",
        str(SHIM_PORT),
        "--upstream",
        f"http://127.0.0.1:{SERVER_PORT}",
    ]


@contextlib.contextmanager
def shim(log: pathlib.Path, dump: pathlib.Path | None = None) -> Iterator[None]:
    """Own the tool shim on :8101 for the duration of the block.

    **Always restores a plain shim**, which is the bug this replaces. The
    shell restarted the shim with SHIM_DUMP and restored a plain one at the end
    of the replay branch -- but its empty-payload check `exit 1`s before the
    restore, so that path left a payload-dumping shim on :8101 for every later
    stage, and nothing downstream would have said so.

    A foreign shim is refused, not killed (#252, #253): we do not signal a
    process this project did not start, and we do not run beside one either.
    """
    env = {"SHIM_DUMP": str(dump)} if dump is not None else None
    if port_answers(SHIM_PORT) and unitctl.state(unitctl.read(SHIM_UNIT)) != (
        unitctl.RUNNING
    ):
        raise Refusal(
            f"something this run did not start is listening on :{SHIM_PORT}. "
            "Stop it and re-run: this stage manages its own shim, and it "
            "cannot restore a shim it did not start."
        )
    unitctl.stop(SHIM_UNIT)
    unitctl.start(SHIM_UNIT, shim_argv(), log=log, cwd=REPO, env=env)
    if not _wait_for_port(SHIM_PORT):
        raise Refusal(f"the shim did not answer on :{SHIM_PORT}; see {log}")
    try:
        yield
    finally:
        unitctl.stop(SHIM_UNIT)


def _wait_for_port(port: int, timeout: float = 20.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_answers(port):
            return True
        time.sleep(0.25)
    return False


def run_argv(
    logdir: pathlib.Path,
    server_log: pathlib.Path,
    *,
    batch: str,
    trials: int,
    require_draft: bool,
    task: str | None = None,
    results: pathlib.Path | None = None,
) -> list[str]:
    """A `run.py` command line for one stage."""
    argv = [
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
        "--server-log",
        str(server_log),
        "--draft-log-engine",
        "ds4",
        "--require-draft" if require_draft else "--no-require-draft",
    ]
    if task is not None:
        argv += ["--task", task]
    if results is not None:
        argv += ["--results", str(results)]
    return argv


def _run(argv: Sequence[str], out: pathlib.Path) -> int:
    """Run a command, tee-ing to `out`. Returns its real exit code.

    The shell piped every stage through `tee` and recovered the status with
    `${PIPESTATUS[0]}` -- correctly, but only where it remembered. Here the
    status is the return value and there is no pipeline to lose it to.
    """
    with out.open("wb") as handle:
        done = subprocess.run(
            list(argv), cwd=REPO, stdout=handle, stderr=subprocess.STDOUT, check=False
        )
    logger.info("-> rc=%d; log %s", done.returncode, out)
    return done.returncode


@contextlib.contextmanager
def arm(
    want_mtp: bool, kv_dir: pathlib.Path, server_log: pathlib.Path
) -> Iterator[None]:
    """One server, asserted to be the arm it claims, for the block's duration.

    The runbook says to read the graph line before trusting an MTP row. A
    bypass arm that quietly loaded the sidecar would produce a passing run and
    prove nothing; a treated arm that quietly did not is the whole of #210.
    """
    kv_dir.mkdir(parents=True, exist_ok=True)
    with ds4_server.serving(
        server_command(want_mtp, kv_dir),
        server_log,
        cwd=DS4_TREE,
        model_id=MODEL_ID,
        want_mtp=want_mtp,
        port=SERVER_PORT,
    ):
        if want_mtp:
            logger.info("sidecar: %s", assert_sidecar(server_log))
        yield


def stage_bypass(logdir: pathlib.Path, server_log: pathlib.Path, trials: int) -> int:
    """The deliberately broken arm. A zero exit here is a FAILURE.

    The gate is the subject, not the rows. `--results` points at a scratch
    file so the claim "the broken arm contaminated nothing" does not rest on
    reading the caller correctly.
    """
    scratch = logdir / "bypass-scratch.jsonl"
    with arm(False, KV_BYPASS, server_log):
        rc = _run(
            run_argv(
                logdir,
                server_log,
                batch="210-bypass-demo",
                trials=1,
                require_draft=True,
                task=BYPASS_TASK,
                results=scratch,
            ),
            logdir / "run-bypass.log",
        )
    logger.info("bypass arm exit=%d (0 would mean the gate did NOT fire)", rc)
    written = 0
    if scratch.exists():
        written = len([ln for ln in scratch.read_text().splitlines() if ln.strip()])
    logger.info("rows written by the refused arm: %d", written)
    if rc == 0:
        raise Refusal("the broken arm ran to completion. The gate is not binding.")
    if written:
        raise Refusal(
            f"the refused arm wrote {written} rows; a refusal must raise before "
            "write_row, so this is the gate firing too late to matter."
        )
    return 0


def stage_treated(logdir: pathlib.Path, server_log: pathlib.Path, trials: int) -> int:
    with arm(True, KV_TREATED, server_log):
        return _run(
            run_argv(
                logdir,
                server_log,
                batch="210-treated",
                trials=trials,
                require_draft=True,
            ),
            logdir / "run-treated.log",
        )


def stage_silent(logdir: pathlib.Path, server_log: pathlib.Path, trials: int) -> int:
    """#151's zero-counter case, measured deliberately rather than refused."""
    with arm(True, KV_TREATED, server_log):
        return _run(
            run_argv(
                logdir,
                server_log,
                batch="210-silent-arm",
                trials=trials,
                require_draft=False,
            ),
            logdir / "run-silent.log",
        )


def stage_probe(
    logdir: pathlib.Path,
    server_log: pathlib.Path,
    trials: int,
    *,
    via_shim: bool = False,
) -> int:
    """Engagement probes at two prompt sizes, no trials and no rows.

    PAD=0 is the size #151's earlier measurement used (cycles=297 with tools);
    PAD=11000 is the size the agent harness actually sends (0 cycles with
    tools). If `tools` is the discriminator, both pads look alike. If length
    is, both shapes do.
    """
    base = f"http://127.0.0.1:{SHIM_PORT if via_shim else SERVER_PORT}"
    # Through the shim, `stream` and `tools-stream` are the client's real
    # shapes: the shim converts them to non-streaming upstream calls, and
    # whether MTP survives that conversion is what the direct probe cannot ask.
    arms = (
        ["plain", "tools", "stream", "tools-stream"] if via_shim else ["plain", "tools"]
    )
    tag = "shim-" if via_shim else ""
    worst = 0
    with arm(True, KV_TREATED, server_log):
        for pad in (0, 11000):
            logger.info("=== %spad=%d ===", tag, pad)
            rc = _run(
                [
                    "uv",
                    "run",
                    "python",
                    "scripts/mtp_engagement.py",
                    "--base-url",
                    base,
                    "--model",
                    MODEL_ID,
                    "--server-log",
                    str(server_log),
                    "--arms",
                    *arms,
                    "--repeats",
                    "2",
                    "--pad-tokens",
                    str(pad),
                    "--max-tokens",
                    "200",
                    "--json",
                    str(logdir / f"engagement-{tag}pad{pad}.json"),
                ],
                logdir / f"probe-{tag}pad{pad}.log",
            )
            worst = worst or rc
    return worst


def stage_replay(logdir: pathlib.Path, server_log: pathlib.Path, trials: int) -> int:
    """Capture a real agent payload through the shim, then bisect it.

    The dumping shim is a context manager, which is the fix: the shell's
    empty-payload check exited before its restore, leaving a payload-dumping
    shim on :8101 for every later stage.
    """
    dump = logdir / "agent-payload.json"
    with shim(logdir / "shim-dump.log", dump=dump), arm(True, KV_TREATED, server_log):
        # One cheap trial, only to make the client produce a real request. Its
        # row is a by-product; require_draft is off because a zero here is the
        # thing being investigated, not a reason to stop.
        _run(
            run_argv(
                logdir,
                server_log,
                batch="151-replay-capture",
                trials=1,
                require_draft=False,
                task=BYPASS_TASK,
                results=logdir / "replay-capture.jsonl",
            ),
            logdir / "run-replay-capture.log",
        )
        if not dump.exists() or dump.stat().st_size == 0:
            raise Refusal(
                f"the shim captured no payload at {dump} -- SHIM_DUMP writes only "
                "the first INSTRUCTED payload, and this trial may have had none."
            )
        return _run(
            [
                "uv",
                "run",
                "python",
                "scripts/mtp_replay_probe.py",
                "--payload",
                str(dump),
                "--server-log",
                str(server_log),
                "--base-url",
                f"http://127.0.0.1:{SERVER_PORT}",
                "--json",
                str(logdir / "replay-ablations.json"),
            ],
            logdir / "replay.log",
        )


def stage(name: str):
    """The function for a stage name."""
    return {
        "bypass": stage_bypass,
        "treated": stage_treated,
        "silent": stage_silent,
        "probe": stage_probe,
        "probe-shim": lambda d, s, t: stage_probe(d, s, t, via_shim=True),
        "replay": stage_replay,
    }[name]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="The #210 MTP treatment gate.")
    p.add_argument("stage", choices=STAGES)
    p.add_argument("--trials", type=int, default=int(os.environ.get("TRIALS", "1")))
    p.add_argument("--logdir", type=pathlib.Path, default=None)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    logdir = args.logdir or ab_driver.logdir_for(
        pathlib.Path.home() / "bench-logs", f"210-treatment-gate-{args.stage}"
    )
    logdir.mkdir(parents=True, exist_ok=True)
    logger.info("logs in: %s", logdir)
    server_log = logdir / f"ds4server-{args.stage}.log"

    # #210 step 1: "counters_requested is never false on a row that claims
    # MTP". The field reads this process's environment and `counters_on` reads
    # the server's argv, so a server started with --mtp-timing alone writes
    # `counters_requested: false, counters_on: true` -- and #210's own body
    # reads a false there as "accepted: 0 means not measured". On this run it
    # means measured and zero, which is the opposite.
    os.environ["DS4_MTP_TIMING"] = "1"

    try:
        with run_lock(f"mtp_treatment_gate.py {args.stage} (#210)", os.getpid()):
            # `replay` owns the shim itself; every other stage needs one up.
            if args.stage == "replay":
                rc = stage(args.stage)(logdir, server_log, args.trials)
            else:
                with shim(logdir / "shim.log"):
                    rc = stage(args.stage)(logdir, server_log, args.trials)
        logger.info("stage %s complete -- %s", args.stage, logdir)
        return rc
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
