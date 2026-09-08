#!/usr/bin/env python3
"""Refuse a commit while a stack_agent A/B is live (#227).

A commit during a pinned run moves HARNESS_HEAD, and every remaining sweep
refuses in about a second from its own fresh process. A logs-only commit killed
7 of 8 sweeps on 2026-09-08, and the wrapper printed "all 8 sweeps complete"
afterwards. pre-commit is the only place a commit can be stopped before it
moves HEAD, so this hook is the guard for that failure mode.

The hook refuses only on a confident match. No match and an error both let the
commit through (fail open): a guard that blocks every commit is worse than the
bug it guards, and an ambiguous pgrep result is not evidence of a live run.
Set LOCAL_LLM_ALLOW_COMMIT_DURING_RUN=1 to override for a deliberate commit.

Only the standard library is used, so the hook needs nothing from the venv.
`language: python` still builds a hermetic environment, so `uv run pre-commit
run` and a bare `git commit` invoke the same interpreter -- the guard cannot be
present under one and absent under the other, which is the whole point.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

# pgrep -f matches whole command lines. The bracket around the first character
# keeps this process from matching itself: the regex ``[s]tack`` matches the
# text "stack", never the literal string "[s]tack". Without it, whatever shell
# or parent process quoted the pattern would be the very thing the regex sees.
_PATTERN = "[s]tack_agent_ab.sh"


def _live_run() -> bool:
    """True if a stack_agent A/B is running, else False.

    Returns False on any error and when pgrep finds no match, so an ambiguous
    check fails open rather than blocking a commit on doubt.
    """
    pgrep = shutil.which("pgrep")
    if pgrep is None:
        logger.warning("pgrep not found; cannot check for a live A/B; commit allowed")
        return False
    proc = subprocess.run(
        [pgrep, "-f", _PATTERN], capture_output=True, text=True, check=False
    )
    # rc 0 means at least one match. Anything else -- no match (1) or an error
    # (2+) -- means there is no live run we can prove.
    return proc.returncode == 0


def main() -> int:
    """Exit 1 to refuse a commit, 0 to allow it."""
    if os.environ.get("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN") == "1":
        logger.info("commit allowed: LOCAL_LLM_ALLOW_COMMIT_DURING_RUN=1")
        return 0
    if not _live_run():
        return 0
    logger.error(
        "a stack_agent A/B is running; committing would move HARNESS_HEAD and kill "
        "every remaining sweep. Wait for the run to finish, or set "
        "LOCAL_LLM_ALLOW_COMMIT_DURING_RUN=1 to commit anyway."
    )
    return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stdout,  # house rule: one stream, and the handler goes to stdout
    )
    raise SystemExit(main())
