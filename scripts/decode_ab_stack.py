#!/usr/bin/env python3
"""Paired decode-rate A/B for two whole STACKS -- engine tree and weights together. #138

Port of `scripts/decode_ab_stack.sh` (#235).

`decode_ab.py` varies the weights and `decode_ab_engine.py` varies the tree.
This is the honest third shape: each arm carries its own engine, weights and
PLE sidecar, because no single binary loads both GGUFs. Measured 2026-09-04:

      ds4-metal ba01f5d          old Q4_0 loads      new Q4_K "deepseek4.block_count missing"
      ivan qwen3.8-flash-next    old Q4_0 same error new Q4_K loads, coherent

So `decode_ab.sh` (two GGUFs, one engine) and `decode_ab_engine.sh` (two
engines, one GGUF) can neither of them express this comparison.

**The result is a two-variable comparison and must always be reported as
one.** The quant and the engine move together and nothing here can separate
them. Neither half can be attributed on its own (#138).

Both Qwen3.8-Flash-Next ds4 builds keep the 51B-value PLE n-gram table in an
external sidecar, so every arm that uses one passes `--ple`. The flag is real
but undocumented, absent from `--help` and present in the parser at
ds4_bench.c:275 at ds4-main 9ab70534.

    uv run python scripts/decode_ab_stack.py <label-a> <tree-a> <gguf-a> <ple-a> \\
        <label-b> <tree-b> <gguf-b> <ple-b> [outdir]

Pass "-" for a PLE sidecar an arm does not use.

## What must survive the port

**An odd rep count is refused before the lock and before the build check.**
Alternation cancels the position bias only on an even count (#201). The shell
refused it at line 63, a hundred lines before it took the lock; a refusal
after the machine is claimed already cost something.

**OUT is absolutized before the lock (#203).** Each arm runs with `cwd=tree`,
so a relative OUT is created under the repo by `mkdir` and resolved under the
ds4 tree by `--csv` -- the CSV files land nowhere.

**The per-arm log is not a convenience.** ds4 prints its Metal route and
pipeline fallbacks to stderr at startup, and interleaved in one stream those
cannot be diffed between arms. The shell sent every arm's stderr to one batch
log; this port gives each arm its own (the same divergence decode_ab.py made).
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

DEFAULT_OUT = REPO / "benchmarks" / "ds4" / "stack-ab"

#: Same frontiers and gen budget as decode_ab and ds4's own speed-bench, so
#: the numbers stay comparable to speed-bench/m5_max.csv.
CTX_START = 2048
CTX_MAX = 16384
STEP = 2048
GEN = 128
REPS = 4

PASS = "-"  # the shell's spelling for "this arm has no PLE sidecar"


def check_binaries(trees: Sequence[pathlib.Path]) -> None:
    """Refuse a missing build before the lock, not minutes into it."""
    for tree in trees:
        binary = tree / "ds4-bench"
        if not (binary.exists() and os.access(binary, os.X_OK)):
            raise decode_ab.Refusal(
                f"{binary} is missing or not executable -- build it first"
            )


def stacks_text(
    arms: Sequence[tuple[str, pathlib.Path, pathlib.Path, str]],
    *,
    prompt: pathlib.Path,
    ctx_start: int,
    ctx_max: int,
    step: int,
    gen: int,
    reps: int,
) -> str:
    """Which engine tree, weights and PLE produced each arm, beside the CSVs.

    A stack A/B whose rows do not say which engine produced them is unreadable
    a week later, and this is the one shape where the engine is part of the
    arm rather than held constant.
    """
    when = (
        datetime.datetime.now(datetime.UTC).astimezone().isoformat(timespec="seconds")
    )
    lines = [f"# stack A/B, {when}"]
    for position, (label, tree, gguf, ple) in enumerate(arms):
        head = decode_ab.git_out(tree, "rev-parse", "--short", "HEAD") or "unknown"
        lines.append(f"{'AB'[position]} label={label} tree={tree} @ {head}")
        if decode_ab.git_out(tree, "status", "--porcelain"):
            lines.append(
                f"{'AB'[position]}_dirty=true   # uncommitted code: "
                "the sha does not name this binary"
            )
        size = gguf.stat().st_size if gguf.exists() else None
        lines.append(f"{'AB'[position]} gguf={gguf} ({size or '?'} bytes)")
        lines.append(f"{'AB'[position]} ple={ple}")
    lines.append("# TWO VARIABLES: engine and weights move together. Report as a stack")
    lines.append("# comparison; neither half can be attributed on its own (#138).")
    lines.append(f"prompt={prompt}")
    lines.append(
        f"sweep ctx_start={ctx_start} ctx_max={ctx_max} step={step} "
        f"gen={gen} reps={reps}"
    )
    return "\n".join(lines) + "\n"


def sweep(
    arms_in: Sequence[tuple[str, pathlib.Path, pathlib.Path, str]],
    reps: int,
    out: pathlib.Path,
    prompt: pathlib.Path,
    *,
    ctx_start: int = CTX_START,
    ctx_max: int = CTX_MAX,
    step: int = STEP,
    gen: int = GEN,
    owner_pid: int,
    allow_odd: bool = False,
) -> int:
    """The whole sweep. Returns a process exit code."""
    # Refuse an uneven rep count HERE, before the lock and before any build
    # check. `ab_driver.run` refuses it too, but it is called inside the lock,
    # and a refusal after the machine is claimed is a refusal that already
    # cost something. The shell checked it at line 63, before it took the lock.
    if not ab_driver.leads_equally(reps, len(arms_in)) and not allow_odd:
        raise decode_ab.Refusal(
            f"reps={reps} over {len(arms_in)} arms does not let each arm lead "
            "equally often, so alternation cannot cancel the position bias "
            "(#130, #201) -- up to +5.9% on a cold first rep, larger than most "
            "effects this measures. Use an even count, or --allow-odd-reps."
        )
    check_binaries([tree for _, tree, _, _ in arms_in])
    out.mkdir(parents=True, exist_ok=True)

    decode_ab.stamp_prompt(prompt, sidecar=out)
    text = stacks_text(
        arms_in,
        prompt=prompt,
        ctx_start=ctx_start,
        ctx_max=ctx_max,
        step=step,
        gen=gen,
        reps=reps,
    )
    with (out / "stacks.txt").open("a") as handle:
        handle.write(text)
    logger.info("%s", text.rstrip())

    built = [
        ab_driver.Arm(name=label, backend=str(tree), serve=ab_driver.nothing)
        for label, tree, _, _ in arms_in
    ]
    gguf_of = {label: gguf for label, _, gguf, _ in arms_in}
    ple_of = {label: ple for label, _, _, ple in arms_in}

    def one_arm(arm: ab_driver.Arm, tag: str, rep: int) -> int:
        tree = pathlib.Path(arm.backend)
        csv = out / f"{arm.name}-rep{rep}.csv"
        log = out / f"{arm.name}-rep{rep}.log"
        position = [a.name for a in ab_driver.order(built, rep)].index(arm.name) + 1
        # Record the order this arm ran in, so positional bias can be tested
        # for instead of assumed away (#130 item 3).
        with (out / "run-order.txt").open("a") as handle:
            handle.write(
                f"rep={rep} position={position} of {len(built)} label={arm.name}\n"
            )
        logger.info("%s rep %d (position %d) -> %s", arm.name, rep, position, csv)
        argv = decode_ab.bench_argv(
            gguf_of[arm.name],
            csv,
            prompt,
            binary=tree / "ds4-bench",
            ctx_start=ctx_start,
            ctx_max=ctx_max,
            step=step,
            gen=gen,
        )
        ple = ple_of[arm.name]
        if ple != PASS:
            # bench_argv has no ple slot; the flag is real but undocumented,
            # so this appends it rather than assuming every arm has a sidecar.
            argv += ["--ple", str(ple)]
        # child.run, not subprocess.run: a driver stopped mid-sweep must take
        # ds4-bench with it (#268). cwd=tree: ds4-bench resolves metal/*.metal
        # relative to its own tree, so running both arms from one tree would
        # silently compare a build against itself (#118).
        rc = child.run(argv, cwd=tree, log=log)
        if rc != 0:
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
        f"decode_ab_stack.py {built[0].name} vs {built[1].name}", owner_pid
    ):
        failed = ab_driver.run(built, reps, one_arm, allow_uneven=allow_odd)
    logger.info("done: %s", out)
    return ab_driver.report(failed, reps, len(built))


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("label_a")
    p.add_argument("tree_a", type=pathlib.Path)
    p.add_argument("gguf_a", type=pathlib.Path)
    p.add_argument("ple_a")
    p.add_argument("label_b")
    p.add_argument("tree_b", type=pathlib.Path)
    p.add_argument("gguf_b", type=pathlib.Path)
    p.add_argument("ple_b")
    p.add_argument("out", nargs="?", type=pathlib.Path, default=DEFAULT_OUT)
    p.add_argument("--reps", type=int, default=int(os.environ.get("REPS", REPS)))
    p.add_argument(
        "--ctx-start", type=int, default=int(os.environ.get("CTX_START", CTX_START))
    )
    p.add_argument(
        "--ctx-max", type=int, default=int(os.environ.get("CTX_MAX", CTX_MAX))
    )
    p.add_argument("--step", type=int, default=int(os.environ.get("STEP", STEP)))
    p.add_argument("--gen", type=int, default=int(os.environ.get("GEN", GEN)))
    p.add_argument("--prompt", type=pathlib.Path, default=None)
    p.add_argument(
        "--allow-odd-reps",
        action="store_true",
        default=os.environ.get("ALLOW_ODD_REPS") == "1",
    )
    args = p.parse_args(argv)

    logs.configure()

    prompt = args.prompt or pathlib.Path(
        os.environ.get(
            "PROMPT",
            pathlib.Path.home() / "git" / "ds4" / "speed-bench" / "promessi_sposi.txt",
        )
    )
    try:
        return sweep(
            [
                (args.label_a, args.tree_a, args.gguf_a, args.ple_a),
                (args.label_b, args.tree_b, args.gguf_b, args.ple_b),
            ],
            args.reps,
            # #203: absolutize before the lock, because each arm runs with
            # cwd=tree and a relative path resolves under the ds4 tree.
            args.out.resolve(),
            prompt,
            ctx_start=args.ctx_start,
            ctx_max=args.ctx_max,
            step=args.step,
            gen=args.gen,
            owner_pid=os.getpid(),
            allow_odd=args.allow_odd_reps,
        )
    except (decode_ab.Refusal, ValueError) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
