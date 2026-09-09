"""Measure how much prefill a stalled KV prefix costs (#64, #50).

`ds4-server` logs a line whenever the live KV cache misses:

    ds4-server: live kv cache miss live=432 prompt=332 common=257 reason=token-mismatch

`common` is the reusable prefix. In a healthy session it tracks `prompt`
upward, so each turn re-prefills only what is new. #64 observed it **frozen**
at ~20,400 while `prompt` grew 25k -> 67k, which means every turn re-prefills
everything past that point:

    live=47958  prompt=25468  common=20398  reason=token-mismatch
    live=66171  prompt=65794  common=20398  reason=token-mismatch

#50 names the mechanism: the client injects a token counter as a system
message with `cache_control`, and the number changes every turn, so the
cached prefix can never match.

This turns that one observation into a number over any log we hold. The cost
of a miss is `prompt - common` tokens of re-prefill; at a measured prefill
rate those tokens are seconds of dead time before the first output token.

    uv run python scripts/kv_prefix_audit.py <server.log> [more.log ...]
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import re
import sys

logger = logging.getLogger(__name__)

#: Prefill tokens per second, for turning wasted tokens into wasted seconds.
#: ~360 t/s is the figure #64 quotes for this machine; override on the CLI
#: rather than editing, because it is a property of the run and not of the code.
DEFAULT_PREFILL_TPS = 360.0

MISS = re.compile(
    r"live kv cache miss\s+live=(?P<live>\d+)\s+prompt=(?P<prompt>\d+)"
    r"\s+common=(?P<common>\d+)\s+reason=(?P<reason>[a-z-]+)"
)


@dataclasses.dataclass(frozen=True)
class Miss:
    live: int
    prompt: int
    common: int
    reason: str


def parse(text: str) -> list[Miss]:
    """Every cache miss in a log, in order. Unparseable lines are ignored."""
    return [
        Miss(
            live=int(m["live"]),
            prompt=int(m["prompt"]),
            common=int(m["common"]),
            reason=m["reason"],
        )
        for m in MISS.finditer(text)
    ]


def wasted_tokens(misses: list[Miss]) -> int:
    """Tokens re-prefilled because the prefix did not cover them.

    `prompt - common` per miss. Negative differences are treated as zero: a
    `common` above `prompt` is not a cost, and clamping keeps one odd line
    from making a log look better than it is.
    """
    return sum(max(0, m.prompt - m.common) for m in misses)


def stalled_runs(misses: list[Miss], min_length: int = 3) -> list[list[Miss]]:
    """Consecutive misses sharing one `common` value.

    This is #64's signature, and it separates a prefix that is *working but
    cold* from one that has stopped: a cold cache shows `common` climbing, a
    stalled one shows it pinned turn after turn.

    The first version of this also required `prompt` to rise, matching the
    example on #64. Real logs disagree -- this is from a trial we hold:

        live=11415 prompt=11375 common=10534
        live=11427 prompt=11360 common=10534
        live=11413 prompt=11359 common=10534

    `common` is pinned while `prompt` drifts *down*, and ~830 tokens are
    re-prefilled on each of those turns. The direction of `prompt` is
    incidental; the pinned prefix is the defect. Requiring a rising prompt
    hid the steady state, which is where a long session spends most of its
    time.

    `min_length` exists because two misses can share a `common` by
    coincidence. Runs are reported with their cost, so a trivial one is
    visibly trivial rather than silently dropped.
    """
    runs: list[list[Miss]] = []
    current: list[Miss] = []
    for miss in misses:
        if current and miss.common == current[-1].common:
            current.append(miss)
            continue
        if len(current) >= min_length:
            runs.append(current)
        current = [miss]
    if len(current) >= min_length:
        runs.append(current)
    return runs


def summarise(path: pathlib.Path, misses: list[Miss], prefill_tps: float) -> str:
    if not misses:
        return f"{path.name}: no cache-miss lines"
    runs = stalled_runs(misses)
    waste = wasted_tokens(misses)
    reasons = {}
    for m in misses:
        reasons[m.reason] = reasons.get(m.reason, 0) + 1
    top = ", ".join(
        f"{k}={v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])
    )
    headline = (
        f"{path.name}: {len(misses)} misses, {waste:,} tokens re-prefilled "
        f"(~{waste / prefill_tps:,.0f}s at {prefill_tps:g} t/s) [{top}]"
    )
    out = [headline]
    for run in runs:
        cost = wasted_tokens(run)
        out.append(
            f"    STALL: common pinned at {run[0].common:,} across {len(run)} "
            f"misses, prompt {run[0].prompt:,} -> {run[-1].prompt:,}, "
            f"{cost:,} tokens re-prefilled (~{cost / prefill_tps:.0f}s)"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("logs", nargs="+", type=pathlib.Path)
    p.add_argument("--prefill-tps", type=float, default=DEFAULT_PREFILL_TPS)
    args = p.parse_args(argv)

    total_waste = 0
    total_stalls = 0
    for path in args.logs:
        try:
            misses = parse(path.read_text(errors="replace"))
        except OSError as exc:
            logger.error("%s: %s", path, exc)
            continue
        logger.info("%s", summarise(path, misses, args.prefill_tps))
        total_waste += wasted_tokens(misses)
        total_stalls += len(stalled_runs(misses))

    if len(args.logs) > 1:
        logger.info("")
        logger.info(
            "across %d logs: %d stalled runs, %s tokens re-prefilled "
            "(~%.0f minutes at %g t/s)",
            len(args.logs),
            total_stalls,
            f"{total_waste:,}",
            total_waste / args.prefill_tps / 60,
            args.prefill_tps,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
