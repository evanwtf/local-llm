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
        --since 2026-09-04T20:57-0400 --until 2026-09-04T21:27-0400 \\
        --reason "aborted sweep (#138)" --apply

Both bounds need a UTC offset. The window is compared by instant, not by
string: the ledgers hold `-0400` and `+0000` rows, and as strings
`2026-10-05T12:30:00+0000` sorts after `2026-10-05T09:00:00-0400` although it
is half an hour earlier. A bound or a selected row with no offset names no
instant, and is refused rather than guessed into a zone.

`--until` is REQUIRED for `--apply`, and the reason is this example. Written
without it, the same command re-run after the relaunch finished would have
excluded the good rows too: `--since` alone is a forward-open window, and
idempotency only protects rows that are already excluded. A void run is a
closed interval; say where it ends.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import results

import logs

logger = logging.getLogger(__name__)


def _harness_running() -> bool:
    """Is a benchmark appending to the ledger right now?

    The run lock is not the check: the A/B protocol runs `run.py --no-lock`,
    which is exactly why the lock read as free during the 2026-09-04 incident.
    Ask the process table instead.

    Fails closed. A missing pgrep, a timeout, or pgrep's own error (exit 2 or
    3) is no answer, and no answer must not authorize a rewrite. pgrep exits 1
    for "no match", and that is the only "not running".
    """
    try:
        out = subprocess.run(
            ["pgrep", "-f", "benchmarks/agent/run.py"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("cannot tell whether run.py is running (%s); assuming it is", exc)
        return True
    if out.returncode == 1:
        return False
    if out.returncode != 0:
        logger.error(
            "pgrep exited %d (%s); assuming run.py is running",
            out.returncode,
            out.stderr.strip(),
        )
    return True


def instant(value: str, what: str) -> datetime.datetime:
    """Parse an ISO 8601 timestamp that carries a UTC offset.

    Raises ValueError on a naive one. It names no instant, and the ledgers
    hold two zones (`-0400` on the M5 Max and the Spark, `+0000` on the
    cluster and the Ryzen), so any guess is four hours wrong on one of them.
    """
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{what} {value!r} is not an ISO 8601 timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(
            f"{what} {value!r} has no UTC offset; write it as e.g. {value}-0400"
        )
    return parsed


def selects(
    row: dict, backend: str | None, since: str | None, until: str | None
) -> bool:
    """Whether this row is in the window being excluded.

    Compared as instants, never as strings (review of b7a366b, finding 3).
    The parse is for the comparison only: the row is never re-serialized, so
    its stored timestamp keeps its own spelling.
    """
    if backend and row.get("backend") != backend:
        return False
    started = row.get("started")
    if not isinstance(started, str):
        return False
    if not (since or until):
        return True
    at = instant(started, f"row {row.get('task')}-{row.get('trial')} started")
    if since and at < instant(since, "--since"):
        return False
    return not (until and at >= instant(until, "--until"))


def mark(
    ledger: pathlib.Path,
    *,
    backend: str | None,
    since: str | None,
    until: str | None,
    reason: str,
    apply: bool,
) -> tuple[int, int]:
    """(newly excluded, already excluded). Writes only when `apply`.

    With `apply`, the ledger lock is held from the read to the replace, so a
    row `run.py` appends meanwhile waits for the rewrite instead of vanishing.
    """
    if apply:
        with results.ledger_lock(ledger):
            return _mark(ledger, backend, since, until, reason, apply)
    return _mark(ledger, backend, since, until, reason, apply)


def _mark(
    ledger: pathlib.Path,
    backend: str | None,
    since: str | None,
    until: str | None,
    reason: str,
    apply: bool,
) -> tuple[int, int]:
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
        # Atomic: a crash mid-write leaves the old file, never half of one.
        results.replace_ledger(ledger, "\n".join(out) + "\n")
    return newly, already


def main(argv: list[str] | None = None) -> int:
    logs.configure(fmt=logs.PLAIN)
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("ledger", type=pathlib.Path)
    p.add_argument("--backend")
    p.add_argument("--since", help="ISO start with offset, inclusive")
    p.add_argument("--until", help="ISO end with offset, exclusive")
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
    try:
        for flag, value in (("--since", args.since), ("--until", args.until)):
            if value:
                instant(value, flag)
    except ValueError as exc:
        logger.error("%s", exc)
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
    try:
        newly, already = mark(
            args.ledger,
            backend=args.backend,
            since=args.since,
            until=args.until,
            reason=args.reason,
            apply=args.apply,
        )
    except ValueError as exc:
        logger.error("refusing: %s", exc)
        return 2
    logger.info(
        "%s %d row(s); %d already excluded",
        "excluded" if args.apply else "would exclude",
        newly,
        already,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
