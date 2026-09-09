#!/usr/bin/env python3
"""Does echoing the shim's own scaffolding back carry the tool-call loop? #112

Port of `scripts/strip_toggle_ab.sh` (#235).

The shim removes bare `<tool_call>` tags from returned content when it has
recovered a call. That is the shipped behaviour and it has never been measured.
`SHIM_NO_STRIP=1` is the off arm and touches nothing else -- so the arm is the
**shim's mode**, not a flag on `run.py`, which is the one structural difference
from `targets_ab.py`.

Pre-registered on the issue before any run, repeated here so a reader of the
output does not have to go and find it:

    works:   with the strip removed, the conditional failure rate after >= 1
             prior tool error rises relative to the strip-on arm on the same
             protocol, at >= 8 failures per arm -- the echo carries the loop
             and the strip stays.
    nothing: the two arms are indistinguishable at >= 8 failures per arm --
             the echo is not the carrier; the strip is scaffolding hygiene,
             not a fix, and item 2 closes as measured-no-effect.
    no call: fewer than 8 failures per arm -- no claim either way.

**The outcome variable is out of reach and the proxy is not it.** Multi-turn
death rate sits near zero under this protocol and would need far more trials
per arm to resolve a drop. The conditional rate is a MECHANISM PROXY, and the
link from its slope to actual deaths has never been measured. Say so wherever
it is quoted.

    uv run python scripts/strip_toggle_ab.py --runs 4

Read out with `scripts/strip_ab_report.py`.

## Reading the arm back out of the shim

The strip is worth **23 points of pass rate** (#112). Setting `SHIM_NO_STRIP`
is not evidence the shim honoured it, and a shim in the other mode does not
fail -- it silently produces the other experiment. `tool_shim.serving` waits
for the shim's own `scaffolding strip: ON/OFF` line and refuses unless it says
what this arm asked for.
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
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
PREFIX = "112-strip"
ARMS = ("on", "off")
SHIM_PORT = 8101
SERVER_PORT = 8000
DEFAULT_RESULTS = REPO / "benchmarks" / "agent" / "results-112-strip-ab.jsonl"


def manifest_for(results: pathlib.Path) -> pathlib.Path:
    return results.with_name(results.stem + "-manifest.jsonl")


def strip_for(arm: str) -> bool:
    """The arm IS the shim's mode. `on` strips, `off` does not."""
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, not {arm!r}")
    return arm == "on"


def server_command() -> list[str]:
    return ds4_server.argv(
        DS4_MODEL,
        DS4_PLE,
        DS4_KV,
        binary=DS4_TREE / "ds4-server",
        port=SERVER_PORT,
    )


def sweep(
    runs: int,
    until: str | None,
    results: pathlib.Path,
    manifest: pathlib.Path,
    logdir: pathlib.Path,
    batch_id: str,
    owner_pid: int,
) -> int:
    cutoff = batchlib.resolve_until(until) if until else None
    if os.environ.get(batchlib.FAKE_NOW):
        logger.info(
            "clock overridden: %s=%s", batchlib.FAKE_NOW, os.environ[batchlib.FAKE_NOW]
        )
    if provenance.code_is_dirty(REPO, untracked=False):
        raise batchlib.Void("the harness checkout is dirty; commit first")

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

    failed = 0
    with batchlib.machine(
        f"strip_toggle_ab.py (#112 remedy 2, {runs} runs)", owner_pid, preflight
    ):
        for run, arm in enumerate(batchlib.order(ARMS, runs), 1):
            if batchlib.past(cutoff):
                batchlib.void(manifest, run, until or "")
                raise batchlib.Void(
                    f"past {until} before run {run} of {runs} -- a partial "
                    "batch is no result"
                )
            # The shim restarts per run because it IS the arm. The server
            # restarts per run because that is the #138 protocol both
            # experiments share.
            with (
                tool_shim.serving(
                    logdir / f"shim-{arm}.log",
                    repo=REPO,
                    port=SHIM_PORT,
                    strip=strip_for(arm),
                ),
                ds4_server.serving(
                    server_command(),
                    logdir / f"ds4server-run{run}-{arm}.log",
                    cwd=DS4_TREE,
                    model_id=MODEL_ID,
                    want_mtp=False,
                    port=SERVER_PORT,
                ),
            ):
                if batchlib.run_one(b, run, arm, utc=True) != 0:
                    failed += 1
    logger.info("batch complete; manifest: %s", manifest)
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", type=int, default=4)
    p.add_argument("--until", default=None)
    p.add_argument("--results", type=pathlib.Path, default=DEFAULT_RESULTS)
    p.add_argument("--manifest", type=pathlib.Path, default=None)
    p.add_argument("--logdir", type=pathlib.Path, default=None)
    p.add_argument("--batch", default=None)
    args = p.parse_args(argv)

    logs.configure()

    import tempfile
    import time

    logdir = args.logdir or pathlib.Path(tempfile.mkdtemp(prefix="strip-toggle-ab-"))
    try:
        return sweep(
            args.runs,
            args.until,
            args.results,
            args.manifest or manifest_for(args.results),
            logdir,
            args.batch or time.strftime("%m%d-%H%M"),
            os.getpid(),
        )
    except (
        batchlib.Void,
        ValueError,
        tool_shim.WrongMode,
        tool_shim.NeverReady,
        ds4_server.ForeignServer,
        ds4_server.ServerNeverStarted,
        ds4_server.GraphMismatch,
        ds4_server.NotReady,
    ) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
