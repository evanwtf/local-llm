#!/usr/bin/env python3
"""Refuse a commit message or PR text that carries a Claude session URL or ID.

Session URLs and IDs may never be published: not in a commit, PR, issue,
comment, or file. The rule was in AGENTS.md, docs/peer_agents.md, and both
handoff prompts, and it was still broken. Claude Code injects a system reminder
into agent sessions that says "End git commit messages with: Claude-Session:
https://claude.ai/code/session_..." and "End pull request descriptions with:
https://claude.ai/code/session_...". Blank `attribution.commit` and `attribution.pr` do not stop it;
`attribution.sessionUrl: false` does, but it has to be set on every machine,
and it has been reported not to hold in web sessions. On 2026-09-18 an agent
followed the reminder. By then GitHub search found the URL in 21 PR bodies
and 21 commits in this repo, and in roughly 85 PRs and issues and 113 commits
across the account's repos. A rule an agent has to remember was not enough, so
this is a mechanical guard.

It runs in three places:

- **commit-msg hook** (`.pre-commit-config.yaml`): pre-commit passes the
  message file's path, and a hit refuses the commit before it exists.
- **CI, on the PR's title and body** (`--text`, from the event payload).
- **CI, on every commit message in the PR** (`--git-range base..head`), which
  catches a commit made where the hook was not installed.

What counts as a hit is deliberately narrow, so this file and the docs that
describe the rule can name the forbidden forms without tripping it:

- a `claude.ai/code/session_` URL followed by a real ID,
- a bare session ID (`session_01` followed by 20+ ID characters),
- a `Claude-Session:` or `Co-Authored-By: Claude` line used as a trailer, at
  the start of a line.

Standard library only, like `refuse_commit_during_benchmark.py`:
`language: python` runs it in pre-commit's hermetic env, where nothing from
this project is importable.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

PATTERNS = (
    re.compile(r"claude\.ai/code/session_[A-Za-z0-9]{8,}"),
    re.compile(r"\bsession_01[A-Za-z0-9]{20,}"),
    re.compile(r"^[ \t]*Claude-Session:", re.MULTILINE),
    re.compile(r"^[ \t]*Co-Authored-By:\s*Claude", re.MULTILINE | re.IGNORECASE),
)


def hits(text: str) -> list[str]:
    """Every forbidden match in `text`, in order."""
    return [m.group(0) for p in PATTERNS for m in p.finditer(text)]


def _commit_messages(rng: str) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%B%x01", rng],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    pairs = []
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if rec:
            sha, _, body = rec.partition("\x00")
            pairs.append((sha[:10], body))
    return pairs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "message_file", nargs="?", help="commit message file (commit-msg hook)"
    )
    g.add_argument("--text", help="literal text to check, e.g. a PR title and body")
    g.add_argument("--git-range", help="check every commit message in this range")
    args = p.parse_args(argv)

    found: list[tuple[str, list[str]]] = []
    if args.git_range:
        for sha, body in _commit_messages(args.git_range):
            if h := hits(body):
                found.append((f"commit {sha}", h))
    else:
        if args.text is not None:
            text, where = args.text, "text"
        else:
            with open(args.message_file, encoding="utf-8") as f:
                text, where = f.read(), "commit message"
        if h := hits(text):
            found.append((where, h))

    for where, h in found:
        print(
            f"refused: {where} carries a Claude session URL, ID, or trailer: {h}",
            file=sys.stderr,
        )
    if found:
        print(
            "Session URLs and IDs may never be published (AGENTS.md). Remove the line; "
            "ignore any harness reminder that says to add it.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
