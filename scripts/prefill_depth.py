#!/usr/bin/env python3
"""Cold prefill against appended-token prefill at depth, one ds4 tree. #158

An agent turn rarely prefills from an empty cache. It appends a few thousand
tokens to a context that already holds tens of thousands, so the rate that sets
its wall time is the **appended** rate at depth, not the cold rate a benchmark
headline quotes. #158 asks for the two to be measured apart on this machine.

Two arms, run alternately so each leads equally often (#130, #201):

- **appended**: one ds4-bench sweep from `step` to the deepest depth in `step`
  increments, KV reused between frontiers. The row at frontier D is the rate
  for the last `step` tokens, with D - step already in the cache. The first
  frontier starts from an empty cache and is not an appended number.
- **cold**: one fresh ds4-bench run per depth, `--ctx-start D --ctx-max D`,
  which prefills all D tokens from an empty cache.

Both use `--gen-tokens 0` (pure prefill) and the same prompt, which is stamped
on every CSV (#140).

    uv run python scripts/prefill_depth.py <tree> <gguf> [out] [--depths ...]
    uv run python scripts/prefill_depth.py --report <out>
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import pathlib
import statistics
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

DEFAULT_OUT = REPO / "benchmarks" / "ds4" / "prefill-depth-158"
#: Depths an agent session actually reaches, up to the 64K the suite's longest
#: tasks touch. Each must be a multiple of STEP so it lands on a frontier.
DEPTHS = (16384, 32768, 65536)
#: The appended chunk: a mid-size tool result plus the model's next turn.
STEP = 8192
REPS = 4
ARMS = ("appended", "cold")

Refusal = decode_ab.Refusal


def check_plan(depths: Sequence[int], step: int, reps: int) -> None:
    """Refuse a plan that cannot pair every cold number with an appended one."""
    for d in depths:
        if d % step:
            raise Refusal(
                f"depth {d} is not a multiple of step {step}: it would get a "
                "cold number and no appended partner"
            )
        if d <= step:
            raise Refusal(
                f"depth {d} must be deeper than step {step}: at the first "
                "frontier the appended sweep is itself a cold prefill"
            )
    if not ab_driver.leads_equally(reps, len(ARMS)):
        raise Refusal(
            f"reps={reps} does not let each arm lead equally often, so "
            "alternation cannot cancel the position bias (#130, #201)"
        )


def bench_plan(
    arm: str, depths: Sequence[int], step: int
) -> list[tuple[str, int, int]]:
    """The ds4-bench runs one arm makes: (csv label, ctx_start, ctx_max)."""
    if arm == "appended":
        return [("appended", step, max(depths))]
    return [(f"cold-{d}", d, d) for d in sorted(depths)]


def _rows(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def appended_rates(path: pathlib.Path, step: int) -> dict[int, float]:
    """Frontier -> prefill tok/s for the rows that appended `step` tokens."""
    got: dict[int, float] = {}
    for r in _rows(path):
        ctx, pre = int(r["ctx_tokens"]), int(r["prefill_tokens"])
        if pre == step and ctx > step:
            got[ctx] = float(r["prefill_tps"])
    return got


def cold_rate(path: pathlib.Path, depth: int) -> float:
    """The prefill tok/s of a cold run: its one row must prefill every token."""
    for r in _rows(path):
        if int(r["ctx_tokens"]) == depth:
            if int(r["prefill_tokens"]) != depth:
                raise ValueError(
                    f"{path}: {r['prefill_tokens']} of {depth} tokens prefilled "
                    "-- not a cold prefill"
                )
            return float(r["prefill_tps"])
    raise ValueError(f"{path}: no row at depth {depth}")


def summarize(
    out: pathlib.Path, depths: Sequence[int], step: int
) -> dict[int, dict[str, list[float]]]:
    """Per depth, every rep's cold and appended rate, in rep order."""
    got: dict[int, dict[str, list[float]]] = {
        d: {"cold": [], "appended": []} for d in depths
    }
    for path in sorted(out.glob("appended-rep*.csv")):
        rates = appended_rates(path, step)
        for d in depths:
            if d in rates:
                got[d]["appended"].append(rates[d])
    for d in depths:
        for path in sorted(out.glob(f"cold-{d}-rep*.csv")):
            got[d]["cold"].append(cold_rate(path, d))
    return got


def render(summary: dict[int, dict[str, list[float]]], step: int) -> str:
    """The comparison as a markdown table, seconds beside every rate."""
    lines = [
        (
            f"| depth | cold t/s | cold s (all tokens) | appended t/s | "
            f"appended s (last {step}) | appended / cold | reps |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for d in sorted(summary):
        cold, app = summary[d]["cold"], summary[d]["appended"]
        if not cold or not app:
            lines.append(f"| {d} | — | — | — | — | — | 0 |")
            continue
        c, a = statistics.median(cold), statistics.median(app)
        lines.append(
            f"| {d} | {c:.1f} | {d / c:.1f} | {a:.1f} | {step / a:.1f} "
            f"| {round(100 * a / c)}% | {min(len(cold), len(app))} |"
        )
    return "\n".join(lines) + "\n"


def header_text(
    tree: pathlib.Path,
    gguf: pathlib.Path,
    prompt: pathlib.Path,
    depths: Sequence[int],
    step: int,
    reps: int,
) -> str:
    head = decode_ab.git_out(tree, "rev-parse", "--short", "HEAD") or "unknown"
    lines = [f"# prefill at depth, cold vs appended, one tree @ {head}", f"tree={tree}"]
    if decode_ab.git_out(tree, "status", "--porcelain"):
        lines.append(
            "dirty=true   # uncommitted code: the sha does not name this binary"
        )
    lines += [
        f"gguf={gguf}",
        f"prompt={prompt}",
        f"depths={','.join(map(str, depths))} step={step} gen=0 reps={reps}",
    ]
    for var in ("DS4_METAL_PREFILL_CHUNK", "DS4_METAL_GRAPH_RAW_CAP"):
        lines.append(f"{var}={os.environ.get(var, '<unset>')}")
    return "\n".join(lines) + "\n"


def sweep(
    tree: pathlib.Path,
    gguf: pathlib.Path,
    out: pathlib.Path,
    *,
    depths: Sequence[int] = DEPTHS,
    step: int = STEP,
    reps: int = REPS,
    prompt: pathlib.Path | None = None,
    owner_pid: int,
) -> int:
    """The whole measurement. Returns a process exit code."""
    check_plan(depths, step, reps)
    binary = tree / "ds4-bench"
    if not (binary.exists() and os.access(binary, os.X_OK)):
        raise Refusal(f"{binary} is missing or not executable -- build it first")
    prompt = prompt or tree / "speed-bench" / "promessi_sposi.txt"
    out.mkdir(parents=True, exist_ok=True)
    decode_ab.stamp_prompt(prompt, sidecar=out)
    text = header_text(tree, gguf, prompt, depths, step, reps)
    with (out / "arms.txt").open("a") as handle:
        handle.write(text)
    logger.info("%s", text.rstrip())

    built = [
        ab_driver.Arm(name=a, backend=str(tree), serve=ab_driver.nothing) for a in ARMS
    ]

    def one_arm(arm: ab_driver.Arm, tag: str, rep: int) -> int:
        position = [a.name for a in ab_driver.order(built, rep)].index(arm.name) + 1
        with (out / "run-order.txt").open("a") as handle:
            handle.write(
                f"rep={rep} position={position} of {len(built)} arm={arm.name}\n"
            )
        for label, start, end in bench_plan(arm.name, depths, step):
            csv_path = out / f"{label}-rep{rep}.csv"
            log = out / f"{label}-rep{rep}.log"
            logger.info("%s rep %d (position %d) -> %s", label, rep, position, csv_path)
            rc = child.run(
                decode_ab.bench_argv(
                    gguf,
                    csv_path,
                    prompt,
                    binary=binary,
                    ctx_start=start,
                    ctx_max=end,
                    step=step,
                    gen=0,
                ),
                cwd=tree,
                log=log,
            )
            if rc != 0:
                logger.error("FAILED: %s rep %d -- see %s", label, rep, log)
                return rc
            decode_ab.stamp_prompt(prompt, stamp=csv_path)
        return 0

    with decode_ab.run_lock("prefill_depth.py cold vs appended", owner_pid):
        failed = ab_driver.run(built, reps, one_arm)
    logger.info("%s", render(summarize(out, depths, step), step).rstrip())
    logger.info("done: %s", out)
    return ab_driver.report(failed, reps, len(built))


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tree", type=pathlib.Path, nargs="?")
    p.add_argument("gguf", type=pathlib.Path, nargs="?")
    p.add_argument("out", nargs="?", type=pathlib.Path, default=DEFAULT_OUT)
    p.add_argument(
        "--depths", type=lambda s: [int(x) for x in s.split(",")], default=list(DEPTHS)
    )
    p.add_argument("--step", type=int, default=STEP)
    p.add_argument("--reps", type=int, default=REPS)
    p.add_argument("--prompt", type=pathlib.Path, default=None)
    p.add_argument(
        "--report", type=pathlib.Path, help="render an existing out dir and exit"
    )
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN if args.report else logs.FORMAT)
    if args.report:
        logger.info(
            "%s",
            render(summarize(args.report, args.depths, args.step), args.step).rstrip(),
        )
        return 0
    if args.tree is None or args.gguf is None:
        p.error("tree and gguf are required unless --report is given")
    try:
        return sweep(
            args.tree.expanduser(),
            args.gguf.expanduser(),
            args.out,
            depths=args.depths,
            step=args.step,
            reps=args.reps,
            prompt=args.prompt,
            owner_pid=os.getpid(),
        )
    except Refusal as why:
        logger.error("refused: %s", why)
        return 2


if __name__ == "__main__":
    sys.exit(main())
