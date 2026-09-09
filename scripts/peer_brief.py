"""Generate the state half of a handoff as Markdown. #160

The handoff brief was written by hand -- roughly 1,400 words of repo state,
conventions, machine rules and gotchas, composed token by token by the most
expensive agent in the room. Nearly all of it is derivable: HEAD, dirty files,
the NEXT.md top 10, the open P0/P1 list, commits since a ref, the run-lock
holder, the resident model servers, and the `~/git/ds4-*` trees with the #70
ancestor check.

This script emits that derivable half. The judgment half -- what to do and why
-- stays hand-written, because that is the part worth an expensive agent's
tokens. The output is Markdown meant to be pasted into a handoff message, not
a report to be read on its own.

The machine state comes from `preflight` (the run lock and the server census)
and the tree drift from `staleness` (the #70 check). This script renders what
`peer_state` collects; it does not re-derive any of it.

Usage:

    uv run python scripts/peer_brief.py
    uv run python scripts/peer_brief.py --since 9ab7053
    uv run python scripts/peer_brief.py --repo /Users/x/git/local-llm
"""

from __future__ import annotations

import argparse
import datetime
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib import agent_identity, peer_state

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)
agent_identity.install(logger)


def _dirty_line(paths: list[str]) -> str:
    if not paths:
        return "clean"
    # Porcelain is `XY path`; the path is everything after the two status
    # columns, so a rename (`R  old -> new`) keeps its arrow.
    shown = ", ".join(p[3:] for p in paths[:8])
    more = f" (+{len(paths) - 8} more)" if len(paths) > 8 else ""
    return f"{shown}{more}"


def _lock_line(state: str, why: str) -> str:
    if state == "free":
        return "free"
    if state == "ours":
        return f"held by this process ({why})"
    if state == "stale":
        return f"stale ({why})"
    return f"{state} ({why})"


def _server_lines(servers: list) -> list[str]:
    if not servers:
        return ["none resident"]
    return [
        f"{p.short} (pid {p.pid}) {p.rss_gib:.1f} GiB after {p.age}"
        for p in sorted(servers, key=lambda p: p.rss_gib, reverse=True)
    ]


def _tree_lines() -> list[str]:
    trees = peer_state.ds4_trees()
    if not trees:
        return ["no ~/git/ds4-* trees"]
    lines = []
    for tree in trees:
        drift = peer_state.tree_drift(tree)
        if drift is None:
            lines.append(f"{tree.name} -- unreadable")
            continue
        note = drift.get("note") or "current"
        if drift.get("dirty"):
            note += " [UNCOMMITTED]"
        lines.append(f"{tree.name} @ {drift.get('head')} -- {note}")
    return lines


def _top10_lines() -> list[str]:
    items = peer_state.next_top10()
    if not items:
        return ["NEXT.md top 10 unreadable"]
    labels = _labels_by_issue()
    return [
        f"{i['rank']}. #{i['issue']} ({labels.get(i['issue'], '?')}) -- {i['title']}"
        for i in items
    ]


def _labels_by_issue() -> dict[int, str]:
    """{issue: comma-joined labels} from the open P0/P1 list."""
    out: dict[int, str] = {}
    for issue in peer_state.open_p0p1():
        names = [l.get("name", "") for l in issue.get("labels", [])]
        out[issue.get("number")] = ",".join(n for n in names if n)
    return out


def _p0p1_lines() -> list[str]:
    issues = peer_state.open_p0p1()
    if not issues:
        return ["gh issue list returned nothing (offline?)"]
    return [
        f"#{i.get('number')} [{','.join(l.get('name', '') for l in i.get('labels', []))}] -- {i.get('title')}"
        for i in sorted(issues, key=lambda i: i.get("number", 0))
    ]


def _invariant_check() -> str | None:
    """The NEXT.md invariant: P0 + P1 is exactly the top 10.

    Returns a warning line when they disagree, else None. The file is the
    source of truth; a mismatch means the file was edited without the labels
    being re-applied.
    """
    top = {i["issue"] for i in peer_state.next_top10()}
    p0p1 = {i.get("number") for i in peer_state.open_p0p1()}
    if not top or not p0p1:
        return None
    if top == p0p1:
        return None
    only_top = sorted(top - p0p1)
    only_p0p1 = sorted(p0p1 - top)
    return (
        "INVARIANT BROKEN: NEXT.md top 10 and the P0/P1 labels disagree. "
        f"in NEXT.md only: {only_top}; labeled P0/P1 only: {only_p0p1}"
    )


def brief(repo: pathlib.Path, since: str | None) -> str:
    """Render the state half of a handoff as Markdown."""
    head = peer_state.git_head(repo) or "unknown"
    dirty = peer_state.git_dirty(repo)
    state, why = peer_state.run_lock()
    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Handoff brief -- {repo.name} @ {head} ({now})",
        "",
        "## Repo state",
        f"- HEAD: `{head}`",
        f"- Dirty: {_dirty_line(dirty)}",
        "",
        "## NEXT.md top 10",
        *_top10_lines(),
        "",
        "## Open P0/P1",
        *_p0p1_lines(),
    ]
    invariant = _invariant_check()
    if invariant:
        lines += ["", f"- **{invariant}**"]
    if since:
        commits = peer_state.commits_since(repo, since)
        commit_lines = [f"- {c}" for c in commits] or ["none"]
        lines += ["", f"## Commits since {since}", *commit_lines]
    lines += [
        "",
        "## Machine",
        f"- Run lock: {_lock_line(state, why)}",
        "- Resident servers:",
        *(f"  - {s}" for s in _server_lines(peer_state.servers())),
        "",
        "## ds4-* trees (#70 check)",
        *(f"- {t}" for t in _tree_lines()),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        type=pathlib.Path,
        default=peer_state.REPO,
        help="the repo to brief (default: ~/git/local-llm)",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="include commits since this ref, e.g. 9ab7053",
    )
    args = parser.parse_args(argv)
    logs.configure(fmt="%(asctime)s %(agent)s %(name)s %(levelname)s %(message)s")
    logger.info("generating handoff brief for %s", args.repo)
    for line in brief(args.repo, args.since).splitlines():
        logger.info("%s", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
