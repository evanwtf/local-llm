#!/usr/bin/env python3
"""Paired decode-rate A/B for two ENGINE BUILDS of the same GGUF. #118

Port of `scripts/decode_ab_engine.sh` (#235).

`decode_ab.py` varies the weights; this varies the tree. It is the
reproduction shape of ds4 PR #964, which measured main / branch / branch /
main with each side built in its own worktree so each reads its own
`metal/*.metal` at runtime -- two builds in one tree would silently share
shaders, and the A/B would compare a build against itself.

    uv run python scripts/decode_ab_engine.py \\
        <label-a> <tree-a> <label-b> <tree-b> <gguf> [outdir]

## What must survive the port

**The per-arm log, which is not a convenience.** ds4 prints its Metal route,
its drift-patch flag set and its pipeline fallbacks to stderr at startup, and
that output is the only evidence the two trees ran DIFFERENT code. Interleaved
in one batch log it cannot be diffed; per arm it can. This matters most when
an engine A/B comes out flat: "the change is a wash" and "the new kernels were
never selected" produce the same CSV, and only the startup diagnostics
separate them.

**OUT is absolutized before the lock (#203).** Each arm runs with `cwd=tree`,
so a relative OUT is created under the repo by `mkdir` and resolved under the
ds4 tree by `--csv` -- the CSV files land nowhere. The default OUT is
absolute, which is why the bug only ever showed on a hand-passed relative
path.

**Both binaries are checked before the lock.** A missing build used to fail
mid-run, with the lock held and the model resident. A typo should cost a
second, not a model load.

**An odd rep count is refused (#201).** Alternation cancels the position bias
only on an even count: at REPS=3 reps 1 and 3 run A-first and only rep 2 runs
B-first, so the bias lands 2:1 on one arm. Across #171's twelve reps whichever
arm ran first was faster in 9, median +0.9%, and +5.9% on the first rep of a
cold session. It is a refusal rather than a warning because the failure is
silent: an odd sweep produces a complete CSV and a plausible number.

**The prompt comes from tree A for both arms**, so it is byte-identical across
the A/B even when the branch touches `speed-bench/`.

The `PREFILL_CHUNK` reasoning -- why 0 is refused and why above 8192 only the
first frontier honours the flag -- lives in `decode_ab.prefill_chunk`, which
this calls rather than restates.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import pathlib
import sys
from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import ab_driver
import child
import decode_ab

import logs

logger = logging.getLogger(__name__)

DEFAULT_OUT = REPO / "benchmarks" / "ds4" / "decode-ab-964"

#: Same frontiers and gen budget as decode_ab and ds4's own speed-bench, so
#: the numbers stay comparable to speed-bench/m5_max.csv and to #91's #621 A/B.
CTX_START = 2048
CTX_MAX = 16384
STEP = 2048
GEN = 128
REPS = 4


def prompt_for(tree_a: pathlib.Path) -> pathlib.Path:
    """Tree A's corpus, for BOTH arms.

    Taking each arm's prompt from its own tree would let a branch that touches
    `speed-bench/` change the input as well as the engine, and the A/B would
    no longer be about the engine.
    """
    return tree_a / "speed-bench" / "promessi_sposi.txt"


def check_binaries(trees: Sequence[pathlib.Path]) -> None:
    """Refuse a missing build before the lock, not minutes into it."""
    for tree in trees:
        binary = tree / "ds4-bench"
        if not (binary.exists() and os.access(binary, os.X_OK)):
            raise decode_ab.Refusal(
                f"{binary} is missing or not executable -- build it first"
            )


def engines_text(
    arms: Sequence[tuple[str, pathlib.Path]],
    gguf: pathlib.Path,
    prompt: pathlib.Path,
    *,
    ctx_start: int,
    ctx_max: int,
    step: int,
    gen: int,
    reps: int,
    chunk: int | None,
) -> str:
    """Both trees' commits, beside the CSVs.

    #118's arm shas (b0a147a and 8969dbb) live only in issue prose, so
    re-running it a month later means trusting a sentence. An engine A/B whose
    rows cannot say which commits produced them is the gap #137 found on the
    client side and #138 found on the engine side.
    """
    when = (
        datetime.datetime.now(datetime.UTC).astimezone().isoformat(timespec="seconds")
    )
    lines = [f"# engine A/B, {when}"]
    for position, (label, tree) in enumerate(arms):
        head = decode_ab.git_out(tree, "rev-parse", "--short", "HEAD") or "unknown"
        lines.append(f"{'AB'[position]} label={label} tree={tree} @ {head}")
        if decode_ab.git_out(tree, "status", "--porcelain"):
            lines.append(
                f"{'AB'[position]}_dirty=true   # uncommitted code: "
                "the sha does not name this binary"
            )
    lines.append(f"gguf={gguf}")
    lines.append(f"prompt={prompt}")
    # The sweep is an input to every ratio in the CSVs and was recorded
    # nowhere. On 2026-09-06 a comparison against a published 32-frontier
    # baseline ran on this script's 8-frontier default and read 1.152 against
    # the published 1.155 -- a confirmatory artifact of sampling only the
    # steep region, with nothing beside the rows saying the sweep differed.
    lines.append(
        f"sweep ctx_start={ctx_start} ctx_max={ctx_max} step={step} "
        f"gen={gen} reps={reps}"
    )
    lines.append(f"prefill_chunk={chunk if chunk is not None else '<flag absent>'}")
    # These set the same caps as the flags, but only when the flags are
    # absent, so an inherited value would silently change the prefill shape of
    # a run that never mentions it.
    for var in ("DS4_METAL_PREFILL_CHUNK", "DS4_METAL_GRAPH_RAW_CAP"):
        lines.append(f"{var}={os.environ.get(var, '<unset>')}")
    return "\n".join(lines) + "\n"


def sweep(
    arms_in: Sequence[tuple[str, pathlib.Path]],
    gguf: pathlib.Path,
    reps: int,
    out: pathlib.Path,
    *,
    prompt: pathlib.Path | None = None,
    ctx_start: int = CTX_START,
    ctx_max: int = CTX_MAX,
    step: int = STEP,
    gen: int = GEN,
    chunk: int | None = None,
    owner_pid: int,
    allow_odd: bool = False,
) -> int:
    """The whole sweep. Returns a process exit code."""
    # Refuse an uneven rep count HERE, before the lock and before any build
    # check. `ab_driver.run` refuses it too, but it is called inside the lock,
    # and a refusal after the machine is claimed is a refusal that already
    # cost something. The shell checked it at line 56, a hundred lines before
    # it took the lock, and that ordering is part of the behavior.
    if not ab_driver.leads_equally(reps, len(arms_in)) and not allow_odd:
        raise decode_ab.Refusal(
            f"reps={reps} over {len(arms_in)} arms does not let each arm lead "
            "equally often, so alternation cannot cancel the position bias "
            "(#130, #201) -- up to +5.9% on a cold first rep, larger than most "
            "effects this measures. Use an even count, or --allow-odd-reps."
        )
    trees = [tree for _, tree in arms_in]
    check_binaries(trees)
    prompt = prompt or prompt_for(trees[0])
    out.mkdir(parents=True, exist_ok=True)

    decode_ab.stamp_prompt(prompt, sidecar=out)
    text = engines_text(
        arms_in,
        gguf,
        prompt,
        ctx_start=ctx_start,
        ctx_max=ctx_max,
        step=step,
        gen=gen,
        reps=reps,
        chunk=chunk,
    )
    with (out / "engines.txt").open("a") as handle:
        handle.write(text)
    logger.info("%s", text.rstrip())

    built = [
        ab_driver.Arm(name=label, backend=str(tree), serve=ab_driver.nothing)
        for label, tree in arms_in
    ]

    def one_arm(arm: ab_driver.Arm, tag: str, rep: int) -> int:
        tree = pathlib.Path(arm.backend)
        csv = out / f"{arm.name}-rep{rep}.csv"
        log = out / f"{arm.name}-rep{rep}.log"
        position = [a.name for a in ab_driver.order(built, rep)].index(arm.name) + 1
        with (out / "run-order.txt").open("a") as handle:
            handle.write(
                f"rep={rep} position={position} of {len(built)} label={arm.name}\n"
            )
        logger.info("%s rep %d (position %d) -> %s", arm.name, rep, position, csv)
        # cwd=tree: ds4-bench resolves metal/*.metal relative to its own tree,
        # and running both arms from one tree is how an engine A/B silently
        # compares a build against itself.
        rc = child.run(
            decode_ab.bench_argv(
                gguf,
                csv,
                prompt,
                binary=tree / "ds4-bench",
                ctx_start=ctx_start,
                ctx_max=ctx_max,
                step=step,
                gen=gen,
                chunk=chunk,
            ),
            cwd=tree,
            log=log,
        )
        if rc != 0:
            # The failure text is in the log, not on stdout, so say where it
            # went and show the tail rather than dying silently.
            logger.error("FAILED: %s rep %d -- see %s", arm.name, rep, log)
            for line in log.read_text(errors="replace").splitlines()[-20:]:
                logger.error("  %s", line)
            return rc
        # Stamp immediately, not at the end: a run that dies halfway still
        # leaves CSVs, and an unstamped one cannot be told from a
        # differently-prompted one (#140).
        decode_ab.stamp_prompt(prompt, stamp=csv)
        return rc

    with decode_ab.run_lock(
        f"decode_ab_engine.py {built[0].name} vs {built[1].name}", owner_pid
    ):
        failed = ab_driver.run(built, reps, one_arm, allow_uneven=allow_odd)
    logger.info("done: %s", out)
    return ab_driver.report(failed, reps, len(built))


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("label_a")
    p.add_argument("tree_a", type=pathlib.Path)
    p.add_argument("label_b")
    p.add_argument("tree_b", type=pathlib.Path)
    p.add_argument("gguf", type=pathlib.Path)
    p.add_argument("out", nargs="?", type=pathlib.Path, default=DEFAULT_OUT)
    p.add_argument("--reps", type=int, default=REPS)
    p.add_argument("--ctx-start", type=int, default=CTX_START)
    p.add_argument("--ctx-max", type=int, default=CTX_MAX)
    p.add_argument("--step", type=int, default=STEP)
    p.add_argument("--gen", type=int, default=GEN)
    p.add_argument("--prompt", type=pathlib.Path, default=None)
    p.add_argument(
        "--prefill-chunk",
        default=os.environ.get("PREFILL_CHUNK"),
        help="cold large-chunk prefill (#171); absent by default",
    )
    p.add_argument(
        "--allow-odd-reps",
        action="store_true",
        default=os.environ.get("ALLOW_ODD_REPS") == "1",
        help="run an odd rep count anyway; the position bias will not cancel",
    )
    args = p.parse_args(argv)
    logs.configure()

    try:
        chunk = decode_ab.prefill_chunk(args.prefill_chunk)
        return sweep(
            [(args.label_a, args.tree_a), (args.label_b, args.tree_b)],
            args.gguf,
            args.reps,
            # #203: absolutize before anything else, because each arm runs
            # with cwd=tree and a relative path resolves under the ds4 tree.
            args.out.resolve(),
            prompt=args.prompt,
            ctx_start=args.ctx_start,
            ctx_max=args.ctx_max,
            step=args.step,
            gen=args.gen,
            chunk=chunk,
            owner_pid=os.getpid(),
            allow_odd=args.allow_odd_reps,
        )
    except decode_ab.Refusal as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
