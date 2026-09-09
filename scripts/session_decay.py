"""Does a session get worse the longer the server runs? (#120)

#120 has three numbers and no mechanism: 36/45 on a continuous server, 42/45
with a restart between trials, 38/45 with the disk-KV budget raised 4x. Six
pass-rate points hide in an operational variable, and for an agent someone
actually uses, a server that degrades with uptime is a product defect rather
than a benchmark artifact.

The rows can answer part of it without new machine time. Every row carries a
`trial` index, so pass rate by trial index says whether later trials in a
cycle do worse -- which is the shape #120 describes and #112 observed
directly (13/13/10 on one arm, 10/9/6 on another).

**What this cannot do.** `trial` counts repetitions of a cell, not uptime, and
a restart-between-trials cycle resets the server between them while a
continuous run does not. So a decline here is consistent with server state and
also with drift in the machine, and separating them needs the restart arm --
which is exactly the comparison #120 already has. This narrows where to look;
it does not settle it.

    uv run python scripts/session_decay.py <results.jsonl> [--backend NAME]
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import pathlib
import sys

logger = logging.getLogger(__name__)


def load_rows(path: pathlib.Path) -> list[dict]:
    """Rows that carry a verdict and a trial index. Others are not evidence."""
    out: list[dict] = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("excluded"):
            continue
        if not isinstance(row.get("passed"), bool):
            continue
        if not isinstance(row.get("trial"), int):
            continue
        out.append(row)
    return out


def by_trial(rows: list[dict]) -> dict[int, tuple[int, int]]:
    """trial index -> (passed, total)."""
    table: dict[int, list[int]] = collections.defaultdict(lambda: [0, 0])
    for row in rows:
        table[row["trial"]][1] += 1
        if row["passed"]:
            table[row["trial"]][0] += 1
    return {k: (v[0], v[1]) for k, v in sorted(table.items())}


def report(table: dict[int, tuple[int, int]], min_per_trial: int = 5) -> str:
    if not table:
        return "no rows with a verdict and a trial index"
    lines = ["trial | passed / total | rate"]
    for trial, (passed, total) in sorted(table.items()):
        lines.append(f"{trial:>5} | {passed:>6} / {total:<5} | {passed / total:6.1%}")
    thin = [t for t, (_, n) in table.items() if n < min_per_trial]
    if thin:
        lines.append("")
        lines.append(
            f"NOTE: trials {thin} have fewer than {min_per_trial} rows. "
            "A rate over three rows is a shape, not a rate."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=pathlib.Path)
    p.add_argument("--backend", default=None)
    p.add_argument("--client", default=None)
    args = p.parse_args(argv)

    rows = load_rows(args.results)
    if args.backend:
        rows = [r for r in rows if r.get("backend") == args.backend]
    if args.client:
        rows = [r for r in rows if r.get("client") == args.client]
    logger.info(
        "%d rows with a verdict%s",
        len(rows),
        f" for {args.backend or ''} {args.client or ''}".rstrip(),
    )
    logger.info("")
    logger.info("%s", report(by_trial(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
