#!/usr/bin/env python3
"""Per-turn re-prefill from a ds4-server log, bucketed by context. #158

The ds4 counterpart of `mlxserve_turns.py`. An agent session grows turn by
turn, and each turn should reuse the prefix it already has. When reuse fails,
a turn prefills thousands of tokens again, and that cost sets the session's
wall time; the per-row ledger cannot show it. ds4-server prints one line per
request when its prompt is ready:

    chat ctx=24086..35830:11744 TOOLS prompt done 8.245s

`ctx=A..B:N` is A tokens reused from the cache, B tokens in the prompt, and N
tokens prefilled now, in the time shown. This reads those lines and reports,
per context bucket, how many tokens each turn prefilled and how long it took.

    uv run python scripts/ds4serve_turns.py <server.log> [<server.log> ...]
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
    r"ctx=(?P<cached>\d+)\.\.(?P<total>\d+):(?P<pre>\d+)"
    r"(?: [A-Z]+)* prompt done (?P<sec>[\d.]+)s"
)


@dataclasses.dataclass(frozen=True)
class Turn:
    cached: int
    total: int
    prefilled: int
    seconds: float

    @property
    def rate(self) -> float:
        return self.prefilled / self.seconds if self.seconds else 0.0


@dataclasses.dataclass(frozen=True)
class Bucket:
    label: str
    turns: int
    median_prefilled: float
    median_seconds: float
    total_seconds: float


def parse_line(line: str) -> Turn | None:
    """One prompt-ready line, or None for any other log line."""
    m = _TURN.search(line)
    if not m:
        return None
    return Turn(
        cached=int(m["cached"]),
        total=int(m["total"]),
        prefilled=int(m["pre"]),
        seconds=float(m["sec"]),
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
                median_prefilled=statistics.median(t.prefilled for t in got),
                median_seconds=statistics.median(t.seconds for t in got),
                total_seconds=sum(t.seconds for t in got),
            )
        )
    return out


def render(buckets: Sequence[Bucket]) -> str:
    lines = [
        (
            "| context | turns | median tokens prefilled | median prefill s "
            "| total prefill s |"
        ),
        "|---|---:|---:|---:|---:|",
    ]
    for b in buckets:
        lines.append(
            f"| {b.label} | {b.turns} | {b.median_prefilled:.0f} "
            f"| {b.median_seconds:.2f} | {b.total_seconds:.1f} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("logs", type=pathlib.Path, nargs="+")
    p.add_argument(
        "--edges",
        type=lambda s: tuple(int(x) for x in s.split(",")),
        default=EDGES,
        help="context bucket edges in tokens, comma-separated",
    )
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    turns: list[Turn] = []
    for log in args.logs:
        text = log.read_text(errors="replace")
        turns.extend(t for t in map(parse_line, text.splitlines()) if t)
    if not turns:
        logger.error("no prompt-ready lines in %s", ", ".join(map(str, args.logs)))
        return 1
    logger.info("%s", render(by_bucket(turns, args.edges)).rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
