"""What the verdict does not say: is the passing code any good, and is it recalled?

Issue #4's premise is that a suite where nearly everything passes has stopped
measuring. `summarize.py` reports the verdict and the clock. This reports the
measurements that ride alongside it -- see `grade.py` -- and it reads them the
only supported way, through `results.py`.

Nothing here is a verdict. A large `ruff` delta does not fail a trial; it says
the trial passed while leaving lint behind, which is a different and more
interesting statement.

    uv run python benchmarks/agent/quality.py --task storage-put-and-sweep
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from collections import defaultdict
from typing import Any

import results
import provenance

logger = logging.getLogger(__name__)


def summarise(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict]:
    """Per (task, backend, client), collapse the secondary measurements.

    Absent measurements are excluded from their averages rather than counted as
    zero -- a gate that did not run is not a clean one, and `restored_verbatim`
    of None means the file was unreadable, not that the model wrote something
    original.
    """
    cells: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for r in rows:
        cells[(r["task"], r["backend"], r.get("client") or "claude")].append(r)

    out = {}
    for key, cell in cells.items():
        deltas = [r["gates_delta"] for r in cell if r.get("gates_delta")]
        decided = [r["restored_verbatim"] for r in cell
                   if r.get("restored_verbatim") is not None]
        hashes = [r["solution_sha256"] for r in cell if r.get("solution_sha256")]
        out[key] = {
            "n": len(cell),
            "passed": sum(results.verdict(r) for r in cell),
            "gated": len(deltas),
            "ruff": (sum(d.get("ruff", 0) for d in deltas) / len(deltas)
                     if deltas else None),
            "mypy": (sum(d.get("mypy", 0) for d in deltas) / len(deltas)
                     if deltas else None),
            "verbatim": sum(bool(v) for v in decided),
            "verbatim_of": len(decided),
            "distinct": len(set(hashes)),
            "hashed": len(hashes),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--results", default=str(pathlib.Path(__file__).parent
                                            / "results.jsonl"))
    p.add_argument("--task", action="append", help="repeatable; default all")
    args = p.parse_args(argv)
    provenance.configure()

    rows = results.trials(pathlib.Path(args.results))
    if args.task:
        rows = [r for r in rows if r["task"] in args.task]
    got = summarise(rows)
    measured = {k: v for k, v in got.items() if v["gated"] or v["hashed"]}
    if not measured:
        logger.warning("no row carries a gate or a solution hash -- every trial "
                       "here predates 2026-08-28, when the worktree was still "
                       "deleted with the solution in it")
        return 0

    logger.info("%-30s %-14s %-8s %6s %8s %8s %10s %10s",
                "task", "backend", "client", "pass", "ruff", "mypy",
                "verbatim", "distinct")
    for (task, backend, client), v in sorted(measured.items()):
        logger.info("%-30s %-14s %-8s %6s %8s %8s %10s %10s",
                    task[:30], backend[:14], client,
                    f"{v['passed']}/{v['n']}",
                    "-" if v["ruff"] is None else f"{v['ruff']:+.1f}",
                    "-" if v["mypy"] is None else f"{v['mypy']:+.1f}",
                    f"{v['verbatim']}/{v['verbatim_of']}" if v["verbatim_of"] else "-",
                    f"{v['distinct']}/{v['hashed']}" if v["hashed"] else "-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
