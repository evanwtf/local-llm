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
    "claude": (["claude", "--version"], ""),   # npm, not a GitHub release
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
        got = subprocess.run(argv, capture_output=True, text=True, check=False,
                             timeout=timeout, stdin=subprocess.DEVNULL)
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
            versions[name] = _run(["npm", "view", "@anthropic-ai/claude-code",
                                   "version"], timeout=20) if name == "claude" else None
            continue
        versions[name] = _run(["gh", "release", "view", "--repo", repo,
                               "--json", "tagName", "-q", ".tagName"], timeout=20)
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"fetched_at": time.time(),
                                     "versions": versions}))
    except OSError as exc:
        logger.debug("could not cache upstream versions: %s", exc)
    return versions


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
    return {"head": head, "behind": behind,
            "dirty": bool(dirty), "fetched_days_ago": age}
