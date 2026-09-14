"""Paired prefill/decode A/B for the ds4 MoE tensor-tile level, one tree. #328

`DS4_QWEN4_MOE_MM_NAX` selects the routed-expert matmul tiles on the Metal
tensor ops: unset/`2` is the promoted level-2 default (uncompensated 64-token
half tiles), `5` the compensated tiles (best NLL, ~half the gain), `0` the
simdgroup kill switch (ds4_metal.m:49277 at ds4-pr991 ccea7688). @ivanfioravanti
claims +40% prefill for Qwen3.8-Flash-Next Q4/Q2 from promoting level 2; this
measures the gain on this M5 Max and pairs it with the #149 drift it trades for.

Neither existing driver fits: `metal_knob_ab.py` is a decode-only on/off knob
(its off arm is `0` or a DISABLE var, but MM_NAX's baseline is a nonzero
default), and `decode_ab_engine.py` varies the tree, not the environment, and
passes no `--ple` sidecar. So this varies one env var between two arms of the
SAME tree and GGUF, like `metal_knob_ab.py`, but as a value knob, with the PLE
sidecar Qwen3.8-Flash-Next needs, and with `--gen 0` for a cold prefill.

    uv run python scripts/moe_tile_ab.py <tree> <gguf> --ple <sidecar> \\
        --a-value 2 --b-value 5 --gen 0 --ctx-start 8192 --ctx-max 8192

## The admission signal is the arithmetic, because there is no trace

MM_NAX has no eligibility gate for the qwen4exp expert matmul (types 12/39/16/
10): the level -> kernel mapping is deterministic and always applies, unlike
the opt-in knobs `metal_knob_ab.py` guards, which can request a path the engine
never selects. And ds4 prints no line naming the chosen level. So the proof the
two arms ran DIFFERENT code is that they produce different arithmetic: a short
greedy generation (temp 0) differs between the arms. Identical text means the
knob did nothing -- the tight-meaningless result -- and the run is refused. The
two greedy outputs are kept as the #149 drift evidence the same run needs.

## What is inherited from the other drivers, and why

**An even rep count is refused, not warned** (`ab_driver.leads_equally`).
Alternation cancels the position bias only on an even count; an odd sweep
produces a complete CSV and a plausible, biased number (#130, #201).

**OUT is absolutized before the lock** (#203): each arm runs with `cwd=TREE`,
so a relative `--csv` resolves under the ds4 tree and the rows land nowhere.

**The prompt comes from tree A for both arms**, byte-identical, so a branch
that touches `speed-bench/` cannot change the input as well as the tiles.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
import time
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

KNOB = "DS4_QWEN4_MOE_MM_NAX"
DEFAULT_OUT = REPO / "benchmarks" / "ds4" / "moe-tile-ab"

CTX_START = 8192
CTX_MAX = 8192
STEP = 2048
GEN = 0
REPS = 4
#: A short greedy pass to prove the two levels differ arithmetically. A fixed
#: inline prompt, not the corpus: the probe only needs a deterministic
#: continuation, and a short prompt avoids any context-length handling.
PROBE_TOKENS = 48
PROBE_PROMPT = (
    "Write an iterative Fibonacci function in Python, then explain it in one sentence."
)


class Refusing(RuntimeError):
    """A wrong arm or a no-op knob, refused before or without publishing."""


def prompt_for(tree: pathlib.Path) -> pathlib.Path:
    """Tree's own corpus, for both arms; byte-identical across the A/B."""
    return tree / "speed-bench" / "promessi_sposi.txt"


def bench_argv(
    gguf: pathlib.Path,
    prompt: pathlib.Path,
    csv: pathlib.Path,
    ple: pathlib.Path | None,
    *,
    ctx_start: int,
    ctx_max: int,
    step: int,
    gen: int,
) -> list[str]:
    """`ds4-bench` for one arm. Relative binary: it resolves `metal/*.metal`
    against its own tree, so the arm runs with `cwd=TREE`."""
    argv = ["./ds4-bench", "-m", str(gguf)]
    if ple is not None:
        argv += ["--ple", str(ple)]
    argv += [
        "--metal",
        "--prompt-file",
        str(prompt),
        "--ctx-start",
        str(ctx_start),
        "--ctx-max",
        str(ctx_max),
        "--step-incr",
        str(step),
        "--gen-tokens",
        str(gen),
        "--csv",
        str(csv),
    ]
    return argv


def probe_argv(gguf: pathlib.Path, ple: pathlib.Path | None) -> list[str]:
    """`ds4` (the CLI, not the bench) for a short greedy generation at temp 0.

    The bench emits only timings; the admission signal needs the generated
    text, so the probe uses the CLI. Greedy (temp 0) makes the comparison
    deterministic: any difference between the two arms is the tile arithmetic,
    not sampling.
    """
    argv = ["./ds4", "-m", str(gguf)]
    if ple is not None:
        argv += ["--ple", str(ple)]
    argv += [
        "-p",
        PROBE_PROMPT,
        "--tokens",
        str(PROBE_TOKENS),
        "--temp",
        "0",
    ]
    return argv


def generated_text(log: pathlib.Path) -> str:
    """The model's own output from a probe log, stripped of ds4's `ds4:` lines.

    ds4 prints diagnostics prefixed `ds4:` to the same stream; everything else
    is the generated continuation. Comparing the continuations, not the whole
    log, keeps a per-run timing line out of the equality check.
    """
    lines = [
        ln
        for ln in log.read_text(errors="replace").splitlines()
        if not ln.startswith("ds4:")
    ]
    return "\n".join(lines).strip()


def run_bench(
    argv: Sequence[str],
    tree: pathlib.Path,
    log: pathlib.Path,
    header: str,
    value: str,
) -> int:
    """One child invocation with the arm's env, its header written first.

    `append=True` keeps the header so the log carries the arm's own definition
    -- which value of the knob, on which binary -- and the admission probe reads
    it rather than trusting the caller.
    """
    log.write_text(header + "\n")
    return child.run(list(argv), cwd=tree, log=log, env={KNOB: value}, append=True)


def probe(
    label: str,
    value: str,
    *,
    out: pathlib.Path,
    tree: pathlib.Path,
    gguf: pathlib.Path,
    ple: pathlib.Path | None,
) -> str:
    """Run the greedy probe for one arm and return its generated text."""
    log = out / f"probe-{label}.log"
    argv = probe_argv(gguf, ple)
    header = f"# probe: {label} {KNOB}={value} {' '.join(argv)}"
    logger.info("probe %s (%s=%s) -> %s", label, KNOB, value, log)
    run_bench(argv, tree, log, header, value)
    return generated_text(log)


def check_admission(
    a_value: str,
    b_value: str,
    *,
    out: pathlib.Path,
    tree: pathlib.Path,
    gguf: pathlib.Path,
    ple: pathlib.Path | None,
) -> tuple[str, str]:
    """Prove the two levels differ arithmetically. Returns (a_text, b_text).

    Refuses when the greedy outputs are byte-identical: the knob did nothing,
    and a prefill-rate difference would then be noise wearing two labels.
    """
    kw = {"out": out, "tree": tree, "gguf": gguf, "ple": ple}
    a_text = probe("a", a_value, **kw)
    b_text = probe("b", b_value, **kw)
    if not a_text or not b_text:
        raise Refusing(
            "a probe produced no generated text; cannot prove the arms differ "
            f"(a={len(a_text)} chars, b={len(b_text)} chars) -- see the probe logs"
        )
    if a_text == b_text:
        raise Refusing(
            f"the two tile levels ({KNOB}={a_value} vs {b_value}) produced "
            "byte-identical greedy output, so the knob is a no-op here and a "
            "prefill difference would be noise; refusing to measure it"
        )
    return a_text, b_text


def run_meta(
    a_value: str,
    b_value: str,
    tree: pathlib.Path,
    gguf: pathlib.Path,
    ple: pathlib.Path | None,
    reps: int,
    *,
    ctx_start: int,
    ctx_max: int,
    step: int,
    gen: int,
    admission: tuple[str, str],
) -> dict[str, object]:
    """What produced the rows, beside them, so a later reader need not guess."""
    return {
        "knob": KNOB,
        "a_value": a_value,
        "b_value": b_value,
        "tree": str(tree),
        "head": decode_ab.git_out(tree, "rev-parse", "--short", "HEAD") or "unknown",
        "gguf": str(gguf),
        "ple": str(ple) if ple else None,
        "reps": reps,
        "sweep": {
            "ctx_start": ctx_start,
            "ctx_max": ctx_max,
            "step": step,
            "gen": gen,
        },
        "admission_signal": "greedy-output-differs",
        "admission": {"a_chars": len(admission[0]), "b_chars": len(admission[1])},
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def sweep(
    tree: pathlib.Path,
    gguf: pathlib.Path,
    ple: pathlib.Path | None,
    *,
    a_value: str,
    b_value: str,
    reps: int,
    out: pathlib.Path,
    ctx_start: int,
    ctx_max: int,
    step: int,
    gen: int,
    owner_pid: int,
    allow_odd: bool = False,
) -> int:
    """The whole A/B. Returns a process exit code."""
    if not ab_driver.leads_equally(reps, 2) and not allow_odd:
        raise Refusing(
            f"reps={reps} over 2 arms does not let each arm lead equally often, "
            "so alternation cannot cancel the position bias (#130, #201) -- up "
            "to +5.9% on a cold first rep. Use an even count, or --allow-odd-reps."
        )
    if a_value == b_value:
        raise Refusing(
            f"both arms request {KNOB}={a_value}; that is one arm wearing two "
            "labels, not an A/B"
        )
    bench = tree / "ds4-bench"
    cli = tree / "ds4"
    for binary in (bench, cli):
        if not (binary.exists() and os.access(binary, os.X_OK)):
            raise Refusing(f"{binary} is missing or not executable -- build it first")
    if ple is not None and not ple.exists():
        raise Refusing(f"--ple sidecar {ple} does not exist")

    prompt = prompt_for(tree)
    out.mkdir(parents=True, exist_ok=True)
    decode_ab.stamp_prompt(prompt, sidecar=out)

    # Prove the levels differ before taking the lock for the timed sweep: a
    # no-op knob should cost two short probes, not a full measurement.
    admission = check_admission(
        a_value, b_value, out=out, tree=tree, gguf=gguf, ple=ple
    )

    meta = run_meta(
        a_value,
        b_value,
        tree,
        gguf,
        ple,
        reps,
        ctx_start=ctx_start,
        ctx_max=ctx_max,
        step=step,
        gen=gen,
        admission=admission,
    )
    (out / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    (out / "probe-a.txt").write_text(admission[0] + "\n")
    (out / "probe-b.txt").write_text(admission[1] + "\n")
    logger.info("run: %s", json.dumps(meta))

    values = {"a": a_value, "b": b_value}
    arms = [
        ab_driver.Arm(name=label, backend=str(tree), serve=ab_driver.nothing)
        for label in ("a", "b")
    ]

    def one_arm(arm: ab_driver.Arm, tag: str, rep: int) -> int:
        value = values[arm.name]
        csv = out / f"{arm.name}-rep{rep}.csv"
        log = out / f"{arm.name}-rep{rep}.log"
        position = [a.name for a in ab_driver.order(arms, rep)].index(arm.name) + 1
        argv = bench_argv(
            gguf,
            prompt,
            csv,
            ple,
            ctx_start=ctx_start,
            ctx_max=ctx_max,
            step=step,
            gen=gen,
        )
        header = f"# arm: {arm.name} {KNOB}={value} (pos {position}) {' '.join(argv)}"
        logger.info(
            "%s rep %d (pos %d, %s=%s) -> %s", arm.name, rep, position, KNOB, value, csv
        )
        rc = run_bench(argv, pathlib.Path(arm.backend), log, header, value)
        if rc != 0:
            logger.error("FAILED: %s rep %d -- see %s", arm.name, rep, log)
            for line in log.read_text(errors="replace").splitlines()[-20:]:
                logger.error("  %s", line)
            return rc
        decode_ab.stamp_prompt(prompt, stamp=csv)
        return rc

    with decode_ab.run_lock(f"moe_tile_ab.py {KNOB} {a_value} vs {b_value}", owner_pid):
        failed = ab_driver.run(arms, reps, one_arm, allow_uneven=allow_odd)
    logger.info("done: %s", out)
    return ab_driver.report(failed, reps, len(arms))


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tree", type=pathlib.Path)
    p.add_argument("gguf", type=pathlib.Path)
    p.add_argument("out", nargs="?", type=pathlib.Path, default=DEFAULT_OUT)
    p.add_argument("--ple", type=pathlib.Path, default=None, help="PLE sidecar GGUF")
    p.add_argument(
        "--a-value", default="2", help=f"{KNOB} for arm A (default 2, level 2)"
    )
    p.add_argument(
        "--b-value", default="5", help=f"{KNOB} for arm B (default 5, compensated)"
    )
    p.add_argument("--reps", type=int, default=REPS)
    p.add_argument("--ctx-start", type=int, default=CTX_START)
    p.add_argument("--ctx-max", type=int, default=CTX_MAX)
    p.add_argument("--step", type=int, default=STEP)
    p.add_argument(
        "--gen", type=int, default=GEN, help="0 for a cold prefill (default)"
    )
    p.add_argument("--allow-odd-reps", action="store_true")
    args = p.parse_args(argv)
    logs.configure()

    try:
        return sweep(
            args.tree,
            args.gguf,
            args.ple,
            a_value=args.a_value,
            b_value=args.b_value,
            reps=args.reps,
            out=args.out.resolve(),
            ctx_start=args.ctx_start,
            ctx_max=args.ctx_max,
            step=args.step,
            gen=args.gen,
            owner_pid=os.getpid(),
            allow_odd=args.allow_odd_reps,
        )
    except Refusing as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
