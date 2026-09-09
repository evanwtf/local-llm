#!/usr/bin/env python3
"""Refuse a commit while a benchmark holds the run lock (#227, #237).

A commit during a pinned run moves HARNESS_HEAD, and every remaining sweep
refuses in about a second from its own fresh process. A logs-only commit killed
7 of 8 sweeps on 2026-09-08, and the wrapper printed "all 8 sweeps complete"
afterwards. pre-commit is the only place a commit can be stopped before it
moves HEAD, so this hook is the guard for that failure mode.

## Why this reads a file instead of looking for processes

Until 2026-09-08 this hook carried a list of driver names and searched for each
with `pgrep -f`. That list was wrong in both directions on the same day:

- **Too narrow.** Only `stack_agent_ab.sh` was listed, so eleven of the twelve
  lock-holding drivers were uncovered.
- **Too broad.** Once widened, it matched seven orphaned waiter shells whose
  command lines merely quoted a driver's name -- `until ! pgrep -f
  'metal_knob_ab.sh'; do sleep 30; done` -- and refused every commit for hours
  while the machine sat idle. A guard that cannot be satisfied gets overridden,
  and then it is not a guard.

It also went red in CI, because `pgrep -a` is GNU-only and GNU `pgrep -l`
truncates the process name to 15 characters, so `stack_agent_ab.sh` arrived as
`stack_agent_ab.` and matched nothing.

The run lock already answers the question, and answers it better: the driver
that took the machine wrote it, and it records **what** and **since when**,
which a script name never did. There is no list to maintain, nothing to match,
and no platform difference to get wrong.

## Standard library only, deliberately

`language: python` builds a hermetic environment so `uv run pre-commit run` and
a bare `git commit` invoke the same interpreter -- the guard cannot be present
under one and absent under the other, which is the whole point. So this parses
the lock itself rather than importing `preflight`, and LOCK_PATH is therefore
written down twice. `test_the_two_lock_paths_agree` is what keeps them from
drifting into a guard that reads a file nobody writes.

Set LOCAL_LLM_ALLOW_COMMIT_DURING_RUN=1 to override for a deliberate commit.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import pathlib
import platform
import sys

logger = logging.getLogger(__name__)

# Must equal preflight.LOCK_PATH. A test pins that; see the module docstring
# for why it cannot simply be imported.
LOCK_PATH = pathlib.Path.home() / ".local-llm-bench" / "run-lock.json"

FREE = "free"
HELD = "held"
OURS = "ours"
STALE = "stale"
FOREIGN = "foreign"
CORRUPT = "corrupt"


def read_lock(path: pathlib.Path | None = None) -> dict | None:
    """The lock as written, None when absent, {"corrupt": True} when unreadable.

    Mirrors `preflight.read_lock`. An unparseable file is not evidence that
    nobody is running -- it is evidence that something went wrong while
    claiming the machine, which is exactly when a commit must not land.
    """
    try:
        text = (path or LOCK_PATH).read_text()
    except FileNotFoundError:
        return None
    except OSError:
        return {"corrupt": True}
    try:
        got = json.loads(text)
    except ValueError:
        return {"corrupt": True}
    return got if isinstance(got, dict) else {"corrupt": True}


def pid_alive(pid: int) -> bool:
    """Whether `pid` exists. EPERM counts as alive: it is someone else's."""
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def lock_state(lock: dict | None, hostname: str) -> tuple[str, str]:
    """Classify the lock: (state, one-line explanation).

    The same states `preflight.lock_state` returns, minus `ours` -- this hook
    runs as a child of `git commit` and never holds the lock itself.
    """
    if lock is None:
        return FREE, "no lock held"
    if lock.get("corrupt"):
        return CORRUPT, f"{LOCK_PATH} is unreadable"
    host = lock.get("hostname")
    if host != hostname:
        return FOREIGN, (
            f"the lock belongs to {host!r}, not this machine ({hostname!r})"
        )
    holder = lock.get("pid")
    if not isinstance(holder, int):
        return CORRUPT, "the lock records no usable pid"
    if pid_alive(holder):
        what = lock.get("what") or "unspecified work"
        return HELD, (
            f"pid {holder} is running {what} since {lock.get('started', 'unknown')}"
        )
    return STALE, f"pid {holder} is gone; the lock is stale"


def main(argv: list[str] | None = None) -> int:
    """Exit 1 to refuse a commit, 0 to allow it."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if os.environ.get("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN") == "1":
        logger.info("commit allowed: LOCAL_LLM_ALLOW_COMMIT_DURING_RUN=1")
        return 0

    state, why = lock_state(read_lock(), platform.node())

    if state == HELD:
        logger.error(
            "a benchmark holds the run lock -- %s. Committing would move "
            "HARNESS_HEAD and kill every remaining sweep, or split "
            "harness_dirty across the arms of a comparison. Wait for the run "
            "to finish, or set LOCAL_LLM_ALLOW_COMMIT_DURING_RUN=1 to commit "
            "anyway.",
            why,
        )
        return 1

    if state == CORRUPT:
        # Deliberately a refusal, and deliberately the one case that can block
        # a commit with no run in progress. `preflight` already treats a
        # corrupt lock as a busy machine, and the two disagreeing would be
        # worse than either. Unlike the old wrong match, this one says what to
        # do about it.
        logger.error(
            "the run lock is unreadable (%s), so this cannot tell whether a "
            "benchmark is running. Inspect %s and delete it if no run is live, "
            "or set LOCAL_LLM_ALLOW_COMMIT_DURING_RUN=1.",
            why,
            LOCK_PATH,
        )
        return 1

    # free, stale and foreign all allow the commit. A stale lock names a dead
    # pid and a foreign one names another machine; neither is a run this commit
    # can damage, and refusing on them is how a guard becomes noise.
    #
    # `foreign` diverges from `preflight`, which refuses it. That is deliberate
    # and rests on a fact that could change: there is one machine and one peer
    # here, so a lock from another hostname is a leftover, not a live claim. If
    # this tree is ever shared with a second machine -- a network home, a
    # synced checkout -- a foreign lock becomes a run in progress somewhere
    # else, and allowing the commit would damage it. Revisit this line then;
    # the choice is made, not missed.
    logger.debug("commit allowed: %s (%s)", state, why)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
