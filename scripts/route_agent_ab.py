#!/usr/bin/env python3
"""ds4's Metal 4 TensorOps route against the withheld one, on the agent bench.

#149. Port of `scripts/route_agent_ab.sh` (stage 5 of #235).

Two arms, one backend name:

    t   DS4_METAL_ENABLE_TENSOR=1        the fast TensorOps route
    r   the variable unset               the reference route, withheld

The R arm is defined by the *tree*, not by a flag: `~/git/ds4-metal-ref` sits on
a commit whose subject is "Withhold the automatic Metal 4 tensor enable", so the
route does not turn itself on. The commit hash of the cherry-pick differs from
ivan's; the subject is the identity, and it is checked before anything starts.

Both arms serve under one backend name, so no row says which route produced it.
Attribution is by time window: each sweep appends `<tag> <start> <end>` to
`sweep-order.txt`, and `scripts/route_ab_report.py` joins rows to windows at
read-out. That file's format is load-bearing -- the tags stay `t-sweepN` and
`r-sweepN` and the server logs stay `server-<tag>.log`, because the report
parses both and a completed 2026-09-05 run is already on disk in that shape.

    uv run python scripts/route_agent_ab.py
    uv run python scripts/route_agent_ab.py --sweeps-per-arm 4

## What this fixes on the way across

**The shell cannot run today (#264).** It passes `--skip-tensor-gate`, which
`run.py` removed when the ds4 route gate was rewritten; argparse rejects it and
exits 2 before any work. Each sweep ran under `|| echo "... returned non-zero"`,
so six server restarts and several hours would have produced zero rows and
exited 0. The modern flag is `--allow-unverified-route`, and this passes that.

**The gate was pointed at the wrong tree.** `preflight.ds4_equivalence_state()`
fingerprints `$DS4_TREE/ds4_test`, defaulting to `~/git/ds4-metal`. The shell
never set `DS4_TREE`, so run.py would have judged the *other* tree's binary
while `ds4-metal-ref` served the rows. This exports it.

**Five `pgrep`/`pkill` calls are gone.** Three of them ran the sequence
`pkill -f 'ds4-server --metal'`, sleep, `pgrep`, `pkill -9`, sleep, `pgrep`, and
that pattern is what `pgrep -f` matches wrongly: the shell quoting the pattern
carries the pattern in its own command line. The server is a `unitctl` unit with
a recorded pid (#234), the :8101 shim is identified by the port it holds, and
the phase-0 "is a server resident" check asks `ds4_server.foreign()`.

**The teardown is a context manager.** The shell started each server with `&`
inside a subshell and stopped it only at the top of the *next* restart, so a
clean finish left the last arm's server resident -- 97.9 GiB, the #145 shape.
`ds4_server.serving()` stops on every exit path.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import pathlib
import shutil
import subprocess
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
import metal_equivalence
import metal_route
import ports
import provenance

import logs

logger = logging.getLogger(__name__)

MODELS = pathlib.Path.home() / "models" / "qwen3.8-flash-next-ds4-q4"
GGUF = MODELS / (
    "Qwen3.8-Flash-Next-Q40RoutedExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-"
    "Q8Shared-Q8Out.gguf"
)
PLE = MODELS / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
GLM_Q2 = pathlib.Path.home() / "git" / "ds4" / "gguf" / "GLM-5.3-Flash-Q2.gguf"
TREE = pathlib.Path.home() / "git" / "ds4-metal-ref"
KV = {
    metal_route.TENSOR: pathlib.Path.home() / ".ds4" / "server-kv-149t",
    metal_route.WITHHELD: pathlib.Path.home() / ".ds4" / "server-kv-149r",
}

BENCH_LOGS = pathlib.Path.home() / "bench-logs"
DEFAULT_OUT = BENCH_LOGS / "149-route-ab"
BACKEND = "qwen38fnds4shim"
MODEL_ID = "qwen3.8-flash-next-q4"
SERVER_PORT = 8000
SHIM_PORT = 8101
SHIM_MARKER = "qwen_tool_shim"
TENSOR_ENV = "DS4_METAL_ENABLE_TENSOR"

WITHHOLD_SUBJECT = "Withhold the automatic Metal 4 tensor enable"
GATE_LOG = "gate-validate.log"
GATE_OK = "metal-tensor-equivalence: OK"
OPTIN_ROUTE = "tensor-optin"

# #149's pre-registered reproduction of the drift. The recorded gate log reads
# worst_rms=1.38592 worst_max_abs=7.26952; the bands are wide enough that a
# rebuild moving the last digits is not a refusal, and narrow enough that a
# different failure is.
RMS_BAND = (1.2, 1.6)
MAX_ABS_BAND = (6.0, 8.5)


class Refusing(RuntimeError):
    """A pre-registered condition the run must not start under."""


def check_shim() -> str:
    """The :8101 tool-format shim must be up. Identified by port, then name.

    The shell asked `pgrep -f qwen_tool_shim`, which says a process exists
    somewhere and nothing about :8101. A ds4-server on that port answers a
    connection and strips nothing, which is the whole of #112 -- worth 23 points
    of pass rate -- so the port is found first and the holder is confirmed
    second.
    """
    ok, why = ports.held_by(SHIM_PORT, SHIM_MARKER)
    if not ok:
        raise Refusing(f"the tool-format shim is not serving :{SHIM_PORT}: {why}")
    return why


def check_strip() -> None:
    """Every published row has the strip on (#112)."""
    if os.environ.get("SHIM_NO_STRIP"):
        raise Refusing(
            "SHIM_NO_STRIP is set; every published row has the strip on (#112)"
        )


def missing_assets() -> list[pathlib.Path]:
    """Every file the run needs that is not there. All of them, not the first.

    Reporting one at a time turns a missing model and a missing binary into two
    separate hours-apart refusals.
    """
    return [
        p
        for p in (GGUF, PLE, GLM_Q2, TREE / "ds4-server", TREE / "ds4_test")
        if not p.exists()
    ]


def tree_subject(tree: pathlib.Path = TREE) -> str:
    """The subject of `tree`'s HEAD commit, or "" when it cannot be read."""
    done = subprocess.run(
        ["git", "-C", str(tree), "log", "--format=%s", "-1"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def check_tree(tree: pathlib.Path = TREE) -> str:
    """The R arm is the withhold commit. The subject is the identity."""
    subject = tree_subject(tree)
    if not subject.startswith(WITHHOLD_SUBJECT):
        raise Refusing(
            f"{tree} is not on the withhold commit: its HEAD subject is "
            f"{subject!r}, which does not start with {WITHHOLD_SUBJECT!r}. "
            "The cherry-pick's hash differs from ivan's, so the subject is what "
            "identifies it."
        )
    return subject


def signature(text: str) -> tuple[float, float] | None:
    """(worst_rms, worst_max_abs) from the tensor-optin summary, or None.

    Deliberately **not** the first `Tensor summary` line. A gate run against the
    withhold tree prints two: `route=auto` (the asserted candidate, which runs
    the withheld route and reads all zeros) and `route=tensor-optin` (the drift
    #149 pre-registered). The overall verdict must come from the first and the
    signature from the second, so reading either one for both purposes is
    wrong in one direction or the other.
    """
    summary = metal_equivalence.parse_summary(text, route=OPTIN_ROUTE)
    rms, max_abs = summary.get("worst_rms"), summary.get("worst_max_abs")
    if not isinstance(rms, float) or not isinstance(max_abs, float):
        return None
    return rms, max_abs


def signature_ok(sig: tuple[float, float] | None) -> bool:
    """Whether the drift signature sits in #149's pre-registered bands."""
    if sig is None:
        return False
    rms, max_abs = sig
    return (
        RMS_BAND[0] <= rms <= RMS_BAND[1]
        and MAX_ABS_BAND[0] <= max_abs <= (MAX_ABS_BAND[1])
    )


def gate_recorded(log: pathlib.Path) -> bool:
    """Whether `log` already holds a passing gate, so the run can skip it.

    The gate is a full GLM-5.3-Flash-Q2 load and takes tens of minutes. Reusing
    a passing log is the shell's behaviour and worth keeping, but only when it
    passes **both** halves -- an OK verdict and a signature in band. The shell
    checked both here and would otherwise re-run.
    """
    if not log.exists():
        return False
    text = log.read_text(errors="replace")
    return GATE_OK in text and signature_ok(signature(text))


def validate_routes(out: pathlib.Path) -> None:
    """Phase 0: the equivalence gate, server down. Raises Refusing.

    Runs before any server starts, because `ds4_test` loads a whole second
    model and cannot do it beside a resident engine.
    """
    log = out / GATE_LOG
    if gate_recorded(log):
        logger.info("gate validation already recorded in %s", log)
        return

    resident = ds4_server.foreign()
    if resident or ds4_server.running():
        detail = ", ".join(f"pid {p.pid} ({p.rss_gib:.1f} GiB)" for p in resident)
        raise Refusing(
            "a ds4-server is resident, so the gate cannot load beside it"
            + (f": {detail}" if detail else "")
        )

    logger.info(
        "running ds4_test --metal-tensor-equivalence (full %s load; tens of "
        "minutes)...",
        GLM_Q2.name,
    )
    with log.open("wb") as handle:
        done = subprocess.run(
            ["./ds4_test", "--metal-tensor-equivalence"],
            cwd=TREE,
            env={**os.environ, "DS4_TEST_MODEL": str(GLM_Q2)},
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    text = log.read_text(errors="replace")

    # In the withhold tree the asserted `auto` candidate runs the withheld
    # route, so the overall verdict is OK even though the reported tensor-optin
    # candidate fails. That is the point of the arm, not a broken gate.
    if done.returncode != 0:
        raise Refusing(f"the gate exited {done.returncode}; see {log}")
    if GATE_OK not in text:
        raise Refusing(f"no {GATE_OK!r} verdict in {log}")
    sig = signature(text)
    if not signature_ok(sig):
        raise Refusing(
            f"the {OPTIN_ROUTE} signature is outside the pre-registered bands "
            f"(rms {RMS_BAND}, max_abs {MAX_ABS_BAND}): got {sig}; see {log}"
        )
    logger.info("gate OK, %s drift rms=%.5f max_abs=%.5f", OPTIN_ROUTE, *sig)


def server_command(kv_dir: pathlib.Path) -> list[str]:
    """The ds4-server argv. Identical for both arms -- the environment differs.

    That is the experiment: one binary, one command line, one variable. A flag
    difference would be a second thing changing.
    """
    return ds4_server.argv(
        GGUF,
        PLE,
        kv_dir,
        binary=TREE / "ds4-server",
        port=SERVER_PORT,
    )


def arm_env(arm: str) -> tuple[dict[str, str], tuple[str, ...]]:
    """(what to set, what to remove) for one arm's server process.

    The removal is the half a dict cannot express, and it is not decorative:
    the shell wrote `env -u DS4_METAL_ENABLE_TENSOR` because an operator who
    exported the variable in their own shell would otherwise hand it to the R
    arm, and the R arm's whole definition is that the variable is absent.
    `unitctl.start` merges into `os.environ`, so "not mentioned" means
    "inherited", which is the wrong default here.
    """
    if arm == metal_route.TENSOR:
        return {TENSOR_ENV: "1"}, ()
    return {}, (TENSOR_ENV,)


def assert_route(log: pathlib.Path, arm: str) -> str:
    """Per sweep, from the server's own startup log. Raises Refusing.

    The pre-registration asks for this every sweep and not once per run: the
    binary is the same for both arms, so the *only* evidence that this server
    took this arm's route is what this server printed.
    """
    text = log.read_text(errors="replace") if log.exists() else ""
    if not metal_route.fast_path_ok(text) or not metal_route.arm_route_ok(text, arm):
        raise Refusing(f"{log.name} is {metal_route.why_not(text, arm)}; see {log}")
    return f"{log.name} route assertion passed (arm={arm})"


def serve_arm(
    arm: str, tag: str, out: pathlib.Path
) -> contextlib.AbstractContextManager[object]:
    """This arm's server for one sweep, with its route asserted on entry."""

    @contextlib.contextmanager
    def opened() -> Iterator[object]:
        log = out / f"server-{tag}.log"
        kv_dir = KV[arm]
        kv_dir.mkdir(parents=True, exist_ok=True)
        env, unset = arm_env(arm)
        with ds4_server.serving(
            server_command(kv_dir),
            log,
            cwd=TREE,
            model_id=MODEL_ID,
            want_mtp=False,
            port=SERVER_PORT,
            env=env,
            unset=unset,
        ) as unit:
            # Inside the `with`, so a failed assertion still stops the server.
            # The same mistake one failure mode over cost #256 a leaked shim.
            logger.info("%s", assert_route(log, arm))
            yield unit

    return opened()


def arms(out: pathlib.Path) -> list[ab_driver.Arm]:
    """The two arms. One backend name: attribution is by sweep window."""
    return [
        ab_driver.Arm(
            name=arm,
            backend=BACKEND,
            serve=lambda tag, arm=arm: serve_arm(arm, tag, out),
        )
        for arm in metal_route.ARMS
    ]


def tag_for(arm: ab_driver.Arm, round_number: int) -> str:
    """`t-sweep1`, `r-sweep1`, ... -- the shape route_ab_report.py parses.

    Not `ab_driver`'s default `r1-t`: the report splits a tag into a prefix and
    an index (`_index(tag, "t-sweep")`), the completed 2026-09-05 run is on disk
    under these names, and `r1-r` would also be a needlessly confusing thing to
    read at three in the morning.
    """
    return f"{arm.name}-sweep{round_number}"


def arm_argv(tag: str, out: pathlib.Path, trials: int, harness_head: str) -> list[str]:
    """The `run.py` command line for one sweep.

    `--allow-unverified-route` and not `--skip-tensor-gate` (#264): the latter
    was removed from run.py with the old `metal_tensor_gate_step`, so the shell
    would exit 2 here. Phase 0 above is the stronger check -- it runs the
    equivalence test itself rather than skipping it.
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
        "--allow-unverified-route",
        "--require-harness-head",
        harness_head,
        "--server-log",
        str(out / f"server-{tag}.log"),
    ]


def collect_transcripts(tag: str, out: pathlib.Path, trials: int, since: float) -> int:
    """Move this sweep's transcripts under `out/<tag>/`. Returns the count.

    Both arms write to `~/bench-logs` under the same backend name, so the files
    have to be claimed before the next sweep writes over them.

    `since` is the instant this sweep started, and the filter is not optional.
    Anything older in the shared directory is a leftover from a killed run, and
    the plain `mv <src>/*<backend>-opencode-1*` this driver used sweeps it in:
    `old-sweep1` once held **22 transcripts for a 15-task sweep**, seven of them
    left by a run killed at 08:17. `lib/transcript_move.sh` closed that for one
    driver with a marker file and `find -newer`, because BSD and GNU `touch -t`
    disagree on the token form. Comparing mtimes needs neither.
    """
    destination = out / tag
    destination.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in sorted(BENCH_LOGS.glob(f"*{BACKEND}-opencode-{trials}*")):
        if path.is_dir() or path.stat().st_mtime <= since:
            continue
        shutil.move(str(path), str(destination / path.name))
        moved += 1
    return moved


def record_window(out: pathlib.Path, tag: str, started: str, ended: str) -> None:
    """Append one sweep's window. This file is how rows learn their arm.

    Times of day only, as #138's did, because that is what the report parses
    and it already handles a run crossing midnight.
    """
    with (out / "sweep-order.txt").open("a") as handle:
        handle.write(f"{tag} {started} {ended}\n")


def clock() -> str:
    return time.strftime("%H:%M:%S")


def run_arm(
    arm: ab_driver.Arm,
    tag: str,
    out: pathlib.Path,
    trials: int,
    harness_head: str,
) -> int:
    """One sweep. Returns run.py's exit code.

    Both times are read separately, at the start and at the end. Reading the
    clock once per line was #138's bug: every sweep got the *next* sweep's
    window and the attribution was off by one arm throughout.
    """
    started = clock()
    since = time.time()
    logger.info("=== %s (%s arm, %s) ===", tag, arm.name, BACKEND)
    log = out / f"{tag}.log"
    # `child.run`, not `subprocess.run` (#268): the measurement re-spawns
    # `opencode`, and a signal to this driver reaches neither unless the child
    # has a process group of its own. Stopping a driver once left its run.py
    # writing rows for five minutes against a server the teardown had already
    # stopped, on a machine the lock had already advertised as free.
    rc = child.run(arm_argv(tag, out, trials, harness_head), cwd=REPO, log=log)
    if rc != 0:
        logger.warning("%s returned %d; see %s", tag, rc, log)
    moved = collect_transcripts(tag, out, trials, since)
    logger.info("%s done, %d transcripts", tag, moved)
    record_window(out, tag, started, clock())
    return rc


def write_record(out: pathlib.Path, harness_head: str, sweeps: int) -> None:
    """The run record, written before the first server starts."""
    subject = tree_subject()
    sha = subprocess.run(
        ["git", "-C", str(TREE), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    lines = [
        f"# route agent A/B, started {time.strftime('%Y-%m-%dT%H:%M:%S %Z')}",
        "# pre-registered on #149; screens 1-4 in scripts/route_ab_report.py",
        f"TREE={TREE} @ {sha} {subject}",
        f"GGUF={GGUF.name} ({GGUF.stat().st_size} bytes)",
        f"PLE={PLE.name}  KV_T={KV[metal_route.TENSOR]}  KV_R={KV[metal_route.WITHHELD]}",
        f"BACKEND={BACKEND} (one backend name; arms attributed via sweep-order.txt windows)",
        f"gate validation: {out / GATE_LOG}",
        f"harness pinned at {harness_head} for all {sweeps * 2} sweeps",
    ]
    (out / "run-record.txt").write_text("\n".join(lines) + "\n")
    for line in lines:
        logger.info("%s", line)


def sweep(sweeps: int, trials: int, out: pathlib.Path) -> int:
    """The whole run. Returns a process exit code."""
    out.mkdir(parents=True, exist_ok=True)
    logger.info("logs in: %s", out)

    check_strip()
    logger.info("%s", check_shim())
    if gone := missing_assets():
        raise Refusing("missing: " + ", ".join(str(p) for p in gone))
    logger.info("tree on the withhold commit: %s", check_tree())

    # Point run.py's ds4 route gate at the tree that will actually serve. It
    # fingerprints $DS4_TREE/ds4_test and defaults to ~/git/ds4-metal, so
    # without this it judges a binary no arm runs.
    os.environ["DS4_TREE"] = str(TREE)
    os.environ["DS4_TEST_MODEL"] = str(GLM_Q2)

    validate_routes(out)

    if provenance.code_is_dirty(REPO, untracked=False):
        raise Refusing(
            "the harness has uncommitted code. A comparative run pinned to a "
            "commit cannot be reproduced from one."
        )
    harness_head = provenance.head(REPO)
    write_record(out, harness_head, sweeps)

    if sweeps % 2:
        logger.warning(
            "sweeps-per-arm=%d is odd, so T leads once more than R and the "
            "position term does not cancel (#130, #201). Pre-registered as "
            "such on #149.",
            sweeps,
        )

    def one(arm: ab_driver.Arm, tag: str, round_number: int) -> int:
        return run_arm(arm, tag, out, trials, harness_head)

    failed = ab_driver.run(arms(out), sweeps, one, allow_uneven=True, tag_for=tag_for)
    logger.info("all %d sweeps complete under %s", sweeps * 2, out)
    logger.info(
        "Read it with: uv run python scripts/route_ab_report.py --run-dir %s", out
    )
    return ab_driver.report(failed, sweeps, len(metal_route.ARMS))


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--sweeps-per-arm",
        type=int,
        default=int(os.environ.get("SWEEPS_PER_ARM", "3")),
    )
    p.add_argument("--trials", type=int, default=int(os.environ.get("TRIALS", "1")))
    p.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path(os.environ.get("OUT") or DEFAULT_OUT),
    )
    args = p.parse_args(argv)

    logs.configure()

    try:
        return sweep(args.sweeps_per_arm, args.trials, args.out)
    except (
        Refusing,
        ValueError,
        ds4_server.ForeignServer,
        ds4_server.ServerNeverStarted,
        ds4_server.GraphMismatch,
        ds4_server.NotReady,
    ) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
