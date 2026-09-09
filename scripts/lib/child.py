"""Run a measurement child so that stopping the driver stops it too. #268

A driver has two kinds of subprocess and, until this module, managed one of
them. The engine is a `unitctl` unit: its pid is recorded, it gets a session of
its own, and a `finally` stops it. The measurement -- `run.py`, which is the
thing that writes rows -- was a plain `subprocess.run`.

On 2026-09-09 a driver was stopped with SIGINT. Its context managers stopped
the server, cleared the unit records and released the machine lock, all
correctly. Its `run.py` child kept going for five more minutes and wrote three
rows against a server that no longer existed, on a machine the lock now
advertised as free. `subprocess.run` kills its immediate child on an exception,
but `run.py` re-spawns `opencode`, and a signal sent to the driver reaches
neither.

The fix is the one `unitctl.start` already uses for the engine:
`start_new_session=True` gives the child a process group whose id is its pid,
so the whole tree can be signalled at once, and the kill goes in a `finally`.

**Order matters more than the kill.** The teardown that produced those rows ran
in exactly the wrong sequence: server first, lock second, child never. A lock
released while work continues is worse than a leaked process, because the next
run is invited onto a busy machine. Hold the lock outside this, so it is
released after the child is confirmed dead.
"""

from __future__ import annotations

import logging
import os
import pathlib
import signal
import subprocess
from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

#: How long a group gets to exit on SIGTERM before it is killed. A benchmark
#: child has files open and a partial row to finish; a second is not generous
#: but it is not nothing, and the alternative to waiting is a truncated line.
GRACE_S = 5.0


def run(
    argv: Sequence[str],
    *,
    cwd: pathlib.Path,
    log: pathlib.Path,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    unset: Sequence[str] = (),
    append: bool = False,
) -> int:
    """Run `argv` to completion, and kill its whole tree on any exit path.

    Returns the exit status. Raises whatever the caller's interruption raises,
    after the tree is down -- the exception a driver was stopped by must not be
    replaced by a teardown detail.

    `env` merges into the current environment and `unset` REMOVES keys from
    it, which is `unitctl.start`'s contract and exists for the same reason: an
    arm defined by a variable being absent cannot be expressed as a dict. A
    merged dict has no way to say "not set" -- a key it does not mention is
    inherited -- so `env -u DS4_METAL_ENABLE_TENSOR` needs `unset`, and #149's
    withheld arm is exactly that arm.

    `append` keeps what the log already holds. A driver that writes the arm's
    own definition into the log before starting the child needs it, so the
    admission probe can read which knob was set rather than trust a table.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    merged: dict[str, str] | None = None
    if env or unset:
        merged = {**os.environ, **(env or {})}
        for key in unset:
            merged.pop(key, None)
    with log.open("ab" if append else "wb") as handle:
        proc = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=merged,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            # The whole point. A new session makes this child a group leader
            # whose pgid is its pid, so `opencode` and anything else it spawns
            # can be signalled together.
            start_new_session=True,
        )
    try:
        return proc.wait(timeout)
    finally:
        terminate(proc)


def terminate(proc: subprocess.Popen[bytes], grace: float = GRACE_S) -> None:
    """Stop `proc`'s process group. A no-op when it has already exited.

    Never raises: this runs in a `finally`, often while an exception is already
    propagating, and a failure to reap must not replace the reason the driver
    is stopping.
    """
    if proc.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        except OSError:
            logger.warning("could not signal pid %d's group", proc.pid, exc_info=True)
            return
        logger.info("sent %s to pid %d's group", sig.name, proc.pid)
        try:
            proc.wait(grace)
            return
        except subprocess.TimeoutExpired:
            continue
    logger.warning("pid %d survived SIGKILL", proc.pid)
