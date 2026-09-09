#!/usr/bin/env python3
"""Audit an MTP arm's drafting counters: the server log beside the ledger.

Why this is a script and not a grep. On 2026-09-08 a published comment read
*"the server log shows real drafting (20394 MTP, 16154 accepted, 15934
drafted)"*. Two of those three numbers were occurrence counts of the **words**
`MTP` and `accepted` in the log file. The third was the total cycle count
reported as the number that drafted, overstating drafting by 3.1x. All three
came out of an ad-hoc grep typed at a prompt, and the real figure was 5074 of
15934 cycles (31.8%).

A hand-rolled `awk` over the timing lines is wrong by construction anyway.
ds4 prints two cycle shapes and they disagree by one token per cycle: the Qwen
line's `accepted=` is already draft-only, while the decode2/micro line's
`committed=` includes a first token that was verified for free. Summing them
the same way is a silent one-token-per-cycle error in one direction or the
other. `benchmarks/agent/mtp_timing` already carries that distinction, so this
reads both sides through it.

    uv run python scripts/mtp_draft_audit.py \
        --server-log ~/bench-logs/<run>/ds4server-r1-mtp.log \
        --batch greedy-mtp-ab

The two sides never match exactly and are not supposed to: the ledger counts
per-task windows, the log counts a whole server process including warmup and
the gaps between tasks. **The drafting SHARE is what must agree.** A share
that disagrees means the per-task windows are missing cycles, and then every
per-row drafting figure in the ledger is suspect -- which is the #210 claim
this project has already had to retract once.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import mtp_timing

logger = logging.getLogger(__name__)

# A share that differs by more than this between the two sources means they
# are not describing the same work. Chosen from the one paired reading we
# have: 31.8% from the log and 31.8% from the ledger over 15 tasks, where the
# 179-cycle gap moved the share by under 0.1pp.
SHARE_TOLERANCE_PP = 1.0


class Totals:
    """Drafting counters from one source, with the share that matters."""

    def __init__(
        self,
        source: str,
        cycles: int,
        drafting: int,
        proposed: int,
        accepted: int,
        spec_misses: int,
    ) -> None:
        self.source = source
        self.cycles = cycles
        self.drafting = drafting
        self.proposed = proposed
        self.accepted = accepted
        self.spec_misses = spec_misses

    @property
    def drafting_share(self) -> float | None:
        """Fraction of cycles that drafted. None when there were no cycles."""
        return self.drafting / self.cycles if self.cycles else None

    @property
    def accept_rate(self) -> float | None:
        """Accepted over proposed. None when nothing was proposed.

        Reported beside the share deliberately. On its own it says nothing
        about how few cycles drafted -- that is the substitution #210 exists
        to refuse.
        """
        return self.accepted / self.proposed if self.proposed else None

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "cycles": self.cycles,
            "drafting": self.drafting,
            "drafting_share": self.drafting_share,
            "proposed": self.proposed,
            "accepted": self.accepted,
            "accept_rate": self.accept_rate,
            "spec_misses": self.spec_misses,
        }


def from_log(text: str) -> Totals:
    """Totals for a whole ds4 server log, via the shared timing parser."""
    counters = mtp_timing.read(text)
    return Totals(
        source="server log",
        cycles=len(counters.cycles),
        drafting=counters.drafting,
        proposed=counters.proposed,
        accepted=counters.accepted,
        spec_misses=counters.spec_misses,
    )


def ledger_rows(text: str, batch: str, backend: str | None = None) -> list[dict]:
    """Rows of one batch that carry draft counters, newest last.

    A row without a `draft` block is not an MTP row and is skipped rather
    than counted as a zero -- counting it would dilute the share with cycles
    that were never observed.
    """
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            logger.warning("skipping a line that is not JSON")
            continue
        if row.get("batch") != batch:
            continue
        if backend is not None and row.get("backend") != backend:
            continue
        if isinstance(row.get("draft"), dict):
            rows.append(row)
    return rows


def from_ledger(text: str, batch: str, backend: str | None = None) -> Totals:
    """Totals summed over one batch's rows in results.jsonl."""
    rows = ledger_rows(text, batch, backend)
    get = lambda key: sum(int(r["draft"].get(key, 0)) for r in rows)
    return Totals(
        source=f"ledger ({len(rows)} rows)",
        cycles=get("cycles"),
        drafting=get("drafting"),
        proposed=get("proposed"),
        accepted=get("accepted"),
        spec_misses=get("spec_misses"),
    )


def shares_agree(
    a: Totals, b: Totals, tolerance_pp: float = SHARE_TOLERANCE_PP
) -> bool:
    """Whether two sources tell the same story about how much drafting happened.

    Two sources with no cycles at all agree. One with cycles and one without
    do not: that is a log matched against the wrong batch, which is a failure
    to report rather than a zero to publish.
    """
    if a.drafting_share is None and b.drafting_share is None:
        return True
    if a.drafting_share is None or b.drafting_share is None:
        return False
    return abs(a.drafting_share - b.drafting_share) * 100 <= tolerance_pp


def render(totals: list[Totals]) -> list[str]:
    """One line per source, plus the sentence a comment should quote."""
    lines = []
    for t in totals:
        share = "n/a" if t.drafting_share is None else f"{t.drafting_share * 100:.1f}%"
        rate = "n/a" if t.accept_rate is None else f"{t.accept_rate:.3f}"
        lines.append(
            f"{t.source:24} cycles={t.cycles:<7} drafted={t.drafting:<7} "
            f"share={share:<7} proposed={t.proposed:<7} accepted={t.accepted:<7} "
            f"accept_rate={rate} spec_misses={t.spec_misses}"
        )
    return lines


def sentence(t: Totals) -> str:
    """The claim, written so it cannot be mistaken for a cycle count."""
    if not t.cycles:
        return "no MTP cycles were observed at all"
    share = t.drafting_share * 100 if t.drafting_share is not None else 0.0
    return (
        f"the head drafted on {t.drafting} of {t.cycles} cycles ({share:.1f}%), "
        f"proposing {t.proposed} tokens of which {t.accepted} were accepted"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-log", type=pathlib.Path, required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--results", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    results = args.results
    if results is None:
        sys.path.insert(
            0,
            str(
                pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent"
            ),
        )
        import results as results_mod

        results = results_mod.default_path()

    log = from_log(args.server_log.read_text(errors="replace"))
    ledger = from_ledger(results.read_text(), args.batch, args.backend)

    for line in render([log, ledger]):
        logger.info("%s", line)

    if not shares_agree(log, ledger):
        logger.error(
            "the two sources disagree about the drafting share; the per-task "
            "windows are not seeing the same cycles as the server process, so "
            "every per-row drafting figure in this batch is suspect (#210)"
        )
        return 1
    logger.info("quote this: %s", sentence(ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
