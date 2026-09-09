"""Did MTP draft during the batch, or only before it? (#39, #151, #210)

One ds4 server log, split at one byte, counted on each side.

**Why a split and not a total.** A server log spans the whole sweep, and the
sweep is two different populations of request. Before the batch, `run.py`
sends the server its own start-up prompts, which carry no tool schema. After
it, every request comes from the coding agent and every one of them carries
one. A total over the whole file mixes them, and the mixture is exactly what
made "MTP is on and doing nothing" indistinguishable from "MTP is on and the
agent never lets it run" for two weeks.

**Why the offset is read and not guessed.** `run.py` prints the byte at which
it began recording -- `recording MTP draft acceptance per row from <log>
(ds4-mtp-timing), starting at byte N`. Pass the sweep log with `--sweep-log`
and this script takes N from that line. `--offset` is for a log whose sweep
log is gone; a wrong offset moves the boundary between the two populations
and is the one input that can manufacture either answer.

**What the counts mean.** A `MTP timing` line is emitted once per decode
cycle on the speculative path. `drafting` counted a draft; `bypassed` reached
the scheduler and was turned away (`verifier=scheduler-bypass`). Zero lines of
either kind is a third thing, and the important one: the request never
reached the speculative path at all.

    uv run python scripts/mtp_log_split.py \
        --server-log evidence/0039-mtp-ab-logs/server-new-sweep1.log \
        --sweep-log  evidence/0039-mtp-ab-logs/new-sweep1.log
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import sys
from typing import Any

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import mtp_timing

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

#: run.py's own line. The byte is what makes the split reproducible.
OFFSET_LINE = re.compile(r"recording MTP draft acceptance .*?starting at byte (\d+)")


def offset_from_sweep_log(text: str) -> int:
    """The recording offset run.py printed, or a refusal.

    More than one match means more than one server was recorded into the same
    sweep log, and the first is not obviously the right one -- refuse rather
    than pick.
    """
    found = OFFSET_LINE.findall(text)
    if not found:
        raise ValueError(
            "no 'starting at byte N' line in the sweep log: this run did not "
            "record draft counters, so there is no boundary to split on"
        )
    if len(set(found)) > 1:
        raise ValueError(
            f"the sweep log records {len(set(found))} different offsets "
            f"({', '.join(sorted(set(found)))}); split each server's log "
            "separately rather than guessing which one this is"
        )
    return int(found[0])


def count(text: str) -> dict[str, Any]:
    """MTP cycle counts for one region of a server log."""
    counters = mtp_timing.read(text)
    return {
        "mtp_timing_lines": len(counters.cycles),
        "drafting": counters.drafting,
        "bypassed": counters.bypassed,
        "proposed": counters.proposed,
        "accepted": counters.accepted,
        "accept_rate": counters.accept_rate,
        "drafting_share": counters.drafting_share,
    }


def split(server_log: bytes, offset: int) -> dict[str, Any]:
    """Counts before and after `offset`, plus the offset itself.

    An offset past the end of the file gives an empty `after` -- which reads
    as "the batch drafted nothing" and would be a lie. Refuse it.
    """
    if offset < 0:
        raise ValueError(f"offset {offset} is negative")
    if offset > len(server_log):
        raise ValueError(
            f"offset {offset} is past the end of a {len(server_log)}-byte log; "
            "an empty 'after' region reads as a null result and is not one"
        )

    def decode(part: bytes) -> str:
        return part.decode("utf-8", errors="replace")

    return {
        "offset": offset,
        "size": len(server_log),
        "before": count(decode(server_log[:offset])),
        "after": count(decode(server_log[offset:])),
    }


def main(argv: list[str] | None = None) -> int:
    logs.configure(fmt=logs.PLAIN)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--server-log", type=pathlib.Path, required=True)
    p.add_argument(
        "--sweep-log",
        type=pathlib.Path,
        help="run.py's log for the same sweep; the offset is read from it",
    )
    p.add_argument("--offset", type=int, help="use only when the sweep log is gone")
    p.add_argument("--json", action="store_true", help="machine-readable, one object")
    args = p.parse_args(argv)

    if (args.sweep_log is None) == (args.offset is None):
        logger.info("give exactly one of --sweep-log or --offset")
        return 2
    try:
        offset = (
            args.offset
            if args.offset is not None
            else offset_from_sweep_log(args.sweep_log.read_text(errors="replace"))
        )
        got = split(args.server_log.read_bytes(), offset)
    except (OSError, ValueError) as exc:
        logger.info("refused: %s", exc)
        return 2

    if args.json:
        logger.info("%s", json.dumps(got, indent=2, sort_keys=True))
        return 0
    logger.info("%s", args.server_log)
    logger.info("  split at byte %d of %d", got["offset"], got["size"])
    for region in ("before", "after"):
        c = got[region]
        logger.info(
            "  %-6s lines %-5d drafting %-5d bypassed %-5d  proposed %-5d "
            "accepted %-5d  accept_rate %s  drafting_share %s",
            region,
            c["mtp_timing_lines"],
            c["drafting"],
            c["bypassed"],
            c["proposed"],
            c["accepted"],
            "n/a" if c["accept_rate"] is None else f"{c['accept_rate']:.3f}",
            "n/a" if c["drafting_share"] is None else f"{c['drafting_share']:.3f}",
        )
    if got["before"]["drafting"] and not got["after"]["mtp_timing_lines"]:
        logger.info(
            "  the same server drafted before the batch and emitted no MTP line "
            "during it: the treatment was applied and the traffic refused it"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
