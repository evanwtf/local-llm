#!/usr/bin/env python3
"""Refuse a commit while a lock-holding benchmark driver is live (#227).

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
#
# Every driver that runs inside a held machine lock. Only
# `stack_agent_ab.sh` was listed until 2026-09-08, so ELEVEN of the twelve
# were uncovered -- a commit during any of them moves HARNESS_HEAD between
# arms and splits `harness_dirty` across a comparison, which is the confound
# the guard exists to stop.
#
# The membership rule is mechanical and a test enforces it: a driver holds
# the lock if it passes `--acquire-lock` (it takes the lock) or `--no-lock`
# (something above it holds one). Either way its run.py calls belong to one
# experiment, and the head must not move between them.
_PATTERNS = (
    "[d]ecode_ab.sh",
    "[d]ecode_ab_engine.sh",
    "[d]ecode_ab_stack.sh",
    "[g]reedy_mtp_ab.sh",
    "[m]etal_knob_ab.sh",
    "[m]tp_treatment_gate.sh",
    "[r]estart_between_trials.sh",
    "[r]estart_between_trials_armB.sh",
    "[r]oute_agent_ab.sh",
    "[s]tack_agent_ab.sh",
    "[s]trip_toggle_ab.sh",
    "[t]argets_ab.sh",
)


def _is_invocation(command: str, script: str) -> bool:
    """True when `command` RUNS `script`, not merely mentions it.

    `pgrep -f` matches the whole command line, so any process that names a
    driver matches -- including a waiter shell built from the driver's own
    name:

        /bin/zsh -c ... until ! pgrep -f 'metal_knob_ab.sh'; do sleep 30; done

    Seven of those, up to six and a half hours old, were live on 2026-09-08.
    Each was itself stuck in the self-match trap AGENTS.md warns about, and
    together they made the guard refuse every commit while no benchmark was
    running at all. A guard that cannot be satisfied gets overridden, and then
    it is not a guard.

    A real invocation puts the script in argv[0] (`./metal_knob_ab.sh`) or
    argv[1] (`bash scripts/metal_knob_ab.sh`). A mention is buried deeper, in
    a quoted string. Position is what separates them.
    """
    tokens = command.split()
    return any(token.split("/")[-1] == script for token in tokens[:2])


def _command_lines(pids: list[str]) -> list[str]:
    """Full argv for each pid, via ps. [] on any error.

    Not from pgrep's own listing: the two platforms disagree about how to ask
    for it. `-a` is GNU-only and BSD prints bare pids; `-l` on GNU prints the
    process NAME from /proc, truncated to 15 characters, so
    `stack_agent_ab.sh` arrives as `stack_agent_ab.` and matches nothing. CI
    went red on exactly that. `ps -o command=` means the same thing on both.
    """
    if not pids:
        return []
    proc = subprocess.run(
        # -ww: unlimited width. GNU ps truncates to the terminal width by
        # default, and a driver launched by absolute path is long enough to
        # lose its own arguments.
        ["ps", "-ww", "-o", "command=", "-p", ",".join(pids)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _live_run() -> str | None:
    """The driver that is running, or None.

    Returns None on any error and when pgrep finds no match, so an ambiguous
    check fails open rather than blocking a commit on doubt.
    """
    pgrep = shutil.which("pgrep")
    if pgrep is None:
        logger.warning("pgrep not found; cannot check for a live A/B; commit allowed")
        return None
    for pattern in _PATTERNS:
        script = pattern.replace("[", "").replace("]", "")
        proc = subprocess.run(
            [pgrep, "-f", pattern], capture_output=True, text=True, check=False
        )
        # rc 0 means at least one match. Anything else -- no match (1) or an
        # error (2+) -- means there is no live run we can prove.
        if proc.returncode != 0:
            continue
        for command in _command_lines(proc.stdout.split()):
            if _is_invocation(command, script):
                logger.debug("live run matched: %s", command[:200])
                return script
    return None


def main() -> int:
    """Exit 1 to refuse a commit, 0 to allow it."""
    if os.environ.get("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN") == "1":
        logger.info("commit allowed: LOCAL_LLM_ALLOW_COMMIT_DURING_RUN=1")
        return 0
    driver = _live_run()
    if driver is None:
        return 0
    logger.error(
        "%s is running; committing would move HARNESS_HEAD and kill every "
        "remaining sweep, or split harness_dirty across the arms of a "
        "comparison. Wait for the run to finish, or set "
        "LOCAL_LLM_ALLOW_COMMIT_DURING_RUN=1 to commit anyway.",
        driver,
    )
    return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stdout,  # house rule: one stream, and the handler goes to stdout
    )
    raise SystemExit(main())
