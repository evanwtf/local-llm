"""Measure the live-KV prefix stall across a corpus of ds4-server logs (#64).

`ds4-server` prints one line per live-cache miss:

    live kv cache miss live=19953 prompt=11928 common=10901 vision=match reason=token-mismatch

`common` is the reusable prefix. #64's observation was that on a Claude Code
trial `common` **froze at ~20,400** while `prompt` grew from 25k to 67k, so
every turn re-prefilled everything past that point. That was one trial, read by
hand. This reads the whole corpus.

Two numbers come out, and they answer different questions.

**The plateau** is whether `common` stops advancing. A cache that is working
has `common` tracking `prompt` upward; a cache that has stalled has `common`
pinned while `prompt` climbs. The report gives the highest `common` seen, and
how many misses occurred at prompts well past it -- a miss whose prompt is far
larger than the best prefix ever reused is a turn that re-prefilled almost
everything.

**The re-prefilled total** is `prompt - common` summed over misses, in tokens.
Divided by a prefill rate it is the wall time the stall cost. The rate is an
argument rather than a constant because it is a property of the model and the
machine, and quoting a time without it would be quoting a number with no units.

A miss is not by itself a fault: the first request of a conversation has
nothing to match and shows up as `common` near zero. The plateau is the signal,
not the count.

    uv run python scripts/prefix_stall.py ~/bench-logs/*/server-*.log
    uv run python scripts/prefix_stall.py --prefill-tps 360 <logs>
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

#: `live kv cache miss live=19953 prompt=11928 common=10901 vision=match reason=token-mismatch`
#: Anchored on the whole phrase rather than on `common=`, because other lines
#: in the same log also carry `tokens=` style fields and a loose pattern would
#: pull them in.
MISS = re.compile(
    r"live kv cache miss "
    r"live=(?P<live>\d+) +prompt=(?P<prompt>\d+) +common=(?P<common>\d+)"
    r"(?: +vision=(?P<vision>\S+))?"
    r"(?: +reason=(?P<reason>\S+))?"
)

#: A miss whose prompt exceeds the best prefix ever reused by more than this
#: is counted as a stalled turn. 2048 is one prefill chunk: below that the
#: difference is bookkeeping, above it the turn paid for a real re-prefill.
STALL_MARGIN_TOKENS = 2048


@dataclass(frozen=True)
class Miss:
    """One live-cache miss."""

    live: int
    prompt: int
    common: int
    vision: str | None
    reason: str | None


@dataclass(frozen=True)
class Stall:
    """What a log says about its own prefix reuse."""

    path: pathlib.Path
    misses: int
    best_common: int
    stalled_turns: int
    reprefilled_tokens: int
    max_prompt: int

    @property
    def stalled(self) -> bool:
        """True when at least one turn re-prefilled well past the best prefix.

        One stalled turn is enough to report: the cost is quadratic in turn
        count, so a log with a single late miss is the start of the same curve
        as one with twenty.
        """
        return self.stalled_turns > 0


def parse_misses(text: str) -> list[Miss]:
    """Every live-cache miss in a log, in the order it was printed."""
    out: list[Miss] = []
    for m in MISS.finditer(text):
        out.append(
            Miss(
                live=int(m.group("live")),
                prompt=int(m.group("prompt")),
                common=int(m.group("common")),
                vision=m.group("vision"),
                reason=m.group("reason"),
            )
        )
    return out


def summarise(path: pathlib.Path, misses: list[Miss]) -> Stall:
    """Reduce one log's misses to the plateau and the re-prefilled total.

    `best_common` is the high-water mark of the reusable prefix. A turn counts
    as stalled when its prompt exceeds that mark by more than one prefill
    chunk -- it had that much context and reused none of it.
    """
    best = max((m.common for m in misses), default=0)
    stalled = sum(1 for m in misses if m.prompt - best > STALL_MARGIN_TOKENS)
    reprefilled = sum(max(0, m.prompt - m.common) for m in misses)
    return Stall(
        path=path,
        misses=len(misses),
        best_common=best,
        stalled_turns=stalled,
        reprefilled_tokens=reprefilled,
        max_prompt=max((m.prompt for m in misses), default=0),
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("logs", nargs="+", help="ds4-server logs to read")
    p.add_argument(
        "--prefill-tps",
        type=float,
        default=None,
        help="prefill rate, to convert re-prefilled tokens into seconds; "
        "omit and only the token counts are reported",
    )
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)

    stalls = []
    for name in args.logs:
        path = pathlib.Path(name)
        if not path.exists():
            logger.warning("%s: no such file", path)
            continue
        misses = parse_misses(path.read_text(errors="replace"))
        if not misses:
            continue
        stalls.append(summarise(path, misses))

    if not stalls:
        logger.error("no live-cache miss lines in any log given")
        return 1

    stalls.sort(key=lambda s: s.reprefilled_tokens, reverse=True)
    logger.info(
        "%-46s %6s %10s %8s %10s %12s",
        "log",
        "misses",
        "best pfx",
        "stalled",
        "max prompt",
        "re-prefilled",
    )
    for s in stalls:
        logger.info(
            "%-46s %6d %10d %8d %10d %12d",
            s.path.name[:46],
            s.misses,
            s.best_common,
            s.stalled_turns,
            s.max_prompt,
            s.reprefilled_tokens,
        )

    total = sum(s.reprefilled_tokens for s in stalls)
    stalled_logs = sum(1 for s in stalls if s.stalled)
    logger.info("")
    logger.info(
        "%d logs, %d with at least one stalled turn; %d tokens re-prefilled",
        len(stalls),
        stalled_logs,
        total,
    )
    if args.prefill_tps:
        logger.info(
            "  at %.0f t/s that is %.1f minutes of prefill that a working "
            "prefix would have made nearly free",
            args.prefill_tps,
            total / args.prefill_tps / 60,
        )
    else:
        logger.info(
            "  pass --prefill-tps to convert that to time; the rate is a "
            "property of the model and the machine, not a constant"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
