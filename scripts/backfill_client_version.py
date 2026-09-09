"""Fill `client_version` on rows that predate it, and only where it is known.

225b90c added `client_version` to the row so #104's finding -- OpenCode
1.18.26 -> 1.18.27 roughly doubling median turns -- can be applied to a single
row. Rows written before that carry the version only inside `env`, keyed by
client name alongside every other client on the machine, which is the join
that made the finding inapplicable in the first place.

The value is derivable for those rows: `env[row["client"]]` is the version of
the client that ran. That is a lookup, not a guess, and it is the only thing
this fills.

**A row whose version cannot be established stays unmarked.** #131 says so
explicitly. An absent `client_version` means "not established"; writing a
plausible value would make it mean "measured", and the two must not merge.

Dry by default. `--apply` rewrites the file, and prints a summary first.

    uv run python scripts/backfill_client_version.py <results.jsonl>
    uv run python scripts/backfill_client_version.py <results.jsonl> --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

logger = logging.getLogger(__name__)


def derive(row: dict) -> str | None:
    """The version of the client that took this row, or None.

    A lookup of `env[client]`, nothing more. No inference from timestamps, no
    nearest-neighbour from adjacent rows: both would invent a measurement.
    """
    client = row.get("client")
    env = row.get("env")
    if not isinstance(client, str) or not isinstance(env, dict):
        return None
    value = env.get(client)
    return value if isinstance(value, str) and value.strip() else None


def plan(lines: list[str]) -> tuple[list[str], dict[str, int]]:
    """Rewritten lines, and counts of what happened.

    Lines that are not JSON objects pass through untouched -- a results file
    is append-only evidence, and a backfill must not be able to drop a row it
    failed to parse.
    """
    out: list[str] = []
    counts = {"filled": 0, "already": 0, "unknowable": 0, "unparsed": 0}
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        try:
            row = json.loads(line)
        except ValueError:
            counts["unparsed"] += 1
            out.append(line)
            continue
        if not isinstance(row, dict):
            counts["unparsed"] += 1
            out.append(line)
            continue
        if row.get("client_version"):
            counts["already"] += 1
            out.append(line)
            continue
        got = derive(row)
        if got is None:
            counts["unknowable"] += 1
            out.append(line)
            continue
        row["client_version"] = got
        counts["filled"] += 1
        out.append(json.dumps(row))
    return out, counts


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=pathlib.Path)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    try:
        lines = args.results.read_text().splitlines()
    except OSError as exc:
        logger.error("%s", exc)
        return 1
    rewritten, counts = plan(lines)
    logger.info(
        "%d rows: %d fillable, %d already set, %d cannot be established, %d unparsed",
        len(lines),
        counts["filled"],
        counts["already"],
        counts["unknowable"],
        counts["unparsed"],
    )
    if not args.apply:
        logger.info("dry run; pass --apply to write")
        return 0
    if not counts["filled"]:
        logger.info("nothing to write")
        return 0
    args.results.write_text("\n".join(rewritten) + "\n")
    logger.info("wrote %s", args.results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
