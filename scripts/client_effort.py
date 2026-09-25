"""Turns and tokens per task, per client, for one backend (#707).

A client A/B holds the server fixed and swaps the agent loop, so pass rate and
wall time are only half the answer: the other half is how much work each
client asked of the model to get there. This prints, per task and per client,
the median num_turns, the median output tokens, and the median peak input
tokens, plus the same three over every row per client.

The counts come from each client's own accounting (see run.py's parsers), so
they are recorded, not equated: OpenCode's `num_turns` counts step_finish
events and Unreal Agent's counts model_response events. Compare within a
client across tasks freely; compare across clients as "roughly how many model
round trips", not as the same quantity.

A missing value (a timed-out trial parses nothing) is left out of its median
and counted, so a cell reads "n=2 of 3" rather than silently shrinking.
"""

from __future__ import annotations

import argparse
import collections
import logging
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "benchmarks" / "agent"))

import results

sys.path.insert(0, str(HERE / "lib"))

import logs

logger = logging.getLogger(__name__)

FIELDS = ("num_turns", "output_tokens", "input_tokens")


def select(rows: list[dict], backend: str, batch: str | None) -> list[dict]:
    """Rows for `backend`, and for `batch` when one is given."""
    out = [r for r in rows if r.get("backend") == backend]
    if batch is not None:
        out = [r for r in out if r.get("batch") == batch]
    return out


def medians(rows: list[dict]) -> dict[str, tuple[float | None, int]]:
    """{field: (median over rows that carry it, how many carry it)}."""
    out: dict[str, tuple[float | None, int]] = {}
    for field in FIELDS:
        values = [r[field] for r in rows if isinstance(r.get(field), int | float)]
        out[field] = (statistics.median(values) if values else None, len(values))
    return out


def by_task_client(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in rows:
        groups[(r.get("task") or "(none)", r.get("client") or "(none)")].append(r)
    return dict(groups)


def _cell(stat: tuple[float | None, int], total: int) -> str:
    value, n = stat
    if value is None:
        return f"- (0 of {total})"
    shown = f"{value:,.0f}"
    return shown if n == total else f"{shown} ({n} of {total})"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ledger", type=pathlib.Path, help="a results.jsonl")
    p.add_argument("--backend", required=True)
    p.add_argument("--batch", help="keep only rows stamped with this batch id")
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)

    rows = select(results.load(args.ledger), args.backend, args.batch)
    if not rows:
        logger.error("no rows for backend %s batch %s", args.backend, args.batch)
        return 1

    groups = by_task_client(rows)
    clients = sorted({c for _, c in groups})
    logger.info("| task | client | turns | output tokens | peak input tokens |")
    logger.info("|---|---|---|---|---|")
    for task, client in sorted(groups):
        cell = groups[(task, client)]
        m = medians(cell)
        logger.info(
            "| `%s` | %s | %s | %s | %s |",
            task,
            client,
            *(_cell(m[f], len(cell)) for f in FIELDS),
        )
    logger.info("")
    for client in clients:
        cell = [r for r in rows if r.get("client") == client]
        m = medians(cell)
        logger.info(
            "%s, all %d rows: median turns %s, output tokens %s, peak input %s",
            client,
            len(cell),
            *(_cell(m[f], len(cell)) for f in FIELDS),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
