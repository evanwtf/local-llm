#!/usr/bin/env python3
"""Does the sandbox target layout change the pass rate? #146

Port of `scripts/targets_ab.sh` (#235).

`--targets sandbox` builds the agent's export from this repo's own clones and
never renames anything in `~/git`. It also changes what the agent sees when it
guesses the operator's path: under legacy the guess is satisfied, under sandbox
it is denied. That is a behaviour change, it lands on the pass rate, and the
cutover needs a measurement rather than an argument.

Pre-registered, before any run:

    cut over:  the two arms are within 1 task per sweep of 15, across 2 sweeps
               per arm -- the layout is a change of plumbing, not of measured
               behaviour, and sandbox becomes the default.
    do not:    sandbox is worse by more than that -- the denied guess is
               costing real trials, and the cutover is abandoned on the record
               with the number that killed it.
    no call:   the arms differ by more than 1 task but in opposite directions
               across sweeps -- the effect is inside our run-to-run noise and
               needs more sweeps than this batch has.

    uv run python scripts/targets_ab.py
    uv run python scripts/targets_ab.py --runs 4 --until 23:30

Read out with:

    uv run python scripts/strip_ab_report.py \\
        --results benchmarks/agent/results-146-targets-ab.jsonl \\
        --manifest benchmarks/agent/results-146-targets-ab-manifest.jsonl

## What the port removes

**The dry run can no longer kill a live batch.** On 2026-09-06 five dry runs of
the shell each reached an unguarded `pkill -f qwen_tool_shim` and killed the
shim belonging to the *real* batch in progress: run 4's smoke gate got
"Connection refused", the batch ended after three runs -- legacy once, sandbox
twice -- and could not satisfy its own pre-registration. The machine lock did
not catch it, because a dry run skips the lock. Here the shim is a `unitctl`
unit and stopping it signals a pid this process recorded, so a run that started
no shim stops none. The guard is structural rather than remembered.

**The cutoff is integer arithmetic.** #175: `[ "23:35" \\< "2359" ]` is true,
because ':' sorts above '5', so a string compare fired the cutoff whenever the
hour was 23.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import pathlib
import subprocess
import sys
from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import batch as batchlib
import ds4_server
import preflight
import provenance
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
PREFIX = "146-targets"
ARMS = ("legacy", "sandbox")
SHIM_PORT = 8101
SERVER_PORT = 8000
DEFAULT_RESULTS = REPO / "benchmarks" / "agent" / "results-146-targets-ab.jsonl"


def manifest_for(results: pathlib.Path) -> pathlib.Path:
    return results.with_name(results.stem + "-manifest.jsonl")


def check_dry_run_paths(results: pathlib.Path, manifest: pathlib.Path) -> None:
    """A dry run must never touch the real results paths.

    Compared against the defaults rather than merely for non-empty: the
    obvious invocation has to be impossible, not merely undocumented.
    """
    default_manifest = manifest_for(DEFAULT_RESULTS)
    if results == DEFAULT_RESULTS or manifest == default_manifest:
        raise batchlib.Void(
            "a dry run must override --results and --manifest away from the "
            f"real paths ({DEFAULT_RESULTS.name}, {default_manifest.name})"
        )


def server_command() -> list[str]:
    return ds4_server.argv(
        DS4_MODEL,
        DS4_PLE,
        DS4_KV,
        binary=DS4_TREE / "ds4-server",
        port=SERVER_PORT,
    )


def sync_targets() -> None:
    """The sandbox arm measures nothing if its clones are stale.

    A stale clone fails as a wrong pass rate rather than as an error, which is
    the worst way for a comparison to be wrong.
    """
    subprocess.run(
        ["uv", "run", "python", "scripts/sync_sandbox_targets.py"],
        cwd=REPO,
        check=True,
    )


def sweep(
    runs: int,
    until: str | None,
    results: pathlib.Path,
    manifest: pathlib.Path,
    logdir: pathlib.Path,
    batch_id: str,
    dry: bool,
    owner_pid: int,
) -> int:
    cutoff = batchlib.resolve_until(until) if until else None
    if os.environ.get(batchlib.FAKE_NOW):
        # A batch that voided (or did not) because of an injected clock records
        # an outcome whose cause is otherwise invisible.
        logger.info(
            "clock overridden: %s=%s", batchlib.FAKE_NOW, os.environ[batchlib.FAKE_NOW]
        )

    if dry:
        check_dry_run_paths(results, manifest)
    else:
        if provenance.code_is_dirty(REPO, untracked=False):
            raise batchlib.Void("the harness checkout is dirty; commit first")
        sync_targets()

    b = batchlib.Batch(
        repo=REPO,
        results=results,
        manifest=manifest,
        logdir=logdir,
        bench_logs=pathlib.Path(
            os.environ.get("BENCH_LOGS") or pathlib.Path.home() / "bench-logs"
        ),
        batch=batch_id,
        harness_head=provenance.head(REPO),
        backend=BACKEND,
        prefix=PREFIX,
    )
    logger.info("logs in:    %s", logdir)
    logger.info("rows in:    %s", results)
    logger.info("harness at: %s", b.harness_head)
    if not batchlib.leads_equally(runs):
        logger.warning(
            "runs=%d is not a multiple of 4, so the arms do not lead equally "
            "often and the position term does not cancel (#130, #201)",
            runs,
        )

    plan = batchlib.order(ARMS, runs)
    failed = 0
    # One loop, and --dry-run swaps only the per-run body. Two loops is how
    # the dry run came to skip the cutoff entirely while its own --help
    # promised "the cutoff and the arm order": a check that exists in the
    # branch nobody rehearses is a check nobody has ever seen fire.
    with contextlib.ExitStack() as stack:
        if not dry:
            stack.enter_context(
                batchlib.machine(
                    f"targets_ab.py (#146, {runs} runs)", owner_pid, preflight
                )
            )
            stack.enter_context(
                tool_shim.serving(
                    logdir / "shim.log", repo=REPO, port=SHIM_PORT, strip=True
                )
            )
        for run, arm in enumerate(plan, 1):
            if batchlib.past(cutoff):
                batchlib.void(manifest, run, until or "")
                raise batchlib.Void(
                    f"past {until} before run {run} of {runs} -- a partial "
                    "batch is no result"
                )
            if dry:
                logger.info("[dry] run %d, targets=%s", run, arm)
                batchlib.append(
                    manifest, {"run": run, "arm": arm, "dry": True, "batch": batch_id}
                )
                continue
            with ds4_server.serving(
                server_command(),
                logdir / f"ds4server-run{run}-{arm}.log",
                cwd=DS4_TREE,
                model_id=MODEL_ID,
                want_mtp=False,
                port=SERVER_PORT,
            ):
                if batchlib.run_one(b, run, arm, ["--targets", arm]) != 0:
                    failed += 1
    logger.info("batch complete; manifest: %s", manifest)
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", type=int, default=4)
    p.add_argument(
        "--until",
        default=None,
        help="HH:MM. A run that would start past it VOIDS the batch rather "
        "than truncating it: a partial batch is no result.",
    )
    p.add_argument("--results", type=pathlib.Path, default=DEFAULT_RESULTS)
    p.add_argument("--manifest", type=pathlib.Path, default=None)
    p.add_argument("--logdir", type=pathlib.Path, default=None)
    p.add_argument("--batch", default=None)
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("TARGETS_AB_DRY_RUN") == "1",
        help="exercise the batch loop -- the cutoff and the arm order -- with "
        "no machine: no dirty check, no sync, no lock, no shim, no server, no "
        "run.py. The measurement is not exercised; the loop is.",
    )
    args = p.parse_args(argv)

    logs.configure()

    import tempfile
    import time

    logdir = args.logdir or pathlib.Path(tempfile.mkdtemp(prefix="targets-ab-"))
    batch_id = args.batch or time.strftime("%m%d-%H%M")
    manifest = args.manifest or manifest_for(args.results)

    try:
        return sweep(
            args.runs,
            args.until,
            args.results,
            manifest,
            logdir,
            batch_id,
            args.dry_run,
            os.getpid(),
        )
    except (
        batchlib.Void,
        ValueError,
        tool_shim.WrongMode,
        tool_shim.NeverReady,
    ) as exc:
        logger.error("REFUSING: %s", exc)
        return 1
    except (
        ds4_server.ForeignServer,
        ds4_server.ServerNeverStarted,
        ds4_server.GraphMismatch,
        ds4_server.NotReady,
    ) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
