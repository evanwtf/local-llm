"""Is anything we are measuring through out of date?

llama.cpp, Ollama, Codex and OpenCode all move several times a day. This
project records the version of every component on every row, so drift does not
corrupt old results -- but it does mean a batch started today can be measuring a
stack that upstream replaced last week, and nothing said so.

Two kinds of drift, both reported:

**Released tools** -- Ollama, Codex, OpenCode, Claude Code -- compared against
their latest upstream release.

**Source builds** -- the llama.cpp worktrees and ds4 -- compared against the
remote-tracking refs already in the local clone. That comparison is offline and
only as fresh as the last `git fetch`, which is stated rather than hidden.

Nothing here blocks a run. A version check that fails a benchmark because
GitHub is slow is worse than no version check. Every lookup is time-bounded and
every failure degrades to "unknown", which is printed -- an unparseable version
must never read as agreement, because then the operator stops looking.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

# The first run of digits-and-dots in the string. Tags arrive as "v0.33.2",
# "rust-v0.150.1", "codex-cli 0.148.0" and "2.1.251 (Claude Code)"; Ollama
# prints its version inside a warning sentence.
_NUMBERS = re.compile(r"(\d+(?:\.\d+)+)")

# name -> (command to print the installed version, GitHub repo for releases)
TOOLS: dict[str, tuple[list[str], str]] = {
    "ollama": (["ollama", "--version"], "ollama/ollama"),
    "codex": (["codex", "--version"], "openai/codex"),
    "opencode": (["opencode", "--version"], "sst/opencode"),
    "claude": (["claude", "--version"], ""),  # npm, not a GitHub release
}

CACHE = pathlib.Path.home() / ".cache" / "local-llm" / "upstream-versions.json"
CACHE_TTL_SECONDS = 3600


def parse(text: str | None) -> tuple[int, ...] | None:
    """Pull a version tuple out of whatever a tool or a tag says."""
    if not text:
        return None
    found = _NUMBERS.search(text)
    if not found:
        return None
    return tuple(int(part) for part in found.group(1).split("."))


def compare(installed: str | None, latest: str | None) -> str:
    """One of current / behind / ahead / unknown.

    `unknown` whenever either side cannot be parsed. Never `current` -- a
    version check that cannot read a version has not agreed with anything.
    """
    a, b = parse(installed), parse(latest)
    if a is None or b is None:
        return "unknown"
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    if a == b:
        return "current"
    return "behind" if a < b else "ahead"


def _run(argv: list[str], timeout: int = 15) -> str | None:
    try:
        got = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (got.stdout or got.stderr or "").strip()
    if not out:
        return None
    # The first line that actually carries a version, not simply the first
    # line. `ollama --version` leads with "could not connect to a running
    # Ollama instance" when the daemon is down and prints the version below it,
    # so taking line one reported the version as unknown on a healthy install.
    for line in out.splitlines():
        if _NUMBERS.search(line):
            return line.strip()
    return out.splitlines()[0]


def installed_versions() -> dict[str, str | None]:
    return {name: _run(argv) for name, (argv, _repo) in TOOLS.items()}


def latest_versions(offline: bool = False) -> dict[str, str | None]:
    """Latest upstream release per tool, cached for an hour.

    Cached because this runs before every batch and the answer does not change
    minute to minute. `offline` skips the network entirely and uses whatever the
    cache holds.
    """
    cached: dict[str, Any] = {}
    if CACHE.is_file():
        try:
            cached = json.loads(CACHE.read_text())
        except (OSError, json.JSONDecodeError):
            cached = {}
    fresh = time.time() - cached.get("fetched_at", 0) < CACHE_TTL_SECONDS
    if offline or fresh:
        return cached.get("versions", {})

    versions: dict[str, str | None] = {}
    for name, (_argv, repo) in TOOLS.items():
        if not repo:
            versions[name] = (
                _run(
                    ["npm", "view", "@anthropic-ai/claude-code", "version"], timeout=20
                )
                if name == "claude"
                else None
            )
            continue
        versions[name] = _run(
            [
                "gh",
                "release",
                "view",
                "--repo",
                repo,
                "--json",
                "tagName",
                "-q",
                ".tagName",
            ],
            timeout=20,
        )
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"fetched_at": time.time(), "versions": versions}))
    except OSError as exc:
        logger.debug("could not cache upstream versions: %s", exc)
    return versions


# Branch names that mean "I am following mainline". Anything else is a
# deliberate parking spot -- a PR branch, a bisect, a patched build -- and
# being behind master is its normal state, not a problem.
MAINLINE = {"main", "master", "HEAD"}


def describe_drift(
    branch: str | None, behind: int | None, tracking: str | None
) -> dict[str, Any]:
    """Is this checkout actually stale, or just parked on a branch?

    A worktree on a PR branch diverges from master by design.
    `~/git/llama.cpp-glm52pr` sits on `glm53-pr27752` because PR #27752 is not
    merged, and reporting it "9 commits behind origin/master" is noise --
    pulling would destroy the build every glm53 row depends on.

    Judge against the branch's own upstream when it has one; otherwise only
    call it stale if it claims to be following mainline.
    """
    if behind is None:
        return {"stale": False, "note": "no remote to compare"}
    if tracking:
        return {
            "stale": behind > 0,
            "note": f"{behind} behind {tracking}" if behind else "current",
        }
    if branch and branch not in MAINLINE:
        return {
            "stale": False,
            "note": f"on PR branch {branch!r}; {behind} behind master "
            f"is expected divergence, not staleness",
        }
    return {
        "stale": behind > 0,
        "note": f"{behind} behind mainline" if behind else "current",
    }


# Notification reasons that mean somebody is talking to you, most direct first.
# `ci_activity` is deliberately absent: 41 of 41 notifications on this account
# were CI noise from unrelated repos, and a check that reports those is one
# nobody reads.
NOTIFY_REASONS = (
    "mention",
    "review_requested",
    "assign",
    "author",
    "comment",
    "subscribed",
)


def interesting_notifications(items, repos, reasons=NOTIFY_REASONS):
    """Notifications from repos this project depends on, ranked by directness.

    **Read items are kept.** The ds4 mention that mattered arrived by email and
    was already marked read through the API; filtering to unread would have
    hidden the one notification worth seeing.
    """
    out = []
    for item in items or []:
        try:
            repo = item["repository"]["full_name"]
            reason = item["reason"]
            subject = item["subject"]
        except (KeyError, TypeError):
            continue
        if repo not in repos or reason not in reasons:
            continue
        out.append(
            {
                "repo": repo,
                "reason": reason,
                "unread": bool(item.get("unread")),
                "title": (subject.get("title") or "")[:80],
                "type": subject.get("type") or "",
                "updated": (item.get("updated_at") or "")[:16],
            }
        )
    order = {r: i for i, r in enumerate(reasons)}
    out.sort(key=lambda n: (order.get(n["reason"], 99), n["updated"]))
    return out


def fetch_notifications(days: int = 14) -> list[dict[str, Any]]:
    """Recent notifications including read ones. [] on any failure."""
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))
    out = _run(
        ["gh", "api", f"notifications?all=true&since={since}", "--paginate"], timeout=25
    )
    if not out or not out.startswith("["):
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def new_remote_branches(
    repo: pathlib.Path, known: set[str] | None = None, days: int = 14
) -> list[str]:
    """Remote branches updated recently that the local checkout is not on.

    ds4 is the reference implementation for this hardware and antirez ships
    fast: GLM-5.3-Flash arrived on a preview branch while this project was
    benchmarking the model on an unsupported stack, and the branch had existed
    the whole time (#38). A new branch on that remote is a signal, not noise.

    Offline -- reads refs already fetched, so it is only as fresh as the last
    `git fetch`, which `git_drift` reports separately.
    """
    out = _run(
        [
            "git",
            "-C",
            str(repo),
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)%09%(committerdate:unix)",
            "refs/remotes",
        ]
    )
    if not out:
        return []
    import time as _time

    cutoff = _time.time() - days * 86400
    fresh = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        name, when = parts[0], int(parts[1])
        if when < cutoff or name.endswith("/HEAD"):
            continue
        short = name.split("/", 1)[-1]
        if short in {"main", "master"} or (known and short in known):
            continue
        fresh.append(short)
    return fresh


def git_drift(repo: pathlib.Path) -> dict[str, Any] | None:
    """How far behind its remote-tracking branch is this checkout?

    Offline: it reads refs already fetched, so the answer is only as fresh as
    the last fetch. `fetched_days_ago` says how stale that is, because "0
    commits behind" from a month-old fetch is not reassurance.
    """
    if not (repo / ".git").exists():
        return None
    head = _run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"])
    if head is None:
        return None
    behind = None
    for ref in ("origin/master", "origin/main", "upstream/master", "upstream/main"):
        got = _run(["git", "-C", str(repo), "rev-list", "--count", f"HEAD..{ref}"])
        if got and got.isdigit():
            behind = int(got)
            break
    dirty = _run(["git", "-C", str(repo), "status", "--porcelain"])
    fetch_head = repo / ".git" / "FETCH_HEAD"
    age = None
    if fetch_head.is_file():
        age = (time.time() - fetch_head.stat().st_mtime) / 86400
    branch = _run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
    # `_run` falls back to stderr, and `@{u}` on a branch with no upstream
    # prints "fatal: no upstream configured". That is an absence, not a name.
    tracking = _run(
        [
            "git",
            "-C",
            str(repo),
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{u}",
        ]
    )
    if tracking and (tracking.startswith("fatal:") or " " in tracking):
        tracking = None
    verdict = describe_drift(branch, behind, tracking)
    return {
        "head": head,
        "behind": behind,
        "branch": branch,
        "dirty": bool(dirty),
        "fetched_days_ago": age,
        "stale": verdict["stale"],
        "note": verdict["note"],
    }
