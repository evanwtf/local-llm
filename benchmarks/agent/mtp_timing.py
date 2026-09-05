"""Read ds4's MTP timing counters: was the draft head actually used?

#148/#151. We run `qwen38fnds4mtp7shim` with `--mtp-draft 7` and an MTP gguf
on disk, and we have never asserted that a single drafted token was accepted
-- only that the flag was passed. Two independent reports landed on the same
day saying that is not enough: an oMLX recipe whose apparent 2x was mostly
repairing an MTP config enabled with no usable draft head, and ivanfioravanti
saying "In ds4 I've not cooked support for MTP in ds4 chat" while measuring
MTPLX above ds4 on an M5 Max. A flag that is accepted and does nothing is the
most expensive kind of no-op: it looks like a treatment arm.

ds4 already counts this. `--mtp-timing`, or the env var `DS4_MTP_TIMING`,
prints one line per speculative cycle to stderr (ds4.c:79516). The env var is
what the harness should use -- it turns the counters on without changing the
server command line, so the arm's launch config stays identical to the rows
already taken.

**The trap this module exists to avoid.** A cycle reports `drafted=` and
`committed=`, and `committed` INCLUDES the first token, which is verified for
free -- ds4_session_eval() has already produced the base logits for the
committed prefix, so the first token costs no speculative work. ds4.c says so
directly, and the margin-skip line proves it: a cycle that DECLINES to use the
draft still prints `drafted=2 committed=1`. So a run whose draft head never
works reports `committed=1` on every cycle, and summing `committed` naively
yields a large, healthy-looking number for an arm that accepted nothing.

    accepted draft tokens = sum(committed - 1)
    proposed draft tokens = sum(drafted - 1)

Both subtract the free token. That subtraction is the whole point of the file;
`test_mtp_timing.py` pins it, because getting it wrong converts "the treatment
was never applied" into "the treatment worked", which is the one error that
cannot be caught downstream.

    uv run python benchmarks/agent/mtp_timing.py server.log
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import re

import provenance

logger = logging.getLogger(__name__)

# ds4 prints MTP counters from two different code paths, and they do not
# agree on what they report. Both are real output from the same binary.
#
# The Qwen path -- the one qwen38fnds4mtp7shim actually runs:
#   ds4: Qwen MTP timing drafted=7 accepted=3 target_tokens=4 cycle=.. verifier=block
# `accepted` is already draft-only. The free first token is the +1 in
# target_tokens: the source builds it as `1u + plan.accepted` (ds4.c:79xxx).
QWEN_CYCLE = re.compile(
    r"^ds4: Qwen MTP timing drafted=(?P<drafted>\d+) accepted=(?P<accepted>\d+)"
    r" target_tokens=(?P<target>\d+)"
)

# The decode2/micro path, which reports `committed` INCLUDING that free token:
#   ds4: mtp timing micro drafted=7 committed=5 draft=.. verify=.. total=..
# A cycle that declines the draft still prints committed=1, so this shape
# needs the subtraction and the Qwen shape must not have it. Getting this
# backwards is a silent one-token-per-cycle error in either direction.
CYCLE = re.compile(
    r"^ds4: mtp timing (?P<kind>[\w-]+) drafted=(?P<drafted>\d+) committed=(?P<committed>\d+)\b"
)

# Emitted under DS4_MTP_SPEC_LOG when the first proposed token already
# disagrees. No speculative work happens, so no timing line follows.
SPEC_MISS = re.compile(r"^ds4: mtp spec miss first draft=(?P<token>-?\d+)\b")


@dataclasses.dataclass(frozen=True)
class Cycle:
    """One speculative cycle, with the free first token already resolved away.

    Both counts are draft-only. The two log shapes disagree about whether
    their raw numbers include the free token, so that is settled at parse
    time rather than carried around to be got wrong later.
    """

    kind: str
    accepted: int
    proposed: int

    @property
    def bypassed(self) -> bool:
        """A cycle where nothing was drafted at all.

        ds4's MTP scheduler can decide drafting is a net loss and switch to
        plain decode, after which it still prints a cycle per token with
        `drafted=0 ... verifier=scheduler-bypass`. Those are not drafting;
        counting them as cycles makes an arm look active while it is not.
        """
        return self.proposed == 0


@dataclasses.dataclass(frozen=True)
class Counters:
    cycles: tuple[Cycle, ...]
    spec_misses: int

    @property
    def accepted(self) -> int:
        return sum(c.accepted for c in self.cycles)

    @property
    def proposed(self) -> int:
        return sum(c.proposed for c in self.cycles)

    @property
    def accept_rate(self) -> float | None:
        """Accepted over proposed. None when nothing was ever proposed."""
        return self.accepted / self.proposed if self.proposed else None

    @property
    def used(self) -> bool:
        """Did the draft head do any work at all?

        Necessary but NOT sufficient for "this is an MTP arm" -- see
        `drafting_share`. A head can accept hundreds of tokens in the first
        seconds and then be switched off for the entire measured run.
        """
        return self.accepted > 0

    @property
    def bypassed(self) -> int:
        """Cycles where the scheduler drafted nothing."""
        return sum(1 for c in self.cycles if c.bypassed)

    @property
    def drafting(self) -> int:
        """Cycles that actually proposed draft tokens."""
        return len(self.cycles) - self.bypassed

    @property
    def drafting_share(self) -> float | None:
        """Fraction of cycles that drafted. None when there were no cycles.

        This is the number that says whether an arm was an MTP arm. #148 was
        written around `accepted == 0`, and that test is too weak: ds4's
        scheduler measures MTP against plain decode and disables it when it
        loses, so an arm can accept hundreds of draft tokens during warmup
        and serve every measured request without drafting at all.
        """
        return self.drafting / len(self.cycles) if self.cycles else None

    def by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.cycles:
            counts[c.kind] = counts.get(c.kind, 0) + 1
        return counts


def read(text: str) -> Counters:
    """Parse ds4 stderr. Unrecognised lines are ignored, not an error --
    a server log is mostly other things."""
    cycles: list[Cycle] = []
    misses = 0
    for raw in text.splitlines():
        line = raw.strip()
        if match := QWEN_CYCLE.match(line):
            accepted = int(match["accepted"])
            if int(match["target"]) != accepted + 1:
                # target_tokens is accepted plus the free first token. If that
                # stops holding, the line's meaning has changed and every
                # number derived from it is suspect -- say so rather than
                # keep counting.
                logger.warning(
                    "unexpected Qwen MTP line: target_tokens=%s but accepted=%d "
                    "(expected %d) -- the log format may have changed",
                    match["target"],
                    accepted,
                    accepted + 1,
                )
            cycles.append(
                Cycle(kind="qwen", accepted=accepted, proposed=int(match["drafted"]))
            )
        elif match := CYCLE.match(line):
            cycles.append(
                Cycle(
                    kind=match["kind"],
                    accepted=max(int(match["committed"]) - 1, 0),
                    proposed=max(int(match["drafted"]) - 1, 0),
                )
            )
        elif SPEC_MISS.match(line):
            misses += 1
    return Counters(cycles=tuple(cycles), spec_misses=misses)


@dataclasses.dataclass(frozen=True)
class Reading:
    """Counters seen in a slice of a log, and where that slice ended."""

    counters: Counters
    offset: int


def read_since(path: pathlib.Path, offset: int = 0) -> Reading:
    """Parse only the bytes appended since `offset`.

    A trial needs the counters *it* produced, not the server's running total,
    and the server log spans a whole sweep. Sampling the offset before and
    after a trial is the same before/after shape the guarded-copy tripwire
    uses, and for the same reason: it attributes to the trial only what
    happened inside it.

    A log that shrank (rotated, or a new server on the same path) resets to 0
    rather than seeking past the end -- a negative slice would silently read
    as "no cycles", which is the one answer that must never be manufactured.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return Reading(counters=Counters(cycles=(), spec_misses=0), offset=offset)
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
    parser.add_argument("log", type=pathlib.Path, help="ds4 server log (stderr)")
    parser.add_argument(
        "--require-used",
        action="store_true",
        help="exit non-zero if no draft token was accepted (#148 gate)",
    )
    args = parser.parse_args(argv)

    counters = read(args.log.read_text(errors="replace"))

    if not counters.cycles:
        logger.warning(
            "no MTP timing lines in %s -- was DS4_MTP_TIMING set on the server?",
            args.log,
        )
        logger.warning(
            "absence of counters is NOT evidence the draft head is unused; "
            "it is evidence the counters were off"
        )
        return 2 if args.require_used else 0

    logger.info("cycles=%d %s", len(counters.cycles), counters.by_kind())
    logger.info("spec misses (first draft rejected)=%d", counters.spec_misses)
    logger.info(
        "cycles drafting=%d bypassed=%d (scheduler switched to plain decode)",
        counters.drafting,
        counters.bypassed,
    )
    logger.info(
        "draft tokens proposed=%d accepted=%d",
        counters.proposed,
        counters.accepted,
    )
    if (share := counters.drafting_share) is not None and share < 0.5:
        logger.warning(
            "only %.1f%% of cycles drafted -- the scheduler spent most of this "
            "log in plain decode, so these rows are largely NOT an MTP measurement",
            100.0 * share,
        )
    if (rate := counters.accept_rate) is not None:
        logger.info("accept rate=%.2f%%", 100.0 * rate)
    else:
        logger.warning("nothing was ever proposed beyond the free first token")

    if not counters.used:
        logger.error(
            "MTP DRAFT HEAD ACCEPTED NOTHING across %d cycles -- "
            "the flag was passed but the treatment was not applied",
            len(counters.cycles),
        )
        return 1 if args.require_used else 0
    return 0


if __name__ == "__main__":
    # provenance.configure(), not basicConfig: every line names the harness
    # commit that wrote it, and test_provenance pins that no entry point
    # bypasses it. A counter read out of a log is only as useful as the
    # record of which code read it.
    provenance.configure(show_name=True)
    raise SystemExit(main())
