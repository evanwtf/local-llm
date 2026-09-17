"""Shared state collection for the peer tooling. #160

`peer_brief.py` and `peer_status.py` both read the same machine and repo
state: the run lock, the resident model servers, the `~/git/ds4-*` trees and
their drift, the repo HEAD and dirty paths, and the P0/P1 queue. That state
is the shared bus between agents -- the filesystem and git, not a server -- so
it is collected once here and rendered differently by the two scripts.

The machine half comes from `preflight`, which owns the run lock and the
server census; the tree half comes from `staleness`, which owns the #70
ancestor check. This module does not re-derive either. It only enumerates the
trees and the queue, which neither owns. The queue is read live from the
issue labels since #463 retired the committed NEXT.md.
"""

from __future__ import annotations

import os
import pathlib
import platform
import subprocess
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[2] / "benchmarks" / "agent")
)
import preflight
import staleness

HOME = pathlib.Path.home()
GIT_DIR = HOME / "git"
# The repo this module lives in, not `~/git/local-llm`. A hardcoded path
# resolved on the laptop and nowhere else (AGENTS.md, `d9a223e`).
REPO = pathlib.Path(__file__).resolve().parents[2]
ORG = "evanwtf/local-llm"


def _run(argv: list[str]) -> str | None:
    """Run a command, return trimmed stdout, or None on any failure.

    A failure is an absence, not an error: an offline `gh` or a missing tree
    must read as "unknown", never as a crash.
    """
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None


def ds4_trees() -> list[pathlib.Path]:
    """Every `~/git/ds4-*` checkout, sorted. [] when none."""
    if not GIT_DIR.is_dir():
        return []
    return sorted(p for p in GIT_DIR.glob("ds4-*") if (p / ".git").exists())


def tree_drift(tree: pathlib.Path) -> dict | None:
    """The #70 ancestor check for one tree, via staleness."""
    return staleness.git_drift(tree)


def run_lock() -> tuple[str, str]:
    """(state, why) for the run lock, via preflight. State is one of
    free/ours/held/stale/foreign/corrupt."""
    lock = preflight.read_lock(preflight.LOCK_PATH)
    return preflight.lock_state(lock, platform.node(), os.getpid())


def servers() -> list[preflight.Proc]:
    """Resident model servers with age, via preflight's own census."""
    text = preflight._capture(["ps", "-eo", "pid,rss,etime,command"])
    return preflight.parse_ps(text)


def git_head(repo: pathlib.Path) -> str | None:
    """Short HEAD rev, or None when the repo is missing."""
    return _run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"])


def git_dirty(repo: pathlib.Path) -> list[str]:
    """Porcelain dirty paths, or [] when clean."""
    out = _run(["git", "-C", str(repo), "status", "--porcelain"])
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def commits_since(repo: pathlib.Path, ref: str) -> list[str]:
    """One-line commit subjects since `ref`, newest first. [] on failure."""
    out = _run(["git", "-C", str(repo), "log", "--oneline", f"{ref}..HEAD"])
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def host_platform_label() -> str:
    """The platform label whose queue this host works: macOS or Nvidia."""
    return "platform:macOS" if platform.system() == "Darwin" else "platform:Nvidia"


def next_top10(label: str | None = None) -> list[dict]:
    """This host's queue as [{rank, issue, priority, title}], in rank order.

    Read live from the labels, the same rule `scripts/make_next.py` prints:
    P0 before P1, then by issue number, for one platform. The committed
    NEXT.md this used to parse went stale between regenerations and covered
    only macOS (#463).
    """
    label = label or host_platform_label()
    rows = []
    for issue in open_p0p1():
        names = {lab["name"] for lab in issue.get("labels", [])}
        prio = sorted(names & {"P0", "P1"})
        if label not in names or len(prio) != 1:
            continue
        rows.append((prio[0], issue["number"], issue["title"]))
    rows.sort()
    return [
        {"rank": n, "priority": p, "issue": num, "title": title}
        for n, (p, num, title) in enumerate(rows, 1)
    ]


def open_p0p1() -> list[dict]:
    """Open P0 and P1 issues as [{number, title, labels}]. [] on failure.

    `--limit 200` is deliberate: `gh issue list` defaults to 30 and silently
    undercounts, and a P0 that never appears is a P0 nobody acts on.

    `--search label:P0,P1` rather than two `--label` flags: gh ANDs repeated
    `--label` filters, so the old form asked for issues carrying both labels
    and matched nothing (#463).
    """
    out = _run(
        [
            "gh",
            "issue",
            "list",
            "-R",
            ORG,
            "--state",
            "open",
            "--search",
            "label:P0,P1",
            "--limit",
            "200",
            "--json",
            "number,title,labels",
        ]
    )
    if not out:
        return []
    try:
        import json

        return json.loads(out)
    except ValueError:
        return []


def peer_branches(repo: pathlib.Path = REPO) -> list[dict]:
    """Local `peer/*` branches as [{name, head}], sorted by name. [] on failure."""
    out = _run(
        [
            "git",
            "-C",
            str(repo),
            "for-each-ref",
            "--format=%(refname:short)%09%(objectname:short)",
            "refs/heads/peer/*",
        ]
    )
    if not out:
        return []
    branches = []
    for line in out.splitlines():
        name, _, head = line.partition("\t")
        if name and head:
            branches.append({"name": name, "head": head})
    return branches


def open_prs() -> list[dict]:
    """Open PRs as [{number, title, headRefName}]. [] on failure."""
    out = _run(
        [
            "gh",
            "pr",
            "list",
            "-R",
            ORG,
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,title,headRefName",
        ]
    )
    if not out:
        return []
    try:
        import json

        return json.loads(out)
    except ValueError:
        return []


def comment_counts(issues: list[int]) -> dict[int, int]:
    """{issue: comment count} for the given issues. Missing on failure."""
    counts: dict[int, int] = {}
    for issue in issues:
        out = _run(["gh", "issue", "view", str(issue), "-R", ORG, "--json", "comments"])
        if not out:
            continue
        try:
            import json

            counts[issue] = len(json.loads(out).get("comments", []))
        except ValueError:
            continue
    return counts
