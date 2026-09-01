"""#67: OpenCode's record before and after --dir, split on the fix commit.

Every OpenCode trial recorded before 7356460 measured a client that was never
told where to work, so it measures this harness rather than OpenCode. This
splits the record on that commit so the two are never averaged together.

Read it with the exclusions in mind: a pre-fix trial that crashed the client is
auto-excluded and never reaches the "before" column, so that column flatters
the old numbers rather than exaggerating them.
"""

from __future__ import annotations

import collections
import json
import logging
import pathlib
import subprocess
import sys

import results

logger = logging.getLogger(__name__)

FIX = "7356460"  # opencode_argv gained --dir
# The pre---dir rows were moved out of results.jsonl by
# scripts/archive_pre_dir_rows.py, so this reads both files. Without the
# archive the "before" column silently empties and the fix looks unmeasured.
ARCHIVE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs/archive/results-opencode-pre-dir.jsonl"
)


def fixed_commits(repo: pathlib.Path) -> set[str]:
    """Commits from the --dir fix to HEAD.

    Anchor on the results file, not __file__: this script has lived outside the
    repo, where `git log` succeeds with empty output and every row silently
    classifies as "before". An empty set is that bug, so refuse rather than
    report a table of zeroes.
    """
    r = subprocess.run(
        ["git", "log", "--format=%h", f"{FIX}~1..HEAD"],
        capture_output=True,
        text=True,
        cwd=repo,
        check=False,
    )
    heads = {c[:7] for c in r.stdout.split()}
    if not heads:
        raise SystemExit(f"no commits from {FIX} in {repo}: {r.stderr.strip()}")
    return heads


def era(row: dict, after: set[str]) -> str:
    """Which side of the fix a row was recorded on.

    Provenance lives under `env`. A row predating that field has no
    harness_head at all, which is itself "before".
    """
    return (
        "after"
        if str(row.get("env", {}).get("harness_head", ""))[:7] in after
        else "before"
    )


def task_class(row: dict) -> str:
    """A script task is its own class: no repo, so no excision or answer leak."""
    return "script" if str(row.get("task", "")).startswith("script-") else "excision"


def tally(rows: list[dict], after: set[str]) -> dict[tuple[str, str, str], list[int]]:
    out: dict[tuple[str, str, str], list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        # results.is_excluded, never a hand-rolled r.get("excluded"). The
        # stored field misses agent_error rows -- a client that died at
        # config never made a model attempt -- and it misses the legacy
        # confound/contaminated keys. Hand-rolling this check already
        # miscounted fourteen rows once; see RESULTS.md.
        if r.get("client") != "opencode" or results.is_excluded(r):
            continue
        cell = out[(r.get("backend"), task_class(r), era(r, after))]
        cell[0] += bool(r.get("passed"))
        cell[1] += 1
    return dict(out)


def report(counts: dict[tuple[str, str, str], list[int]]) -> list[str]:
    lines = [f"{'backend':<16} {'class':<9} {'before':>9} {'after':>9}"]
    for b, k in sorted({(b, k) for b, k, _ in counts}):
        cells = []
        for when in ("before", "after"):
            if (b, k, when) not in counts:
                cells.append("-")
            else:
                w, n = counts[(b, k, when)]
                cells.append(f"{w}/{n}")
        lines.append(f"{b:<16} {k:<9} {cells[0]:>9} {cells[1]:>9}")
    return lines


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "results.jsonl")
    rows = [json.loads(line) for line in p.read_text().splitlines()]
    if ARCHIVE.exists():
        rows += [json.loads(line) for line in ARCHIVE.read_text().splitlines()]
    else:
        logger.warning("%s is missing; the before column will be empty", ARCHIVE)
    for line in report(tally(rows, fixed_commits(p.resolve().parent))):
        logger.info("%s", line)


if __name__ == "__main__":
    main()
