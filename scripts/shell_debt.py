#!/usr/bin/env python3
"""How much shell is left, and how much of it can still misidentify a process.

#235's done condition is two numbers, and they are not the same number:

    a 90% line reduction     3,589 -> 359 lines under scripts/
    zero pgrep/pkill         every file that matches a process by NAME

AGENTS.md quotes both, and a quoted number goes stale the day after it is
written. This computes them, so the next reader re-runs it instead of
believing me.

**They come apart, and knowing where matters.** Line count is readability;
`pgrep` is the one that has cost measurements. A pattern matches the shell
that quoted it, so `until ! pgrep -f '<driver>.sh'` waits on itself -- seven
such shells were found orphaned on 2026-09-08, up to 6h30m old, and the commit
guard refused every commit for hours because it matched them. Bracketing the
first character only ever protected against *self*-match; it does nothing
about a second copy, a waiter, or an editor holding the filename.

A `.sh` counts as REPLACED when a Python file does its job -- which is not
always its own name. `restart_between_trials_armB.sh` is replaced by
`restart_between_trials.py`, which grew both arms in #261, and
`disk_kv_mechanism_test.sh` by `disk_kv_mechanism.py`, which had to drop the
`_test` because pytest collects `*_test.py` from the whole repo. A
name-matching check reports both as unreplaced, which is how this script came
to have an explicit table instead.

    uv run python scripts/shell_debt.py
    uv run python scripts/shell_debt.py --json

Replacement is not retirement. AGENTS.md: a `.sh` is deleted only once its
Python replacement has produced a run that agrees with it.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The scope, as a command anyone can re-run.
SCOPE = "scripts/*.sh"

#: The line target: 10% of the 3,589 lines standing when #235 was written.
BASELINE = 3589
TARGET = 359

#: `<shell>: <the Python that does its job>`. Not a name match -- see the
#: docstring. A file absent from here has no replacement yet.
REPLACED = {
    "scripts/greedy_mtp_ab.sh": "scripts/greedy_mtp_ab.py",
    "scripts/mtp_treatment_gate.sh": "scripts/mtp_treatment_gate.py",
    "scripts/route_agent_ab.sh": "scripts/route_agent_ab.py",
    "scripts/targets_ab.sh": "scripts/targets_ab.py",
    "scripts/strip_toggle_ab.sh": "scripts/strip_toggle_ab.py",
    "scripts/stack_agent_ab.sh": "scripts/stack_agent_ab.py",
    "scripts/metal_knob_ab.sh": "scripts/metal_knob_ab.py",
    "scripts/decode_ab.sh": "scripts/decode_ab.py",
    # #261 gave one module both arms; armB.sh is `--arm B`.
    "scripts/restart_between_trials.sh": "scripts/restart_between_trials.py",
    "scripts/restart_between_trials_armB.sh": "scripts/restart_between_trials.py",
    # `_test` had to go: pytest collects *_test.py from the whole repo.
    "scripts/disk_kv_mechanism_test.sh": "scripts/disk_kv_mechanism.py",
    "scripts/lib/ds4_server.sh": "scripts/lib/ds4_server.py",
    "scripts/lib/mlx_serve.sh": "scripts/lib/mlx_serve.py",
}

BY_NAME = re.compile(r"\b(pgrep|pkill)\b")


def shell_files(root: pathlib.Path = ROOT) -> list[str]:
    """Tracked shell under scripts/. The index, not the working tree (#251)."""
    out = subprocess.run(
        ["git", "ls-files", SCOPE],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(out.stdout.split())


def survey(root: pathlib.Path = ROOT) -> dict[str, object]:
    files = []
    for rel in shell_files(root):
        text = (root / rel).read_text()
        replacement = REPLACED.get(rel)
        files.append(
            {
                "path": rel,
                "lines": len(text.splitlines()),
                "by_name": len(BY_NAME.findall(text)),
                "replaced_by": replacement,
                # A mapping to a file that has since been renamed is worse
                # than no mapping: it reports done and is not.
                "replacement_exists": bool(
                    replacement and (root / replacement).exists()
                ),
            }
        )
    lines = sum(int(f["lines"]) for f in files)
    by_name = sum(int(f["by_name"]) for f in files)
    unreplaced = [f for f in files if not f["replacement_exists"]]
    return {
        "scope": SCOPE,
        "files": files,
        "total_lines": lines,
        "total_by_name": by_name,
        "baseline_lines": BASELINE,
        "target_lines": TARGET,
        "reduction_pct": round(100 * (1 - lines / BASELINE), 1),
        "by_name_unreplaced": sum(int(f["by_name"]) for f in unreplaced),
        "lines_if_replaced_deleted": sum(int(f["lines"]) for f in unreplaced),
        "by_name_if_replaced_deleted": sum(int(f["by_name"]) for f in unreplaced),
    }


def report(s: dict[str, object]) -> None:
    logger.info("scope: git ls-files '%s'", s["scope"])
    logger.info(
        "now:    %d files, %s lines, %s pgrep/pkill calls",
        len(s["files"]),  # type: ignore[arg-type]
        s["total_lines"],
        s["total_by_name"],
    )
    logger.info(
        "target: %s lines (10%% of the %s standing at #235); %s%% of the way",
        s["target_lines"],
        s["baseline_lines"],
        s["reduction_pct"],
    )
    logger.info("")
    logger.info("%-46s %6s %6s  %s", "file", "lines", "byname", "replaced by")
    for f in sorted(s["files"], key=lambda f: -int(f["lines"])):  # type: ignore[arg-type]
        logger.info(
            "%-46s %6s %6s  %s",
            f["path"],
            f["lines"],
            f["by_name"] or "",
            f["replaced_by"] if f["replacement_exists"] else "-- NOT REPLACED",
        )
    logger.info("")
    logger.info(
        "deleting the replaced ones leaves %s lines and %s pgrep/pkill calls",
        s["lines_if_replaced_deleted"],
        s["by_name_if_replaced_deleted"],
    )
    if not s["by_name_if_replaced_deleted"]:
        logger.info(
            "every file that matches a process by NAME has a Python "
            "replacement. Replacement is not retirement: a .sh is deleted "
            "only once its replacement has produced a run that agrees with it."
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--json", action="store_true", help="machine-readable")
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    s = survey()
    if args.json:
        logger.info("%s", json.dumps(s, indent=2))
    else:
        report(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
