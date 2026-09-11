#!/usr/bin/env python3
"""Interleaved agent-suite A/B for two whole STACKS -- engine + weights. #138

Port of `scripts/stack_agent_ab.sh` (#235). The arm description, the server
argv, the pair checks and the run record already moved to `lib/stack_arm.py`
(#260); this is the sweep loop that was left.

The decode A/B answers rate. This answers the only question that decides
anything: does a session finish, and how fast.

**Pre-registered as a SCREEN, not a superiority test.** Two sweeps per arm is
n=30, which resolves a pass-rate difference of ~18-27 pp and a paired wall
difference of ~17-26%. The effect expected from the decode A/B (-24.5%
prefill, +9.5% decode) works out to roughly -10% to -17% of session wall, at or
below that bar, so a superiority pre-registration at this n would land on
"could not tell" almost deterministically and is not made. What it does buy:
whether the stack loads, the shim still translates, sessions complete, and
nothing is catastrophically wrong -- the prerequisite for a 3+3 paired run.

Both arms are overridable through the environment, so this can answer a
question other than #138's without a near-copy drifting away from the original.
The defaults ARE #138.

    uv run python scripts/stack_agent_ab.py --sweeps 2
    NEW_FLAGS='--mtp-draft 7' NEW_RUN_FLAGS=--no-require-draft \\
        uv run python scripts/stack_agent_ab.py

## What the port removes, and what it must not lose

**The EXIT trap.** The shell armed exactly one trap and said why at length:
`ds4_stop_on_exit` does `trap - EXIT INT TERM` then `exit`, so a second handler
chained after it never runs, and arming both left mlx-serve -- ~85 GiB
resident -- unstopped on every normal exit. Nested `with` blocks cannot get
that order wrong.

**The dirty check no longer shells out to uv.** The shell ran
`uv run --frozen python -c 'from provenance import code_is_dirty'` in a
subshell and decoded a three-way exit status, because a bare `uv run` can
rewrite `uv.lock` -- a tracked file -- and so the guard against a dirty tree
could dirty one itself. In-process there is no lock to rewrite and no status to
decode; the predicate is called directly and an exception is the third state.

**The transcript move keeps its mtime filter.** `move_transcripts_since` exists
because a run killed before its own move leaves transcripts that the next
same-arm sweep claims: `old-sweep1` once held 22 transcripts for a 15-task
sweep. The shell needed a marker file and `find -newer` because BSD and GNU
`touch -t` disagree; comparing mtimes needs neither.

**Both times, written separately.** sweep-order.txt once carried one time,
written at the END, while the report read it as the sweep's START. Every window
held the next sweep's rows: on the 2026-09-05 re-run 45 of 60 rows fit no
window and the old-arm control read 14/30 against a true 27/30.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import pathlib
import shlex
import shutil
import sys
import time
from collections.abc import Iterator, Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import ab_driver
import child
import ds4_server
import mlx_serve
import preflight
import provenance
import stack_arm
import tool_shim
import wait_ready

import logs

logger = logging.getLogger(__name__)

BENCH_LOGS = pathlib.Path.home() / "bench-logs"
DEFAULT_OUT = BENCH_LOGS / "138-stack-ab"
LOCK_WHAT = "stack_agent_ab.py (#138/#268)"
SHIM_PORT = 8101

DEFAULTS = {
    "NEW": {
        "TREE": pathlib.Path.home() / "git" / "ds4-ivan-qwen38fn",
        "GGUF": pathlib.Path.home()
        / "models/qwen3.8-flash-next-ds4-q4k-imatrix"
        / (
            "Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-"
            "Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
        ),
        "PLE": pathlib.Path.home()
        / "models/qwen3.8-flash-next-ds4-q4k-imatrix/Qwen3.8-Flash-Next-PLE-Q4_1.gguf",
        "KV": pathlib.Path.home() / ".ds4" / "server-kv-kimat",
        "BACKEND": "qwen38fnds4kimat",
    },
    "OLD": {
        "TREE": pathlib.Path.home() / "git" / "ds4-metal",
        "GGUF": pathlib.Path.home()
        / "models/qwen3.8-flash-next-ds4-q4"
        / (
            "Qwen3.8-Flash-Next-Q40RoutedExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-"
            "Q8Shared-Q8Out.gguf"
        ),
        "PLE": pathlib.Path.home()
        / "models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf",
        "KV": pathlib.Path.home() / ".ds4" / "server-kv",
        "BACKEND": "qwen38fnds4shim",
    },
}


class Refusing(RuntimeError):
    """A pre-registered condition the run must not start under."""


def arm_from_env(side: str) -> stack_arm.Arm:
    """Build one arm from `<SIDE>_*`, defaulting to #138's own stacks.

    Every override the shell had is kept. They are not conveniences: #210/#151
    need MTP on against MTP off in the same tree, #191 needs a second engine,
    and #225 needs two mlx-serve *binaries* so both arms do not resolve to the
    same brew install.
    """
    d = DEFAULTS[side]

    def get(key: str, fallback: object = "") -> str:
        return os.environ.get(f"{side}_{key}") or str(fallback)

    engine = get("ENGINE", stack_arm.DS4)
    mlx_model = get("MLX_MODEL")
    return stack_arm.Arm(
        name=side.lower(),
        backend=get("BACKEND", d["BACKEND"]),
        engine=engine,
        tree=pathlib.Path(get("TREE", d["TREE"])),
        gguf=pathlib.Path(get("GGUF", d["GGUF"])),
        ple=pathlib.Path(get("PLE", d["PLE"])),
        kv=pathlib.Path(get("KV", d["KV"])),
        flags=get("FLAGS"),
        run_flags=get("RUN_FLAGS"),
        mlx_model=pathlib.Path(mlx_model) if mlx_model else None,
        mlx_port=int(get("MLX_PORT", stack_arm.MLX_PORT)),
        mlx_bin=get("MLX_BIN", stack_arm.MLX_SERVE),
    )


def check_strip() -> None:
    """The published rows were all taken with the strip on (#112).

    An inherited `SHIM_NO_STRIP` would make this a different experiment without
    saying so anywhere a reader would look.
    """
    if os.environ.get("SHIM_NO_STRIP"):
        raise Refusing(
            "SHIM_NO_STRIP is set; every published row has the strip on (#112)"
        )


def check_shim(new: stack_arm.Arm, old: stack_arm.Arm) -> str:
    """Only a ds4 arm sits behind the shim. mlx-serve does not.

    A guard written for ds4 and applied to mlx-serve would either refuse a
    legitimate run or pass while checking nothing.
    """
    if not (new.is_ds4 or old.is_ds4):
        return "no ds4 arm, so the :8101 shim is not this run's concern"
    return tool_shim.require_running(SHIM_PORT)


def worktree_state() -> tuple[bool, str]:
    """(clean, why). Three outcomes, because "could not check" is not "clean".

    The shell decoded this from an exit status because it ran the predicate in
    a subshell under `uv run --frozen`. Here it is called directly, so the
    third state is an exception rather than a status of 2 -- and the hazard
    that forced `--frozen` is gone with the subshell: a bare `uv run` can
    rewrite `uv.lock`, a TRACKED file, so the guard whose job is to catch a
    dirty tracked path could dirty one itself.

    `untracked=True` is deliberate and is the whole point (#227 defect 3). It
    is the one gap `--require-harness-head` leaves -- that pin reads
    `untracked=False` -- so a file git has never seen sails past it into a
    read-out void hours later.

    Fails closed, asymmetrically with the pre-commit hook's fail-open: there a
    false refusal blocks every legitimate commit; here the run is already
    committed to the machine and a false clean can sink ~3.5 hours of sweeps.
    Refusing one sweep on doubt costs ~15 minutes.
    """
    try:
        if provenance.code_is_dirty(REPO):
            return False, "the harness worktree has uncommitted code"
    except Exception as exc:  # noqa: BLE001 -- an unreadable tree is not a clean one
        return False, f"could not confirm a clean harness worktree: {exc!r}"
    return True, "clean"


@contextlib.contextmanager
def serving(arm: stack_arm.Arm, tag: str, out: pathlib.Path) -> Iterator[object]:
    """This arm's engine for one sweep, with the other engine stopped first.

    BOTH engines are stopped, not just the one about to start: the previous
    sweep may have been the other arm, and two ~100 GiB servers do not fit on
    this machine at once. Stopping one that is not running is a no-op.
    """
    log = out / f"server-{tag}.log"
    ds4_server.stop(f"for {tag}")
    mlx_serve.stop_and_prove(f"for {tag}")
    command = stack_arm.server_argv(arm)
    logger.info(
        "starting %s on %s%s",
        tag,
        arm.engine,
        f" with {arm.flags}" if arm.flags else "",
    )
    if arm.is_ds4:
        assert arm.kv is not None
        arm.kv.mkdir(parents=True, exist_ok=True)
        with ds4_server.serving(
            command,
            log,
            cwd=arm.tree,
            model_id=arm.served_model,
            # No MTP expectation: this harness's arms differ BY configuration
            # (#210/#151 run MTP on against MTP off in the same tree), so there
            # is no single answer to assert. `None` logs the graph line and
            # asserts nothing, which is what the shell did.
            want_mtp=None,
            port=arm.port,
        ) as unit:
            yield unit
        return
    with mlx_serve.serving(command, log, cwd=REPO) as unit:
        # No route recording: the Metal route is a ds4 concept, and a row for
        # this arm must read `unrecorded` rather than inherit ds4's provenance
        # (#149).
        if not wait_ready.ready(
            f"http://127.0.0.1:{arm.port}",
            arm.served_model,
            timeout=wait_ready.DEFAULT_TIMEOUT,
        ):
            raise Refusing(
                f"{tag}: :{arm.port} did not serve {arm.served_model}. "
                "wait_ready needs the pack's directory name as --model; the "
                "shell's `| tail -1` swallowed argparse's exit 2 and the "
                "harness proceeded WITHOUT waiting, recording 503s as failed "
                "rows."
            )
        yield unit


def run_argv(
    arm: stack_arm.Arm, tag: str, out: pathlib.Path, harness_head: str
) -> list[str]:
    """`run.py` for one sweep.

    `--server-log` is what gives the row a `draft` field at all (#210); without
    it an MTP arm that never speculated is indistinguishable from one that did.
    The log is this sweep's own server, started moments ago, so the probe's byte
    window covers exactly this sweep.
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
        "--require-harness-head",
        harness_head,
        "--server-log",
        str(out / f"server-{tag}.log"),
    ]
    # `arm.draft_log_engine` is None for an engine that does no speculative
    # decoding, and the flag is then OMITTED rather than passed with a name
    # run.py rejects -- an argparse error ends the sweep in one second. It was
    # hard-coded `ds4` before this script had a second engine.
    if arm.draft_log_engine is not None:
        argv += ["--draft-log-engine", arm.draft_log_engine]
    if arm.run_flags:
        argv += shlex.split(arm.run_flags)
    return argv


def collect_transcripts(
    arm: stack_arm.Arm, tag: str, out: pathlib.Path, since: float
) -> int:
    """Move this sweep's transcripts, and only the ones it wrote.

    A run killed before ITS move ran leaves transcripts behind, and the next
    run of the same arm sweeps them in: `old-sweep1` once held 22 transcripts
    for a 15-task sweep, 7 of them from a run killed at 08:17.
    """
    destination = out / tag
    destination.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in sorted(BENCH_LOGS.glob(f"*{arm.backend}-opencode-1*")):
        if path.is_dir() or path.stat().st_mtime <= since:
            continue
        shutil.move(str(path), str(destination / path.name))
        moved += 1
    return moved


def sweep(arm: stack_arm.Arm, tag: str, out: pathlib.Path, harness_head: str) -> int:
    """One sweep. Returns 0 only if run.py survived AND left transcripts.

    Both halves are needed: `run.py` can exit 0 on a session that wrote
    nothing, and a sweep with no evidence is a failed sweep whatever its exit
    code says.
    """
    clean, why = worktree_state()
    if not clean:
        logger.error("%s refusing: %s", tag, why)
        return 1

    started = time.strftime("%H:%M:%S")
    since = time.time()
    logger.info("=== %s (%s) ===", tag, arm.backend)
    log = out / f"{tag}.log"
    # child.run, not subprocess.run: run.py re-spawns `opencode`, and a signal
    # to this driver reaches neither (#268).
    rc = child.run(run_argv(arm, tag, out, harness_head), cwd=REPO, log=log)
    if rc != 0:
        logger.warning("%s returned non-zero (rc=%d); see %s", tag, rc, log)
    moved = collect_transcripts(arm, tag, out, since)
    logger.info("%s done, %d transcripts", tag, moved)
    with (out / "sweep-order.txt").open("a") as handle:
        handle.write(f"{tag} {started} {time.strftime('%H:%M:%S')}\n")
    return 1 if (rc != 0 or moved == 0) else 0


def stamp_run_start(out: pathlib.Path) -> str:
    """Record the run's start instant and clear the previous run's sweep order.

    `stack_agent_report` takes the run DATE from `run-record.txt`'s first line
    and the sweep windows from `sweep-order.txt`. The shell truncated both per
    run; this port truncated run-record (it `write_text`s the record) but
    opened sweep-order in append mode, so a prior run's lines survived and the
    report attached THIS run's date to them. #282 (2026-09-10) VOIDed at
    read-out on exactly that: eight stale lines plus a run-record whose first
    line was `# stack agent A/B`, not a date. So: truncate sweep-order here,
    and return the ISO started line for `run()` to head the record with, in the
    shape the report's `run_started` parses (`date '+%Y-%m-%dT%H:%M:%S %Z'`).
    """
    (out / "sweep-order.txt").write_text("")
    return time.strftime("%Y-%m-%dT%H:%M:%S %Z")


def run(sweeps: int, out: pathlib.Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    started_iso = stamp_run_start(out)
    new, old = arm_from_env("NEW"), arm_from_env("OLD")

    check_strip()
    logger.info("%s", check_shim(new, old))
    stack_arm.check_pair(new, old)
    stack_arm.check_assets(new)
    stack_arm.check_assets(old)

    # Pin the harness for the whole comparison. On 2026-09-04 four sweeps of
    # this script's own A/B recorded FOUR different harness_head values,
    # because the harness was being committed to from the checkout the batch
    # ran from -- new-sweep1 at 563e94b against old-sweep1 at 19958b1. The two
    # arms were not running the same code, which voids the comparison whatever
    # the stacks did.
    if provenance.code_is_dirty(REPO, untracked=False):
        raise Refusing(
            "the harness has uncommitted code. A comparative run pinned to a "
            "commit cannot be reproduced from one."
        )
    harness_head = provenance.head(REPO)

    # The ISO started line MUST be first: the report reads the run date from
    # it (stamp_run_start), and without it read-out VOIDs (#282).
    record = f"{started_iso}\n" + stack_arm.describe(new, old, sweeps=sweeps)
    record += f"\nharness pinned at {harness_head} for all {sweeps * 2} sweeps\n"
    (out / "run-record.txt").write_text(record)
    for line in record.splitlines():
        logger.info("%s", line)

    if sweeps % 2:
        logger.warning(
            "sweeps=%d is odd -- one arm leads once more than the other and "
            "the position term does not cancel (#130). Prefer an even count.",
            sweeps,
        )

    by_name = {a.name: a for a in (new, old)}
    driver_arms = [
        ab_driver.Arm(
            name=a.name,
            backend=a.backend,
            serve=lambda tag, a=a: serving(a, tag, out),
        )
        for a in (new, old)
    ]

    def one(driver_arm: ab_driver.Arm, tag: str, round_number: int) -> int:
        return sweep(by_name[driver_arm.name], tag, out, harness_head)

    # Hold the machine lock across every sweep (#268). Each arm stops both
    # ~100 GiB servers and starts one, so between arms no server is resident
    # and a process scan reports the machine free; run.py gets `--no-lock`
    # because this holds it. Released last, after the measurement child is
    # reaped -- the ordering whose absence left rows on a "free" machine.
    with ab_driver.machine_lock(LOCK_WHAT, os.getpid(), preflight):
        failed = ab_driver.run(
            driver_arms,
            sweeps,
            one,
            allow_uneven=True,
            tag_for=lambda a, n: f"{a.name}-sweep{n}",
        )
    if not failed:
        logger.info("all %d sweeps complete under %s", sweeps * 2, out)
    else:
        # An overnight run that opens this directory reads the last words. A
        # VOID is only discovered at report time, so a wrapper that says
        # "complete" while holding zero-evidence sweeps turns that into a green
        # read.
        note = (
            f"{len(failed)} of {sweeps * 2} sweeps FAILED; run is not green: "
            f"{', '.join(failed)}.\n"
            "    Open the per-sweep logs and transcript dirs named in this run.\n"
        )
        logger.error("%s", note.strip())
        with (out / "run-record.txt").open("a") as handle:
            handle.write(note)
    return len(failed)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sweeps", type=int, default=int(os.environ.get("SWEEPS", "2")))
    p.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path(os.environ.get("OUT") or DEFAULT_OUT),
    )
    args = p.parse_args(argv)

    logs.configure()
    try:
        return run(args.sweeps, args.out)
    except (
        Refusing,
        ValueError,
        tool_shim.NotServing,
        ds4_server.ForeignServer,
        ds4_server.ServerNeverStarted,
        ds4_server.GraphMismatch,
        ds4_server.NotReady,
        mlx_serve.ForeignServer,
        mlx_serve.WouldNotStop,
    ) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
