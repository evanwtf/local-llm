#!/usr/bin/env python3
"""Decompose the wall-time variance in results.jsonl.

Issue #26 opened on a 3x swing between consecutive ds4 trials and proposed the
ds4 KV disk cache, at its 8 GB cap, as the cause -- trial 1 paying to repopulate
after a restart. This script tests that, and it does not survive: the first
trial of a batch is 0.98x the median of the rest, and the same spread appears on
Ollama and llama.cpp backends that have no such cache.

What the numbers say instead is that wall time tracks how many tokens the model
chose to emit (r = 0.98), not how fast the machine served them. A trial is slow
because the agent wrote more, not because the hardware was cold.

    uv run benchmarks/agent/variance.py
    uv run benchmarks/agent/variance.py --results other.jsonl

Read `seconds-per-turn ratio` as the machine's contribution and `turn ratio` as
the agent's. They are not close.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import itertools
import logging
import math
import pathlib
import statistics
import sys
from typing import Any

import results
import provenance

logger = logging.getLogger(__name__)

# A single run.py invocation walks 5 tasks x N trials, so rows inside one batch
# land minutes apart and separate batches are hours or days apart. 90 minutes
# splits them without cutting a slow trial off from its own batch: the longest
# single trial recorded here is 20.4 minutes.
BATCH_GAP_SECONDS = 5400


def cells(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Group passing trials by the condition that is meant to be held fixed."""
    out: dict[tuple[str, str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        out[(r["backend"], r["client"], r["task"])].append(r)
    return out


def batches(cell: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split one cell's trials into runs that happened in the same sitting."""
    cell = sorted(cell, key=lambda r: r["_t"])
    grouped, current = [], [cell[0]]
    for prev, row in itertools.pairwise(cell):
        if (row["_t"] - prev["_t"]).total_seconds() > BATCH_GAP_SECONDS:
            grouped.append(current)
            current = []
        current.append(row)
    grouped.append(current)
    return grouped


def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3:
        return math.nan
    mx, my = statistics.mean(xs), statistics.mean(ys)
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not dx or not dy:
        return math.nan
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (dx * dy)


def report_warmup(grouped: dict[tuple[str, str, str], list[dict[str, Any]]]) -> None:
    """Is the first trial after a restart slower? This is the #26 hypothesis."""
    logger.info("")
    logger.info("Is the FIRST trial of a batch slower than the rest?")
    logger.info("  ratio > 1 means a warm-up cost. #26 predicts roughly 1.5-3x.")
    per_pair: dict[tuple[str, str], list[float]] = collections.defaultdict(list)
    for (backend, client, _task), rows in grouped.items():
        for batch in batches(rows):
            if len(batch) < 3:
                continue
            rest = statistics.median(r["wall_seconds"] for r in batch[1:])
            if rest:
                per_pair[(backend, client)].append(batch[0]["wall_seconds"] / rest)
    every: list[float] = []
    for (backend, client), ratios in sorted(per_pair.items()):
        every += ratios
        logger.info("  %-26s %2d batches  %.2fx", f"{backend} x {client}", len(ratios),
                    statistics.median(ratios))
    if every:
        logger.info("  %-26s %2d batches  %.2fx  <-- overall", "ALL", len(every),
                    statistics.median(every))


def report_drivers(grouped: dict[tuple[str, str, str], list[dict[str, Any]]]) -> None:
    """Split each cell's spread into work done and time per unit of work."""
    logger.info("")
    logger.info("Within a cell, what does wall time track?")
    corr_turns, corr_tokens = [], []
    wall_r, turn_r, spt_r = [], [], []
    for rows in grouped.values():
        usable = [r for r in rows if r.get("num_turns") and r.get("output_tokens")]
        if len(usable) >= 4:
            wall = [r["wall_seconds"] for r in usable]
            for acc, key in ((corr_turns, "num_turns"), (corr_tokens, "output_tokens")):
                got = pearson(wall, [r[key] for r in usable])
                if not math.isnan(got):
                    acc.append(got)
        if len(usable) >= 3:
            ordered = sorted(usable, key=lambda r: r["wall_seconds"])
            slow, fast = ordered[-1], ordered[0]
            wall_r.append(slow["wall_seconds"] / fast["wall_seconds"])
            turn_r.append(slow["num_turns"] / fast["num_turns"])
            spt_r.append((slow["wall_seconds"] / slow["num_turns"])
                         / (fast["wall_seconds"] / fast["num_turns"]))
    if corr_tokens:
        logger.info("  correlation with output tokens : %.2f", statistics.median(corr_tokens))
        logger.info("  correlation with turns         : %.2f", statistics.median(corr_turns))
    if wall_r:
        logger.info("")
        logger.info("  Slowest vs fastest trial in each cell:")
        logger.info("    wall time            %.2fx", statistics.median(wall_r))
        logger.info("    turns taken          %.2fx   <-- the agent's contribution",
                    statistics.median(turn_r))
        logger.info("    seconds per turn     %.2fx   <-- the machine's contribution",
                    statistics.median(spt_r))


def report_worst(grouped: dict[tuple[str, str, str], list[dict[str, Any]]], limit: int) -> None:
    logger.info("")
    logger.info("Widest cells, and what moved:")
    logger.info("  %-40s %16s %10s %15s", "cell", "wall", "turns", "output tokens")
    ranked = []
    for key, rows in grouped.items():
        usable = [r for r in rows if r.get("num_turns") and r.get("output_tokens")]
        if len(usable) < 3:
            continue
        ordered = sorted(usable, key=lambda r: r["wall_seconds"])
        ranked.append((ordered[-1]["wall_seconds"] / ordered[0]["wall_seconds"],
                       key, ordered[0], ordered[-1]))
    for _spread, key, fast, slow in sorted(ranked, reverse=True)[:limit]:
        logger.info("  %-40s %5.0f->%-5.0fs %4d->%-4d %6d->%-6d",
                    " ".join(key), fast["wall_seconds"], slow["wall_seconds"],
                    fast["num_turns"], slow["num_turns"],
                    fast["output_tokens"], slow["output_tokens"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=pathlib.Path,
                        default=pathlib.Path(__file__).parent / "results.jsonl")
    parser.add_argument("--worst", type=int, default=10, help="how many wide cells to list")
    args = parser.parse_args(argv)

    provenance.configure()

    rows = [r for r in results.trials(args.results) if results.verdict(r)]
    if not rows:
        logger.error("no passing trials in %s", args.results)
        return 1
    for r in rows:
        r["_t"] = dt.datetime.fromisoformat(r["started"])

    grouped = cells(rows)
    logger.info("%d passing trials, %d cells (backend x client x task)", len(rows), len(grouped))
    report_warmup(grouped)
    report_drivers(grouped)
    report_worst(grouped, args.worst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
