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
        --since 2026-09-04T20:57 --until 2026-09-04T21:27 \\
        --reason "aborted sweep (#138)" --apply

`--until` is REQUIRED for `--apply`, and the reason is this example. Written
without it, the same command re-run after the relaunch finished would have
excluded the good rows too: `--since` alone is a forward-open window, and
idempotency only protects rows that are already excluded. A void run is a
closed interval; say where it ends.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import subprocess
import sys

logger = logging.getLogger(__name__)


def _harness_running() -> bool:
    """Is a benchmark appending to the ledger right now?

    The run lock is not the check: the A/B protocol runs `run.py --no-lock`,
    which is exactly why the lock read as free during the 2026-09-04 incident.
    Ask the process table instead.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-f", "benchmarks/agent/run.py"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out.stdout.strip())


def selects(
    row: dict, backend: str | None, since: str | None, until: str | None
) -> bool:
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
        # Atomic. read -> transform -> write_text truncates the ledger first,
        # so a crash mid-write loses it entirely. os.replace swaps a complete
        # file in one step. It does NOT make the read-modify-write safe against
        # a concurrent append -- that is what the harness check in main() is
        # for -- it makes the failure mode "old file" instead of "half a file".
        tmp = ledger.with_suffix(ledger.suffix + ".tmp")
        tmp.write_text("\n".join(out) + "\n")
        os.replace(tmp, ledger)
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
    # A void run is a closed interval. Without an end, `--since` keeps matching
    # rows that do not exist yet, so re-running the same command after the next
    # batch would exclude ITS rows too -- and a backend alone would exclude
    # everything that backend ever produced.
    if args.apply and not args.until:
        logger.error(
            "refusing to --apply an open-ended window: pass --until. "
            "A run that has ended has an end timestamp; without one this "
            "command also excludes rows that do not exist yet."
        )
        return 2
    # Only writing is gated. Reporting while a batch runs is safe and useful --
    # it is how you decide what to exclude once the batch ends.
    if args.apply and _harness_running():
        logger.error(
            "refusing to rewrite the ledger while a benchmark is running: "
            "run.py appends to it, and a row written between the read and the "
            "write would be destroyed. Wait for the batch to finish."
        )
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
