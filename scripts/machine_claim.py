"""Claim the machine with intent, and make contention impossible to miss. #160

`preflight.py` owns the run lock -- one owner for that rule. This script
extends it, it does not replace it: the same `.run-lock.json`, the same
O_EXCL acquisition, the same refusal to steal a stale lock. What it adds is
**intent** -- who holds the machine, what for, when it expects to finish, and
a `quiet` flag meaning no CPU-heavy work by anyone because the holder is being
timed.

The #146 confound was exactly this gap: the run lock said a batch was running
but not that the sandbox arm was being timed, so another agent made API calls
and read gguf headers during one arm and not the other, and the result --
sandbox 2.1x slower -- needed a clean repeat because the noise could not be
separated from the effect. A `quiet` claim is something the other agent's
tooling can see and refuse to run against, not something it has to remember
from a message.

The holder is recorded with its agent identity -- session, model and effort,
taken from `LOCAL_LLM_AGENT`, `LOCAL_LLM_MODEL` and `LOCAL_LLM_EFFORT`
(#160 amendment 1 & 2). The identity is never self-reported, and an
unattributed claim is worth less than no claim, so `acquire` refuses when the
identity is unidentified.

Usage:

    uv run python scripts/machine_claim.py acquire --what "decode A/B" [--expected-finish 11:45] [--quiet]
    uv run python scripts/machine_claim.py release
    uv run python scripts/machine_claim.py status
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import platform
import sys

# preflight owns the run lock; machine_claim extends it, nothing adds a second
# one (#160).
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)
import preflight

# The agent identity comes from the environment, never from introspection.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib import agent_identity

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)
agent_identity.install(logger)


def _holder(lock: dict[str, object]) -> str:
    """A one-line description of who holds the machine, model included."""
    agent = lock.get("agent") or "unidentified"
    model = lock.get("agent_model") or "unidentified"
    effort = lock.get("agent_effort") or "unidentified"
    return f"{agent}/{model}/effort={effort}"


def acquire(what: str, expected_finish: str | None, quiet: bool) -> int:
    """Claim the machine. Refuses when the identity is unidentified or the
    machine is taken."""
    if not agent_identity.is_identified():
        logger.error(
            "REFUSING to claim the machine: agent identity is %s -- set %s, %s, %s",
            agent_identity.log_label(),
            agent_identity.AGENT_VAR,
            agent_identity.MODEL_VAR,
            agent_identity.EFFORT_VAR,
        )
        return 2
    agent, model, effort = agent_identity.identity()
    ok, why = preflight.acquire_lock(
        what,
        path=preflight.LOCK_PATH,
        agent=agent,
        agent_model=model,
        agent_effort=effort,
        expected_finish=expected_finish,
        quiet=quiet,
    )
    if not ok:
        logger.error("cannot claim the machine: %s", why)
        return 1
    logger.info("machine claimed: %s", why)
    return 0


def release() -> int:
    """Drop our own claim. Never removes somebody else's."""
    ok, why = preflight.release_lock(path=preflight.LOCK_PATH)
    if not ok:
        logger.error("cannot release the machine: %s", why)
        return 1
    logger.info("machine released: %s", why)
    return 0


def status() -> int:
    """Report who holds the machine. Exits non-zero when it is busy.

    The exit code is the gate: a script that must not run CPU-heavy work while
    the machine is claimed or quiet can call `status` and refuse on non-zero.
    """
    lock = preflight.read_lock(preflight.LOCK_PATH)
    state, why = preflight.lock_state(lock, platform.node(), os.getpid())
    if lock is None:
        logger.info("machine free: %s", why)
        return 0
    if state == "stale":
        logger.warning("machine claim is stale: %s", why)
        return 0
    if state == "ours":
        logger.info("machine held by this process: %s", why)
        return 0
    if state in ("held", "corrupt", "foreign"):
        holder = _holder(lock) if isinstance(lock, dict) else "unidentified"
        quiet = (
            " (quiet: a timed run, no CPU-heavy work by anyone)"
            if isinstance(lock, dict) and lock.get("quiet")
            else ""
        )
        finish = ""
        if isinstance(lock, dict) and lock.get("expected_finish"):
            finish = f", expected finish {lock['expected_finish']}"
        logger.error("machine busy: held by %s%s%s -- %s", holder, quiet, finish, why)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    acquire_parser = sub.add_parser("acquire", help="claim the machine with intent")
    acquire_parser.add_argument("--what", required=True, help="what the machine is for")
    acquire_parser.add_argument(
        "--expected-finish",
        default=None,
        help="when the work expects to finish, e.g. 11:45",
    )
    acquire_parser.add_argument(
        "--quiet",
        action="store_true",
        help="no CPU-heavy work by anyone; the holder is being timed",
    )
    sub.add_parser("release", help="drop our own claim")
    sub.add_parser("status", help="report who holds the machine; non-zero when busy")
    args = parser.parse_args(argv)
    logs.configure(fmt="%(asctime)s %(agent)s %(name)s %(levelname)s %(message)s")
    if args.cmd == "acquire":
        return acquire(args.what, args.expected_finish, args.quiet)
    if args.cmd == "release":
        return release()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
