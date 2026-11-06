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

import provenance
import results

logger = logging.getLogger(__name__)

FIX = "7356460"  # opencode_argv gained --dir

#: The era rule a stamp was made under (#393). Rule 1: a row is "after" when
#: the harness that wrote it descends from FIX. A row stores the rule and the
#: fix commit with its era, never a bare before/after bit, so a later rule
#: reads an old row under the rule that produced it.
RULE = 1

#: The env key run.py stamps at write time, and scripts/stamp_dir_era.py wrote
#: once onto the #355 orphans. It replaced the REBASED_AFTER_FIX allowlist.
STAMP_KEY = "dir_era"

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


def stamp(repo: pathlib.Path) -> dict | None:
    """The era provenance for a row the harness at `repo` writes now (#393).

    run.py puts it in every row's env, so the era never depends on a later
    `git log` that may no longer reach the row's harness_head (#355). It
    reads HEAD's ancestry, not the working tree: a dirty tree is recorded by
    `harness_dirty`, beside it. None when git cannot answer (no repository,
    or a shallow clone without FIX); the row then stays unstamped, and era()
    classifies it from git as before.
    """
    r = subprocess.run(
        ["git", "merge-base", "--is-ancestor", FIX, "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo,
        check=False,
    )
    if r.returncode not in (0, 1):
        return None
    return {
        "rule": RULE,
        "fix": FIX,
        "era": "after" if r.returncode == 0 else "before",
        "basis": "ancestry at write time",
    }


def stamped_era(row: dict) -> str | None:
    """The era a row's own stamp records under RULE, or None.

    A stamp under another rule or fix is not read as this one: the row stays
    for git to classify, rather than be read under a rule that did not make
    it.
    """
    got = (row.get("env") or {}).get(STAMP_KEY)
    if not isinstance(got, dict):
        return None
    if got.get("rule") != RULE or got.get("fix") != FIX:
        return None
    era = got.get("era")
    return era if era in ("before", "after") else None


def era(row: dict, after: set[str]) -> str:
    """Which side of the fix a row was recorded on.

    The row's own stamp decides when it has one (#393). Otherwise its
    harness_head is looked up in `after`, the commits from FIX to HEAD. A row
    predating the provenance field has no harness_head at all, which is
    itself "before".
    """
    if (stamped := stamped_era(row)) is not None:
        return stamped
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
        # A dry run is a setup check with no verdict, and a pass is
        # results.verdict()'s, guards included: a row that edited the tests
        # passed nothing (code review, 2026-10-05).
        if r.get("dry_run"):
            continue
        cell = out[(r["backend"], task_class(r), era(r, after))]
        cell[0] += results.verdict(r)
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
    provenance.configure()
    p = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else results.default_path()
    rows = [json.loads(line) for line in p.read_text().splitlines()]
    if ARCHIVE.exists():
        rows += [json.loads(line) for line in ARCHIVE.read_text().splitlines()]
    else:
        logger.warning("%s is missing; the before column will be empty", ARCHIVE)
    for line in report(tally(rows, fixed_commits(p.resolve().parent))):
        logger.info("%s", line)


if __name__ == "__main__":
    main()
