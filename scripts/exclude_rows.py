"""Mark rows from an aborted or void run as excluded, so they cannot publish.

A crashed sweep leaves real rows behind, and a partial sweep **looks good**:
when `stack_agent_ab.sh` died 8 tasks into a 15-task sweep on 2026-09-04, the
generated table gained `qwen38fnds4kimat | 8/8 | 123s` and ranked it above the
135-row cell it was supposed to be compared against. It only ran the eight
tasks it got through before the crash. That is the same shape as #142 -- a
truncated run flatters itself -- and it reached RECOMMENDATIONS.md in one
`splice_tables.py` invocation.

So rows from a void run are annotated the moment the run is declared void,
not left for a later reader to notice.

**Annotates, never rewrites.** `excluded` and `exclusion_reason` are added;
every measured field is left byte-for-byte alone. That is the standing rule
here -- an earlier attempt to recompute a stored field corrupted 30 rows.

Idempotent: a row already excluded is left alone, including its reason.

    uv run python scripts/exclude_rows.py <ledger> --backend qwen38fnds4kimat \\
        --since 2026-09-04T20:57 --reason "aborted sweep (#138)" --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

logger = logging.getLogger(__name__)


def selects(row: dict, backend: str | None, since: str | None, until: str | None) -> bool:
    """Whether this row is in the window being excluded.

    String comparison on ISO timestamps is deliberate: the ledger writes local
    ISO without a zone, and parsing then re-serialising invites a timezone bug
    in a tool whose whole job is not to alter rows.
    """
    if backend and row.get("backend") != backend:
        return False
    started = row.get("started")
    if not isinstance(started, str):
        return False
    if since and started < since:
        return False
    return not (until and started >= until)


def mark(
    ledger: pathlib.Path,
    *,
    backend: str | None,
    since: str | None,
    until: str | None,
    reason: str,
    apply: bool,
) -> tuple[int, int]:
    """(newly excluded, already excluded). Writes only when `apply`."""
    lines = ledger.read_text().splitlines()
    out: list[str] = []
    newly = already = 0
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        if not selects(row, backend, since, until):
            out.append(line)
            continue
        if row.get("excluded"):
            already += 1
            out.append(line)
            continue
        row["excluded"] = True
        row["exclusion_reason"] = reason
        newly += 1
        out.append(json.dumps(row))
    if apply and newly:
        ledger.write_text("\n".join(out) + "\n")
    return newly, already


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("ledger", type=pathlib.Path)
    p.add_argument("--backend")
    p.add_argument("--since", help="ISO start, inclusive")
    p.add_argument("--until", help="ISO end, exclusive")
    p.add_argument("--reason", required=True)
    p.add_argument("--apply", action="store_true", help="write; otherwise report only")
    args = p.parse_args(argv)

    if not (args.backend or args.since):
        logger.error("refusing to select every row: give --backend and/or --since")
        return 2
    newly, already = mark(
        args.ledger,
        backend=args.backend,
        since=args.since,
        until=args.until,
        reason=args.reason,
        apply=args.apply,
    )
    logger.info(
        "%s %d row(s); %d already excluded",
        "excluded" if args.apply else "would exclude",
        newly,
        already,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
