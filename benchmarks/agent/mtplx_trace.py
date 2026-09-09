"""Read MTPLX's decode trace: did its draft head accept anything?

#148, the mtplx half. The ds4 half lives in `mtp_timing`; this is a separate
module on purpose, because the two engines export the same quantity under
opposite conventions and one reader for both would be wrong for one of them.

MTPLX 2.7.2 writes JSON Lines when `MTPLX_DECODE_TRACE_JSONL` names a path
(`generation.py:1059`). `MTPLX_DECODE_TRACE_INTERVAL_S` sets the cadence,
default 1.0s, and a final record is flushed per request so the tail is not
lost.

**Deltas, not totals.** Each record carries both, and the totals are tempting
-- but the trace object is constructed per generation call, with this request's
`prompt_tokens` and a fresh `run_id`, so `accepted_drafts_total` restarts at
zero on every request. Differencing totals across a trial that made many
requests yields nonsense, and negative nonsense at that. The `_delta` fields
are additive across records and across requests, so they are what we sum.

**No free-token subtraction here, unlike ds4.** MTPLX builds
`committed = [primary] + draft_tokens[:accepted_count]` (`generation.py:9527`)
-- the same structure ds4 uses -- but the counter it *exports* already excludes
the primary: `draft_acceptance_rate_delta` is
`accepted_drafts_delta / drafted_tokens_delta` (`:1300`), draft-only on both
sides. Applying `mtp_timing`'s `committed - 1` rule here would under-report by
one token per cycle and make a working head look partly broken.

    uv run python benchmarks/agent/mtplx_trace.py decode-trace.jsonl
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib

import provenance

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Counters:
    records: int
    requests: int
    accepted: int
    drafted: int
    verify_calls: int
    generated: int

    @property
    def accept_rate(self) -> float | None:
        """Accepted over drafted. None when nothing was ever drafted."""
        return self.accepted / self.drafted if self.drafted else None

    @property
    def used(self) -> bool:
        """The #148 assertion: did the draft head do any work at all?"""
        return self.accepted > 0


EMPTY = Counters(
    records=0, requests=0, accepted=0, drafted=0, verify_calls=0, generated=0
)


def read(text: str) -> Counters:
    """Sum the delta fields over every well-formed record.

    A malformed line is skipped and counted nowhere: a trace being written
    while we read it can end mid-line, and treating a torn tail as zero
    drafting would be a fabricated result.
    """
    records = accepted = drafted = verify = generated = 0
    runs: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or "accepted_drafts_delta" not in row:
            continue
        records += 1
        if run_id := row.get("run_id"):
            runs.add(str(run_id))
        accepted += int(row.get("accepted_drafts_delta") or 0)
        drafted += int(row.get("drafted_tokens_delta") or 0)
        verify += int(row.get("verify_calls_delta") or 0)
        generated += int(row.get("generated_tokens_delta") or 0)
    return Counters(
        records=records,
        requests=len(runs),
        accepted=accepted,
        drafted=drafted,
        verify_calls=verify,
        generated=generated,
    )


@dataclasses.dataclass(frozen=True)
class Reading:
    counters: Counters
    offset: int


def read_since(path: pathlib.Path, offset: int = 0) -> Reading:
    """Parse only the bytes appended since `offset`. See mtp_timing.read_since."""
    try:
        size = path.stat().st_size
    except OSError:
        return Reading(counters=EMPTY, offset=offset)
    if size < offset:
        logger.warning(
            "%s shrank (%d < %d) -- rereading from the start", path, size, offset
        )
        offset = 0
    with path.open("r", errors="replace") as handle:
        handle.seek(offset)
        text = handle.read()
        return Reading(counters=read(text), offset=handle.tell())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=pathlib.Path, help="MTPLX decode trace (JSONL)")
    parser.add_argument(
        "--require-used",
        action="store_true",
        help="exit non-zero if no draft token was accepted (#148 gate)",
    )
    args = parser.parse_args(argv)

    counters = read(args.trace.read_text(errors="replace"))

    if not counters.records:
        logger.warning(
            "no decode-trace records in %s -- was MTPLX_DECODE_TRACE_JSONL set?",
            args.trace,
        )
        logger.warning(
            "absence of a trace is NOT evidence the draft head is unused; "
            "it is evidence the trace was off"
        )
        return 2 if args.require_used else 0

    logger.info(
        "records=%d requests=%d generated=%d verify_calls=%d",
        counters.records,
        counters.requests,
        counters.generated,
        counters.verify_calls,
    )
    logger.info(
        "draft tokens drafted=%d accepted=%d", counters.drafted, counters.accepted
    )
    if (rate := counters.accept_rate) is not None:
        logger.info("accept rate=%.2f%%", 100.0 * rate)
    else:
        logger.warning("nothing was ever drafted")

    if not counters.used:
        logger.error(
            "MTPLX DRAFT HEAD ACCEPTED NOTHING across %d records -- "
            "speculative decoding was configured and did not happen",
            counters.records,
        )
        return 1 if args.require_used else 0
    return 0


if __name__ == "__main__":
    provenance.configure(show_name=True)
    raise SystemExit(main())
