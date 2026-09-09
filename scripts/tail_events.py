"""Count num_turns > 20 events across the ledger, by task and by backend (#191).

The #191 tail -- a task that runs hundreds of turns instead of tens -- is a
rare stochastic degeneration. It moved tasks between runs: parser-mbox-quoting
ran 334 turns in one run and 24 in another on the same build. This script
counts how often the tail fires, grouped by task and by backend, so a reader
can see whether it has structure (concentrated in one task or backend) or is
uniform noise. It needs no machine time: it reads the ledger only.

A row is a tail event when num_turns > 20. A missing or zero num_turns is not
a tail event. The rate is tail events over total rows in the group, so a task
or backend that runs many trials is not over-weighted by volume alone.
"""

from __future__ import annotations

import argparse
import collections
import logging
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "benchmarks" / "agent"))

import results

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

TAIL_TURNS = 20


def tail_events(rows: list[dict]) -> list[dict]:
    """Rows whose num_turns exceeds TAIL_TURNS. None or 0 is not a tail."""
    return [r for r in rows if (r.get("num_turns") or 0) > TAIL_TURNS]


def _table(
    rows: list[dict], key: str | tuple[str, ...]
) -> list[tuple[str, int, int, float]]:
    """Group rows by `key`; return (group, tail, total, rate) sorted by tail desc.

    `key` may be a tuple of fields, joined with " / " for the group label, so a
    task x backend cross-tab is the same code as a single-field grouping.
    """
    total: collections.Counter = collections.Counter()
    tail: collections.Counter = collections.Counter()
    for r in rows:
        g = _group(r, key)
        total[g] += 1
        if (r.get("num_turns") or 0) > TAIL_TURNS:
            tail[g] += 1
    out = [
        (g, tail[g], total[g], tail[g] / total[g] if total[g] else 0.0) for g in total
    ]
    out.sort(key=lambda t: (-t[1], -t[3]))
    return out


def _group(row: dict, key: str | tuple[str, ...]) -> str:
    if isinstance(key, str):
        return row.get(key) or "(none)"
    return " / ".join(row.get(k) or "(none)" for k in key)


def _wilson(k: int, n: int) -> tuple[float, float]:
    """Wilson 95% confidence interval for a proportion k/n, as fractions.

    The normal approximation is wrong at the edges (0/n, n/n), which is exactly
    where a tail rate lands; Wilson stays inside [0, 1] and is the interval the
    peer's census used. z = 1.96 for 95%.
    """
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5 / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _report(rows: list[dict], key: str | tuple[str, ...], label: str) -> None:
    logger.info("=== by %s ===", label)
    logger.info("%-40s %6s %6s %8s  %s", label, "tail", "total", "rate", "95% CI")
    for g, t, n, rate in _table(rows, key):
        lo, hi = _wilson(t, n)
        logger.info(
            "%-40s %6d %6d %7.1f%%  [%4.1f, %4.1f]",
            g,
            t,
            n,
            rate * 100,
            lo * 100,
            hi * 100,
        )
    logger.info("")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "paths",
        nargs="*",
        help="results.jsonl files; default: every hardware/*/results.jsonl",
    )
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)

    paths = [pathlib.Path(x) for x in args.paths]
    if not paths:
        paths = sorted(pathlib.Path("hardware").glob("*/results.jsonl"))
    if not paths:
        logger.error("no ledger found under hardware/")
        return 1

    rows: list[dict] = []
    for path in paths:
        rows.extend(results.load(path))
    if not rows:
        logger.error("no rows in %s", ", ".join(str(p) for p in paths))
        return 1

    tails = tail_events(rows)
    logger.info(
        "rows: %d, tail events (num_turns > %d): %d", len(rows), TAIL_TURNS, len(tails)
    )
    logger.info("")
    _report(rows, "task", "task")
    _report(rows, "backend", "backend")
    _report(rows, ("task", "backend"), "task x backend")
    return 0


if __name__ == "__main__":
    sys.exit(main())
