"""Convert existing timestamps to ISO 8601 with an explicit offset.

Two shapes exist in the tree and they disagree with each other:

  results-*.jsonl   2026-09-06T07:43:45     naive, written in local time
  *-manifest.jsonl  2026-09-06T21:16:48Z    UTC

A readout that joins a row to its manifest entry compares those directly, which
is a four-hour error with no error message. This converts both to
`2026-09-06T07:43:45-0400`, with the offset written out.

"Local time" is the local time of the machine that wrote the file, and the
machines do not share one (#209). The M5 Max and the DGX Spark wrote
America/New_York; the Ryzen desktop wrote UTC, and its own rows say so from
row 113 on (`+0000`). So the zone comes from the ledger's hardware directory,
and a hardware directory with no entry in LEDGER_ZONES is refused, not guessed.

Runs once per shape. `tests/test_iso8601_timestamps.py` is what keeps it
converted, and it reads TIME_FIELDS and DATA_GLOBS from here.

    uv run python scripts/backfill_iso8601.py --dry-run
    uv run python scripts/backfill_iso8601.py --write
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import logging
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

#: The zone each machine's harness wrote naive timestamps in (#209). The Ryzen
#: entry is UTC: its naive `started` values fall 2 to 21 minutes after the
#: commit they ran, read as UTC, and four hours later read as New York.
LEDGER_ZONES = {
    "Cortex-X925-128GB-GB10": ZONE,
    "MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A": ZONE,
    "Ryzen9-7900X-32GB-RTX3080Ti-12GB": UTC,
}

#: Fields that hold a moment in time. The three `*_mtime` fields are file
#: modification times that run.py wrote naive until #209.
TIME_FIELDS = frozenset(
    {
        "started",
        "finished",
        "ended",
        "authored_at",
        "engine_built",
        "gguf_mtime",
        "ds4_server_mtime",
        "llamacpp_server_mtime",
    }
)
#: The live trial ledgers only. `docs/archive/` is preserved evidence and stays
#: byte-identical; `results-smoke.jsonl` is not a trial ledger.
DATA_GLOBS = (
    "benchmarks/agent/*.jsonl",
    "evidence/*.json",
    "hardware/*/results.jsonl",
)

NAIVE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}$")
#: `-04:00` instead of `-0400`. Equally valid ISO 8601 and already present in
#: the tree, so it is converted rather than refused -- the point of one
#: representation is that everything ends up in it, not that most things do.
OFFSET_COLON = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def zone_for(rel: pathlib.PurePath) -> ZoneInfo:
    """The zone a file's naive timestamps were written in, by repo-relative path.

    Files outside `hardware/` were written on the M5 Max, so New York. A
    hardware directory not in LEDGER_ZONES raises: a new machine must say its
    zone before its rows are converted.
    """
    parts = rel.parts
    if parts and parts[0] == "hardware":
        machine = parts[1] if len(parts) > 1 else ""
        if machine not in LEDGER_ZONES:
            raise ValueError(
                f"{rel}: no zone for hardware/{machine} in LEDGER_ZONES; "
                "add the zone its harness wrote in"
            )
        return LEDGER_ZONES[machine]
    return ZONE


def convert(value: str, zone: ZoneInfo = ZONE) -> str:
    """Return the canonical form, or raise if the shape is not recognized.

    A naive value is read in `zone`, the zone of the machine that wrote it. A
    `Z` value is read as UTC and moved to the same zone, so the two files
    finally express one instant the same way.

    Refuses rather than guesses on a local time a DST change makes ambiguous
    (the hour that repeats in the fall) or nonexistent (the hour skipped in the
    spring). There is nothing in the string to settle it, and picking one
    silently is how a run lands an hour from where it ran.
    """
    if CANONICAL.match(value):
        return value
    if OFFSET_COLON.match(value):
        # Already carries the right instant; only the offset spelling differs.
        return datetime.datetime.fromisoformat(value).strftime("%Y-%m-%dT%H:%M:%S%z")
    if UTC_Z.match(value):
        utc = datetime.datetime.fromisoformat(value)
        return utc.astimezone(zone).strftime("%Y-%m-%dT%H:%M:%S%z")
    if NAIVE.match(value):
        local = datetime.datetime.fromisoformat(value).replace(tzinfo=zone)
        earlier = local.replace(fold=0)
        later = local.replace(fold=1)
        if earlier.utcoffset() != later.utcoffset():
            raise ValueError(
                f"{value!r} is ambiguous or does not exist in {zone.key}: it "
                "falls in a DST change and the string does not say which "
                "offset. Fix it by hand."
            )
        return local.strftime("%Y-%m-%dT%H:%M:%S%z")
    raise ValueError(f"unrecognized timestamp shape: {value!r}")


def walk(
    obj: object,
    zone: ZoneInfo = ZONE,
    field: str | None = None,
    counts: collections.Counter[str] | None = None,
) -> object:
    """Convert every TIME_FIELDS value at any depth; count the ones that changed."""
    if isinstance(obj, dict):
        return {k: walk(v, zone, k, counts) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(v, zone, field, counts) for v in obj]
    if isinstance(obj, str) and field in TIME_FIELDS:
        out = convert(obj, zone)
        if counts is not None and out != obj:
            counts[field] += 1
        return out
    return obj


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--write", action="store_true")
    args = parser.parse_args()

    logs.configure()

    changed = 0
    for path in sorted(p for g in DATA_GLOBS for p in ROOT.glob(g)):
        rel = path.relative_to(ROOT)
        zone = zone_for(rel)
        counts: collections.Counter[str] = collections.Counter()
        text = path.read_text()
        if path.suffix == ".json":
            out = json.dumps(walk(json.loads(text), zone, counts=counts), indent=2)
            out += "\n"
        else:
            lines = [ln for ln in text.splitlines() if ln.strip()]
            out = "".join(
                json.dumps(
                    walk(json.loads(ln), zone, counts=counts), separators=(", ", ": ")
                )
                + "\n"
                for ln in lines
            )
        if out == text:
            continue
        changed += 1
        logger.info(
            "%s (%s): %s %s",
            rel,
            zone.key,
            "would rewrite" if args.dry_run else "rewriting",
            ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "format only",
        )
        if args.write:
            path.write_text(out)
    logger.info("%d file(s) %s", changed, "would change" if args.dry_run else "changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
