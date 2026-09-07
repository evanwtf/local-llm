"""Convert existing timestamps to ISO 8601 with an explicit offset.

Two shapes exist in the tree and they disagree with each other:

  results-*.jsonl   2026-09-06T07:43:45     naive, written in local time
  *-manifest.jsonl  2026-09-06T21:16:48Z    UTC

A readout that joins a row to its manifest entry compares those directly, which
is a four-hour error with no error message. This converts both to
`2026-09-06T07:43:45-0400` -- America/New_York, offset written out.

Runs once. `tests/test_iso8601_timestamps.py` is what keeps it converted.

    uv run python scripts/backfill_iso8601.py --dry-run
    uv run python scripts/backfill_iso8601.py --write
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("America/New_York")

TIME_FIELDS = frozenset(
    {"started", "finished", "ended", "authored_at", "engine_built"}
)
DATA_GLOBS = ("benchmarks/agent/*.jsonl", "evidence/*.json")

NAIVE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}$")
#: `-04:00` instead of `-0400`. Equally valid ISO 8601 and already present in
#: the tree, so it is converted rather than refused -- the point of one
#: representation is that everything ends up in it, not that most things do.
OFFSET_COLON = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def convert(value: str) -> str:
    """Return the canonical form, or raise if the shape is not recognised.

    A naive value is read as America/New_York because that is the machine that
    wrote it. A `Z` value is read as UTC and moved to the same zone, so the two
    files finally express one instant the same way.

    Refuses rather than guesses on an ambiguous local time -- the hour that
    repeats at the end of DST. There is nothing in the string to disambiguate
    it, and picking one silently is how a run lands an hour from where it ran.
    """
    if CANONICAL.match(value):
        return value
    if OFFSET_COLON.match(value):
        # Already carries the right instant; only the offset spelling differs.
        return datetime.datetime.fromisoformat(value).strftime("%Y-%m-%dT%H:%M:%S%z")
    if UTC_Z.match(value):
        utc = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return utc.astimezone(ZONE).strftime("%Y-%m-%dT%H:%M:%S%z")
    if NAIVE.match(value):
        local = datetime.datetime.fromisoformat(value).replace(tzinfo=ZONE)
        earlier = local.replace(fold=0)
        later = local.replace(fold=1)
        if earlier.utcoffset() != later.utcoffset():
            raise ValueError(
                f"{value!r} is ambiguous: it occurs twice on the DST fall-back "
                "day and the string does not say which. Fix it by hand."
            )
        return local.strftime("%Y-%m-%dT%H:%M:%S%z")
    raise ValueError(f"unrecognised timestamp shape: {value!r}")


def walk(obj: object, field: str | None = None) -> object:
    if isinstance(obj, dict):
        return {k: walk(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(v, field) for v in obj]
    if isinstance(obj, str) and field in TIME_FIELDS:
        return convert(obj)
    return obj


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--write", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    changed = 0
    for path in sorted(p for g in DATA_GLOBS for p in ROOT.glob(g)):
        text = path.read_text()
        if path.suffix == ".json":
            out = json.dumps(walk(json.loads(text)), indent=2) + "\n"
        else:
            lines = [ln for ln in text.splitlines() if ln.strip()]
            out = "".join(
                json.dumps(walk(json.loads(ln)), separators=(", ", ": ")) + "\n"
                for ln in lines
            )
        if out == text:
            continue
        changed += 1
        logger.info("%s: %s", path.relative_to(ROOT),
                    "would rewrite" if args.dry_run else "rewriting")
        if args.write:
            path.write_text(out)
    logger.info("%d file(s) %s", changed, "would change" if args.dry_run else "changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
