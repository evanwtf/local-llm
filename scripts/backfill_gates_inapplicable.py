"""Mark the Python-gate results on Swift rows as not measured (#46).

ruff and mypy read `.py` files. On the Swift target repo they read nothing,
ruff prints "All checks passed", and the row records `gates_delta =
{"ruff": 0}` -- a clean gate that never ran. `grade.gate_applies()` stops new
rows doing this. This marks the rows written before it.

It adds `gates_inapplicable: true` and changes nothing else: the recorded
values stay as evidence, and `quality.py` skips a marked row's delta.

A row is marked only if its `target_repo` is in SWIFT_TARGETS and it records
a gate delta. Dry by default; `--apply` rewrites the file.

    uv run python scripts/backfill_gates_inapplicable.py <results.jsonl>
    uv run python scripts/backfill_gates_inapplicable.py <results.jsonl> --apply
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import results

import logs

logger = logging.getLogger(__name__)

# Target repos that track no .py file (checked with `git ls-files '*.py'` on
# 2026-09-25). Add a repo here only after the same check.
SWIFT_TARGETS = frozenset({"~/git/monitor"})


def plan(lines: list[str]) -> tuple[list[str], dict[str, int]]:
    """Rewritten lines, and counts of what happened.

    Lines that are not JSON objects pass through untouched.
    """
    out: list[str] = []
    counts = {"marked": 0, "already": 0, "no_gate": 0, "other": 0, "unparsed": 0}
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if not isinstance(row, dict):
            counts["unparsed"] += 1
            out.append(line)
            continue
        if row.get("target_repo") not in SWIFT_TARGETS:
            counts["other"] += 1
        elif row.get("gates_inapplicable") is True:
            counts["already"] += 1
        elif not row.get("gates_delta"):
            counts["no_gate"] += 1
        else:
            row["gates_inapplicable"] = True
            counts["marked"] += 1
            out.append(json.dumps(row))
            continue
        out.append(line)
    return out, counts


def main(argv: list[str] | None = None) -> int:
    logs.configure(fmt=logs.PLAIN)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=pathlib.Path)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    # --apply holds the ledger lock from this read to the replace below, so a
    # row run.py appends meanwhile waits rather than vanishes (b7a366b, #2).
    held = results.ledger_lock(args.results) if args.apply else None
    with held or contextlib.nullcontext():
        return _run(args.results, apply=args.apply)


def _run(path: pathlib.Path, *, apply: bool) -> int:
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        logger.error("%s", exc)
        return 1
    rewritten, counts = plan(lines)
    logger.info(
        "%d rows: %d to mark, %d already marked, %d Swift without a gate, "
        "%d not Swift, %d unparsed",
        len(lines),
        counts["marked"],
        counts["already"],
        counts["no_gate"],
        counts["other"],
        counts["unparsed"],
    )
    if not apply:
        logger.info("dry run; pass --apply to write")
        return 0
    if not counts["marked"]:
        logger.info("nothing to write")
        return 0
    results.replace_ledger(path, "\n".join(rewritten) + "\n")
    logger.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
