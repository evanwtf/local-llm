#!/usr/bin/env python3
"""Paired A/B for one ds4-bench --prefill-chunk value within one tree. #267

`decode_ab` varies the weights and `decode_ab_engine` varies the tree. Each of
those hands an arm a different GGUF or a different binary, so "the two arms
differ" is legible in the argv. This driver varies neither: both arms are the
SAME build in the SAME tree and differ only in the `--prefill-chunk` value.

That is deliberate, and it is also why the failure is silent. @iammac2 found on
ds4#952 that the "enable Q4 prefill at 8K" work only shows with
`--prefill-chunk 8192` -- at 4096 the 8192-token batch is declined at the 4096
limit and nothing changes. The Metal counterpart of that measurement is this
one: one head, chunk 8192 against chunk 4096. Nothing in the corpus varied the
prefill chunk within one build, so this adds it.

    uv run python scripts/prefill_chunk_ab.py <tree> <gguf> 8192 4096 [out]

## The one bug this must not have

If `one_arm` reads a single shared chunk instead of the arm's own, both arms
run the same value, the CSVs are ordinary, the ratio is plausible, and nothing
says the A/B compared a build against itself. `metal_knob_ab` documents the
same trap for a default-on knob whose off arm is `=0`. `test_prefill_chunk_ab`
opens with the guard against it, and two equal chunks are refused outright.

## Same tree is correct here

The engine A/B refuses two arms in one tree because ds4-bench resolves
`metal/*.metal` relative to its cwd, so one tree means one set of shaders and a
build compared against itself. Here that is the point: the shaders are meant to
be identical and only the runtime flag differs. Both arms therefore run
`cwd=tree` on the same binary, and a test pins that so the engine A/B's rule is
not copied in by mistake.
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import re
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

DEFAULT_OUT = REPO / "benchmarks" / "ds4" / "prefill-chunk-267"

#: The frontiers @iammac2 used on ds4#952, so the Metal numbers sit beside the
#: ROCm ones: 8k..32k in 8k steps. gen 128 matches decode_ab and speed-bench.
CTX_START = 8192
CTX_MAX = 32768
STEP = 8192
GEN = 128
REPS = 4

#: ds4-bench prints the cap it actually used on the context-buffers line.
_CAP = re.compile(r"prefill_chunk=(\d+)")


def effective_chunk(log_text: str) -> int | None:
    """The prefill chunk ds4 actually used, read from its own log.

    The requested value is what we passed; this is what took effect. A run
    where the two arms end with the same effective cap did not vary anything,
    however different the requests looked.
    """
    match = _CAP.search(log_text)
    return int(match.group(1)) if match else None


def prompt_for(tree: pathlib.Path) -> pathlib.Path:
    return tree / "speed-bench" / "promessi_sposi.txt"


def check_binary(tree: pathlib.Path) -> None:
    """Refuse a missing build before the lock, not minutes into it."""
    binary = tree / "ds4-bench"
    if not (binary.exists() and os.access(binary, os.X_OK)):
        raise decode_ab.Refusal(
            f"{binary} is missing or not executable -- build it first"
        )


def header_text(
    tree: pathlib.Path,
    gguf: pathlib.Path,
    prompt: pathlib.Path,
    arms_in: Sequence[tuple[str, int]],
    *,
    ctx_start: int,
    ctx_max: int,
    step: int,
    gen: int,
    reps: int,
) -> str:
    """One tree, one GGUF, the two chunks, beside the CSVs. The chunk is the
    only thing that differs between arms, so it is the one thing a re-reader
    most needs and the CSVs do not carry."""
    head = decode_ab.git_out(tree, "rev-parse", "--short", "HEAD") or "unknown"
    lines = [f"# prefill-chunk A/B, one tree @ {head}"]
    lines.append(f"tree={tree}")
    if decode_ab.git_out(tree, "status", "--porcelain"):
        lines.append(
            "dirty=true   # uncommitted code: the sha does not name this binary"
        )
    lines.append(f"gguf={gguf}")
    lines.append(f"prompt={prompt}")
    for label, chunk in arms_in:
        lines.append(f"arm {label} --prefill-chunk {chunk}")
    lines.append(
        f"sweep ctx_start={ctx_start} ctx_max={ctx_max} step={step} "
        f"gen={gen} reps={reps}"
    )
    for var in ("DS4_METAL_PREFILL_CHUNK", "DS4_METAL_GRAPH_RAW_CAP"):
        lines.append(f"{var}={os.environ.get(var, '<unset>')}")
    return "\n".join(lines) + "\n"


def sweep(
    tree: pathlib.Path,
    gguf: pathlib.Path,
    arms_in: Sequence[tuple[str, int]],
    reps: int,
    out: pathlib.Path,
    *,
    prompt: pathlib.Path | None = None,
    ctx_start: int = CTX_START,
    ctx_max: int = CTX_MAX,
    step: int = STEP,
    gen: int = GEN,
    owner_pid: int,
    allow_odd: bool = False,
) -> int:
    """The whole sweep. Returns a process exit code."""
    chunk_by_name = {label: chunk for label, chunk in arms_in}
    if len(chunk_by_name) != len(arms_in):
        raise decode_ab.Refusal("two arms cannot share a label")
    # Two equal chunks are two arms wearing different labels: the same trap
    # metal_knob_ab refuses for a default-on knob whose off arm is =0.
    if len({chunk for _, chunk in arms_in}) < len(arms_in):
        raise decode_ab.Refusal(
            f"the arms request the same chunk {sorted(chunk_by_name.values())} "
            "-- that is one build compared against itself, not an A/B"
        )
    # Refuse an uneven rep count HERE, before the lock and before the build
    # check, so a refusal never costs a claimed machine (decode_ab_engine does
    # the same, and for the same reason).
    if not ab_driver.leads_equally(reps, len(arms_in)) and not allow_odd:
        raise decode_ab.Refusal(
            f"reps={reps} over {len(arms_in)} arms does not let each arm lead "
            "equally often, so alternation cannot cancel the position bias "
            "(#130, #201). Use an even count, or --allow-odd-reps."
        )
    check_binary(tree)
    prompt = prompt or prompt_for(tree)
    out.mkdir(parents=True, exist_ok=True)

    decode_ab.stamp_prompt(prompt, sidecar=out)
    text = header_text(
        tree,
        gguf,
        prompt,
        arms_in,
        ctx_start=ctx_start,
        ctx_max=ctx_max,
        step=step,
        gen=gen,
        reps=reps,
    )
    with (out / "arms.txt").open("a") as handle:
        handle.write(text)
    logger.info("%s", text.rstrip())

    built = [
        ab_driver.Arm(name=label, backend=str(tree), serve=ab_driver.nothing)
        for label, _ in arms_in
    ]
    effective: dict[str, int | None] = {}

    def one_arm(arm: ab_driver.Arm, tag: str, rep: int) -> int:
        csv = out / f"{arm.name}-rep{rep}.csv"
        log = out / f"{arm.name}-rep{rep}.log"
        # The arm's OWN chunk. A single shared value here is the one bug the
        # test file opens against.
        chunk = chunk_by_name[arm.name]
        position = [a.name for a in ab_driver.order(built, rep)].index(arm.name) + 1
        with (out / "run-order.txt").open("a") as handle:
            handle.write(
                f"rep={rep} position={position} of {len(built)} "
                f"label={arm.name} chunk={chunk}\n"
            )
        logger.info(
            "%s rep %d (position %d, chunk %d) -> %s",
            arm.name,
            rep,
            position,
            chunk,
            csv,
        )
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
            logger.error("FAILED: %s rep %d -- see %s", arm.name, rep, log)
            for line in log.read_text(errors="replace").splitlines()[-20:]:
                logger.error("  %s", line)
            return rc
        decode_ab.stamp_prompt(prompt, stamp=csv)
        effective[arm.name] = effective_chunk(log.read_text(errors="replace"))
        return rc

    with decode_ab.run_lock(
        f"prefill_chunk_ab.py {built[0].name} vs {built[1].name}", owner_pid
    ):
        failed = ab_driver.run(built, reps, one_arm, allow_uneven=allow_odd)

    # Admission: if the effective caps did not differ, the flag did not bite
    # and the ratio is a null by construction, not a measurement. Warn loudly;
    # do not refuse, because the clamp rules are the engine's, not ours.
    caps = {name: c for name, c in effective.items() if c is not None}
    if len(caps) == len(built) and len(set(caps.values())) < len(caps):
        logger.warning(
            "ADMISSION: both arms ran the same effective prefill chunk %s -- "
            "the --prefill-chunk flag did not change the cap, so any ratio is a "
            "null by construction, not a measurement.",
            caps,
        )
    else:
        logger.info("admission: effective prefill chunks %s", effective)
    logger.info("done: %s", out)
    return ab_driver.report(failed, reps, len(built))


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tree", type=pathlib.Path)
    p.add_argument("gguf", type=pathlib.Path)
    p.add_argument("chunk_a", type=int)
    p.add_argument("chunk_b", type=int)
    p.add_argument("out", nargs="?", type=pathlib.Path, default=DEFAULT_OUT)
    p.add_argument("--reps", type=int, default=REPS)
    p.add_argument("--ctx-start", type=int, default=CTX_START)
    p.add_argument("--ctx-max", type=int, default=CTX_MAX)
    p.add_argument("--step", type=int, default=STEP)
    p.add_argument("--gen", type=int, default=GEN)
    p.add_argument("--prompt", type=pathlib.Path, default=None)
    p.add_argument(
        "--allow-odd-reps",
        action="store_true",
        default=os.environ.get("ALLOW_ODD_REPS") == "1",
        help="run an odd rep count anyway; the position bias will not cancel",
    )
    args = p.parse_args(argv)
    logs.configure()

    arms_in = [
        (f"chunk{args.chunk_a}", args.chunk_a),
        (f"chunk{args.chunk_b}", args.chunk_b),
    ]
    try:
        return sweep(
            args.tree,
            args.gguf,
            arms_in,
            args.reps,
            # #203: absolutize before anything, because each arm runs cwd=tree
            # and a relative out resolves under the ds4 tree.
            args.out.resolve(),
            prompt=args.prompt,
            ctx_start=args.ctx_start,
            ctx_max=args.ctx_max,
            step=args.step,
            gen=args.gen,
            owner_pid=os.getpid(),
            allow_odd=args.allow_odd_reps,
        )
    except decode_ab.Refusal as refusal:
        logger.error("%s", refusal)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
