#!/usr/bin/env python3
"""Per-turn prefill and decode from an mlx-serve log, bucketed by context. #479

An agent session on mlx-serve grows turn by turn. The hot prefix cache is
capped (2048 MB by default), so past some depth a turn stops reusing most of
its prefix and prefills tens of thousands of tokens again. That cost sets the
session's wall time, and the per-row ledger cannot show it. The server log can:
every completed request prints one line such as

    <- 63463+84 tokens streamed [prefill: 395.2 tok/s (31392 cached / 63463
    total), decode: 38.4 tok/s] [tool_calls]

This reads those lines and reports, per context bucket, how many tokens each
turn prefilled again, at what rate, and how many seconds that took.

    uv run python scripts/mlxserve_turns.py <server.log> [--edges 16384,32768,49152]
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import re
import statistics
import sys
from collections.abc import Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

EDGES = (16384, 32768, 49152)

_TURN = re.compile(
    r"<- (?P<total>\d+)\+(?P<gen>\d+) tokens.*?"
    r"\[prefill: (?P<pre>[\d.]+) tok/s"
    r"(?: \((?P<cached>\d+) cached / \d+ total\))?"
    r", decode: (?P<dec>[\d.]+) tok/s\]"
)


@dataclasses.dataclass(frozen=True)
class Turn:
    total: int
    cached: int
    generated: int
    prefill_tps: float
    decode_tps: float

    @property
    def uncached(self) -> int:
        return self.total - self.cached

    @property
    def prefill_seconds(self) -> float:
        return self.uncached / self.prefill_tps if self.prefill_tps else 0.0


@dataclasses.dataclass(frozen=True)
class Bucket:
    label: str
    turns: int
    median_uncached: float
    median_prefill_tps: float
    median_prefill_seconds: float
    median_decode_tps: float


def parse_line(line: str) -> Turn | None:
    """One completed request, or None for any other log line."""
    m = _TURN.search(line)
    if not m:
        return None
    return Turn(
        total=int(m["total"]),
        cached=int(m["cached"] or 0),
        generated=int(m["gen"]),
        prefill_tps=float(m["pre"]),
        decode_tps=float(m["dec"]),
    )


def _label(lo: int, hi: int | None) -> str:
    k = lo // 1024
    return f"{k}K+" if hi is None else f"{k}-{hi // 1024}K"


def by_bucket(turns: Sequence[Turn], edges: Sequence[int] = EDGES) -> list[Bucket]:
    """Group turns by total context; empty buckets are left out."""
    bounds = [0, *edges]
    out: list[Bucket] = []
    for i, lo in enumerate(bounds):
        hi = bounds[i + 1] if i + 1 < len(bounds) else None
        got = [t for t in turns if t.total >= lo and (hi is None or t.total < hi)]
        if not got:
            continue
        out.append(
            Bucket(
                label=_label(lo, hi),
                turns=len(got),
                median_uncached=statistics.median(t.uncached for t in got),
                median_prefill_tps=statistics.median(t.prefill_tps for t in got),
                median_prefill_seconds=statistics.median(
                    t.prefill_seconds for t in got
                ),
                median_decode_tps=statistics.median(t.decode_tps for t in got),
            )
        )
    return out


def render(buckets: Sequence[Bucket]) -> str:
    lines = [
        (
            "| context | turns | median tokens prefilled | prefill tok/s "
            "| prefill s | decode tok/s |"
        ),
        "|---|---:|---:|---:|---:|---:|",
    ]
    for b in buckets:
        lines.append(
            f"| {b.label} | {b.turns} | {b.median_uncached:.0f} "
            f"| {b.median_prefill_tps:.1f} | {b.median_prefill_seconds:.1f} "
            f"| {b.median_decode_tps:.1f} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("log", type=pathlib.Path)
    p.add_argument(
        "--edges",
        type=lambda s: tuple(int(x) for x in s.split(",")),
        default=EDGES,
        help="context bucket edges in tokens, comma-separated",
    )
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    text = args.log.read_text(errors="replace")
    turns = [t for t in map(parse_line, text.splitlines()) if t]
    if not turns:
        logger.error("no completed requests in %s", args.log)
        return 1
    logger.info("%s", render(by_bucket(turns, args.edges)).rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
