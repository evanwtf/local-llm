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
import time
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
    # ALWAYS build the environment explicitly, even with nothing to merge.
    #
    # #288. `env=None` hands the child the process's **C** environ, which is
    # not the same thing as `os.environ`. `import readline` calls
    # setenv("COLUMNS","80") and setenv("LINES","24") at the C level, and
    # Python's mapping is a snapshot that never reflects it -- so the keys are
    # invisible to every Python-level check, which is why #288's investigation
    # looked at the environment and found nothing.
    #
    # pytest imports readline unconditionally
    # (`_pytest/capture.py:161 at pytest 8.x` -> `_readline_workaround`), so
    # under the suite those keys are always in the C environ. Whether a
    # shell-vs-port differential then passes depends on which readline the
    # interpreter links: GNU sets them, libedit does not. That is the variable
    # #288 mistook for "the checkout" -- a `/tmp` worktree of the same commit
    # passed because its interpreter never put them there.
    #
    # Filtering COLUMNS/LINES in the comparison would have silenced six tests
    # and left the hazard: a benchmark child inheriting state nobody wrote
    # down. An explicit dict is immune to readline today and to whatever
    # mutates `environ` next.
    merged: dict[str, str] = {**os.environ, **(env or {})}
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


def group_alive(pgid: int) -> bool:
    """Whether any process is still in group `pgid`. EPERM counts as alive.

    Signal 0 to the GROUP, not to the leader. The leader is `run.py`; the
    thing that writes rows after a teardown is the `opencode` it spawned, and
    that grandchild stays in the group after the leader has exited.

    The group id cannot be handed to a new process while any member is alive
    (POSIX keeps a pgid reserved for as long as the group exists), so a live
    answer here is about our group and not a recycled pid.
    """
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_group(proc: subprocess.Popen[bytes], grace: float) -> bool:
    """Wait up to `grace` seconds for the whole group to go. True if it did.

    `proc.poll()` reaps the leader on each pass: an unreaped leader is a
    zombie, a zombie still answers signal 0, and the group would then read as
    alive for the whole grace period.
    """
    deadline = time.monotonic() + grace
    while True:
        proc.poll()
        if not group_alive(proc.pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def terminate(proc: subprocess.Popen[bytes], grace: float = GRACE_S) -> None:
    """Stop `proc`'s process group. A no-op when the whole group has exited.

    The test is the GROUP, never the leader alone. A leader that has exited --
    on its own, or on the SIGTERM below -- can leave a grandchild running, and
    until 2026-10-05 this returned as soon as the leader was gone: the group
    was never signalled, or never escalated to SIGKILL, and a `run.py` that
    exited while its `opencode` kept going was #268 over again.

    Never raises: this runs in a `finally`, often while an exception is already
    propagating, and a failure to reap must not replace the reason the driver
    is stopping.
    """
    proc.poll()
    if not group_alive(proc.pid):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            # The group is gone. This is the ordinary case for SIGKILL after a
            # SIGTERM the child honored, and it is the only silent return
            # here: nothing survives, so nothing is left holding the machine.
            return
        except OSError as exc:
            # Everything else means the signal did NOT land and the child may
            # still be alive -- PermissionError above all, which is a group we
            # cannot reach rather than a group that has exited. There is no
            # recovery (you cannot kill what you cannot signal), but the
            # caller is about to release the machine lock on the strength of
            # this returning, so it has to be loud. A silent return here reads
            # in the log exactly like a clean teardown.
            logger.error(
                "could not send %s to pid %d's group (%s); the child may still "
                "be running and the machine is about to be advertised as free",
                sig.name,
                proc.pid,
                exc,
            )
            return
        logger.info("sent %s to pid %d's group", sig.name, proc.pid)
        if _wait_group(proc, grace):
            return
    logger.warning("pid %d's group survived SIGKILL", proc.pid)
