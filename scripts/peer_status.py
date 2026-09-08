"""One deterministic status line for the peer work. #160

Watching the peer meant hand-rolling a shell loop over branches, PRs, comment
counts and process names. Fine once; not a thing to re-invent per session.
This script computes the signals worth watching once -- peer branches and their
heads, open PRs, comment counts on the issues in play, the `~/git/ds4-*` tree
revs, the run lock, and any live model server -- and prints one line when
nothing changed, an itemized block when something did.

"Changed" is a diff against the previous run, stored in `.claude/peer/status.json`
-- the append-only shared channel the issue names, used here as a last-seen
baseline. A new comment, a new PR, a branch that moved, a server that came up
or went down, a lock acquired or released, a tree that went dirty: each is a
line in the block. When none of them moved, the one line is all you get.

Usage:

    uv run python scripts/peer_status.py
    uv run python scripts/peer_status.py --repo /Users/x/git/local-llm
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib import agent_identity, peer_state

logger = logging.getLogger(__name__)
agent_identity.install(logger)

STATE_DIR = pathlib.Path(__file__).resolve().parents[1] / ".claude" / "peer"
STATE_FILE = STATE_DIR / "status.json"


def _snapshot(repo: pathlib.Path) -> dict:
    """The signals worth watching, as a comparable dict."""
    branches = peer_state.peer_branches(repo)
    prs = peer_state.open_prs()
    top = peer_state.next_top10()
    issues = [i["issue"] for i in top]
    comments = peer_state.comment_counts(issues)
    trees = {
        t.name: (peer_state.tree_drift(t) or {}).get("head")
        for t in peer_state.ds4_trees()
    }
    state, _ = peer_state.run_lock()
    servers = [
        {"short": p.short, "pid": p.pid, "gib": round(p.rss_gib, 1), "age": p.age}
        for p in peer_state.servers()
    ]
    return {
        "branches": {b["name"]: b["head"] for b in branches},
        "prs": {str(p.get("number")): p.get("title") for p in prs},
        # str keys, because the snapshot round-trips through JSON and JSON
        # object keys are always strings. comment_counts() returns ints, so
        # without this every run compares this run's int keys against the
        # previous run's str keys -- see _diff.
        "comments": {str(k): v for k, v in comments.items()},
        "trees": trees,
        "lock": state,
        "servers": servers,
    }


def _load_previous() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(current: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(current, indent=2, sort_keys=True))
    except OSError:
        logger.warning("could not write %s", STATE_FILE)


def _str_keys(d: dict) -> dict:
    """The same mapping with str keys. See _diff."""
    return {str(k): v for k, v in d.items()}


def _diff(prev: dict, cur: dict) -> list[str]:
    """Human lines for what changed between two snapshots."""
    out: list[str] = []
    prev_b = prev.get("branches", {})
    cur_b = cur.get("branches", {})
    for name in sorted(set(prev_b) | set(cur_b)):
        if prev_b.get(name) != cur_b.get(name):
            out.append(
                f"branch {name}: {prev_b.get(name) or 'new'} -> {cur_b.get(name) or 'gone'}"
            )
    # Same treatment, and here the failure was quieter: mismatched key types
    # made every PR read as both opened and closed on every run, rather than
    # raising. A crash is the better of the two.
    prev_p = _str_keys(prev.get("prs", {}))
    cur_p = _str_keys(cur.get("prs", {}))
    for num in sorted(set(prev_p) | set(cur_p), key=lambda n: int(n)):
        if prev_p.get(num) != cur_p.get(num):
            out.append(
                f"PR #{num}: {prev_p.get(num) or 'opened'} -> {cur_p.get(num) or 'closed'}"
            )
    # Both sides keyed the same way before they are compared. A snapshot
    # written before the str-keying above still has int keys on disk, and
    # mixing them does not merely mis-compare: sorted() on {3, "3"} raises
    # TypeError and takes the whole status report down. That is what it did.
    prev_c = _str_keys(prev.get("comments", {}))
    cur_c = _str_keys(cur.get("comments", {}))
    for issue in sorted(set(prev_c) | set(cur_c), key=int):
        if prev_c.get(issue, 0) != cur_c.get(issue, 0):
            out.append(
                f"issue #{issue}: {prev_c.get(issue, 0)} -> {cur_c.get(issue, 0)} comments"
            )
    prev_t = prev.get("trees", {})
    cur_t = cur.get("trees", {})
    for name in sorted(set(prev_t) | set(cur_t)):
        if prev_t.get(name) != cur_t.get(name):
            out.append(
                f"tree {name}: {prev_t.get(name) or 'new'} -> {cur_t.get(name) or 'gone'}"
            )
    if prev.get("lock") != cur.get("lock"):
        out.append(
            f"run lock: {prev.get('lock') or 'free'} -> {cur.get('lock') or 'free'}"
        )
    prev_s = {(s["short"], s["pid"]) for s in prev.get("servers", [])}
    cur_s = {(s["short"], s["pid"]) for s in cur.get("servers", [])}
    for short, pid in sorted(cur_s - prev_s):
        out.append(f"server up: {short} (pid {pid})")
    for short, pid in sorted(prev_s - cur_s):
        out.append(f"server down: {short} (pid {pid})")
    return out


def _summary(cur: dict) -> str:
    """The one-line summary, always printed."""
    n_branches = len(cur.get("branches", {}))
    n_prs = len(cur.get("prs", {}))
    n_comments = sum(cur.get("comments", {}).values())
    n_servers = len(cur.get("servers", []))
    gib = sum(s["gib"] for s in cur.get("servers", []))
    lock = cur.get("lock") or "free"
    return (
        f"{n_branches} peer branch(es), {n_prs} PR(s) open, "
        f"{n_comments} comment(s) on the top 10, {n_servers} server(s) up "
        f"({gib:.1f} GiB), lock {lock}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        type=pathlib.Path,
        default=peer_state.REPO,
        help="the repo to watch (default: ~/git/local-llm)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(agent)s %(name)s %(levelname)s %(message)s",
    )
    current = _snapshot(args.repo)
    changed = _diff(_load_previous(), current)
    logger.info("peer status: %s", _summary(current))
    for line in changed:
        logger.info("  changed: %s", line)
    _save(current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
