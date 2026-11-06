"""Stamp the `--dir` era onto the #355 orphan rows, once (#393).

A rebase orphaned three harness_head shas after a run had stamped them into 65
GB10 rows, and `git gc` collected the originals. `git log` cannot reach them,
so `dirfix.era()` read the rows as pre-`--dir` until an allowlist,
`REBASED_AFTER_FIX`, said otherwise. run.py now stamps each new row's era at
write time (`env.dir_era`), so the allowlist is retired, and this script gave
the 65 rows the stamp the allowlist stood in for.

Only these rows. A row whose harness_head `git log` reaches keeps being
classified from git: main is squash-only and never rewritten, so its answer
does not move. A row with no harness_head is not stamped either: nothing
proves its era, and a guess would be worse than the absence.

The proof for each orphan is the commit it was rebased into. The script
refuses unless that commit is on HEAD's history and descends from the fix.
It adds `env.dir_era` and changes nothing else; harness_head stays as written.
Dry by default; `--apply` rewrites the ledger under its lock.

    uv run python scripts/stamp_dir_era.py hardware/<dir>/results.jsonl
    uv run python scripts/stamp_dir_era.py hardware/<dir>/results.jsonl --apply
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import dirfix
import results

import logs

logger = logging.getLogger(__name__)

#: Each orphaned sha as the rows record it, and the reachable commit on main
#: it was rebased into (#355). The runs were after the fix; the trees were
#: identical, and only the parents changed. This was dirfix.REBASED_AFTER_FIX.
REBASED = {
    "6f56a28": "2b734f2",  # hardware/Cortex-X925-128GB-GB10 (5 rows)
    "62d7de1": "29fee77",  # (30 rows)
    "66d5e3a": "693f339",  # (30 rows)
}


def _is_ancestor(older: str, newer: str, repo: pathlib.Path) -> bool:
    r = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer],
        capture_output=True,
        text=True,
        cwd=repo,
        check=False,
    )
    return r.returncode == 0


def unproven(rebased: dict[str, str], repo: pathlib.Path) -> list[str]:
    """Each rebase target that does not prove its rows are after the fix."""
    return [
        f"{orphan} -> {target}: {target} is not on HEAD's history after {dirfix.FIX}"
        for orphan, target in sorted(rebased.items())
        if not (
            _is_ancestor(dirfix.FIX, target, repo)
            and _is_ancestor(target, "HEAD", repo)
        )
    ]


def era_stamp(target: str) -> dict[str, object]:
    return {
        "rule": dirfix.RULE,
        "fix": dirfix.FIX,
        "era": "after",
        "basis": f"#355 rebased into {target}",
    }


def plan(lines: list[str], rebased: dict[str, str]) -> tuple[list[str], dict[str, int]]:
    """Rewritten lines, and counts of what happened.

    Lines that are not JSON objects, and record lines (#305), pass through.
    """
    out: list[str] = []
    counts = {"stamped": 0, "already": 0, "other": 0, "unparsed": 0}
    for line in lines:
        try:
            row = json.loads(line) if line.strip() else None
        except ValueError:
            row = None
        if not isinstance(row, dict):
            if line.strip():
                counts["unparsed"] += 1
            out.append(line)
            continue
        if "record" in row:
            out.append(line)
            continue
        env = row.get("env")
        head = str((env or {}).get("harness_head", ""))[:7]
        if not isinstance(env, dict) or head not in rebased:
            counts["other"] += 1
        elif dirfix.STAMP_KEY in env:
            counts["already"] += 1
        else:
            env[dirfix.STAMP_KEY] = era_stamp(rebased[head])
            counts["stamped"] += 1
            out.append(json.dumps(row))
            continue
        out.append(line)
    return out, counts


def main(argv: list[str] | None = None) -> int:
    logs.configure(fmt=logs.PLAIN)
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("results", type=pathlib.Path)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)
    if gaps := unproven(REBASED, REPO):
        for gap in gaps:
            logger.error("refusing: %s", gap)
        return 1
    # --apply holds the ledger lock from this read to the replace below, so a
    # row run.py appends meanwhile waits rather than vanishes.
    held = results.ledger_lock(args.results) if args.apply else None
    with held or contextlib.nullcontext():
        return _run(args.results, apply=args.apply)


def _run(path: pathlib.Path, *, apply: bool) -> int:
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        logger.error("%s", exc)
        return 1
    rewritten, counts = plan(lines, REBASED)
    logger.info(
        "%d lines: %d to stamp, %d already stamped, %d other rows, %d unparsed",
        len(lines),
        counts["stamped"],
        counts["already"],
        counts["other"],
        counts["unparsed"],
    )
    if not apply:
        logger.info("dry run; pass --apply to write")
        return 0
    if not counts["stamped"]:
        logger.info("nothing to write")
        return 0
    results.replace_ledger(path, "\n".join(rewritten) + "\n")
    logger.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
