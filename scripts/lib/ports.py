"""Identify a process by the port it holds, not by its name. #235

Every process-identification bug in this repo came from matching a name.
`pgrep -f 'ds4-server --metal'` matched the shell that quoted the pattern.
The widened commit guard matched seven waiter shells. `foreign()` matched a
whole command line instead of the executable. `preflight.parse_ps` got it
right the first time -- matching `command.split()[0]` -- only because it had
already been burned.

A port is the resource actually in conflict, and exactly one process holds it.
`lsof` answers that question directly, so nothing has to be searched for.

**A name is still worth checking, second.** Knowing that :8101 is held is not
the same as knowing the tool-format shim holds it: a bare ds4-server on that
port answers a connection and strips nothing (#112). So `holder()` finds the
pid and `command_of()` says what it is -- a confirmation of an identified
process, never a search across all of them.
"""

from __future__ import annotations

import logging
import socket
import subprocess

logger = logging.getLogger(__name__)

TIMEOUT_S = 10


def answers(port: int, timeout: float = 1.0) -> bool:
    """Whether something accepts a connection on `port`.

    Cheaper than `holder` and enough for "is it up yet". It is not enough for
    "is the right thing up": use `holder` and `command_of` for that.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def holder(port: int) -> int | None:
    """The pid listening on `port`, or None.

    None covers three cases that are the same fact from here: nothing is
    listening, `lsof` is absent, and `lsof` hung. A caller that needs to tell
    them apart wants a different tool.
    """
    try:
        done = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = done.stdout.split()
    return int(first[0]) if first and first[0].isdigit() else None


def command_of(pid: int) -> str | None:
    """The full command line of `pid`, or None when it is gone.

    `ps -p` and not `ps -eo ... | grep`: one process is asked about by number.
    """
    try:
        done = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = done.stdout.strip()
    return line or None


def held_by(port: int, marker: str) -> tuple[bool, str]:
    """Whether `port` is held by a process whose command line names `marker`.

    Returns (ok, why) so a refusal can say which of the three things went
    wrong: nothing is listening, something is listening and we cannot read it,
    or something else entirely is listening. A driver that collapses those into
    one message sends the reader to the wrong place.
    """
    pid = holder(port)
    if pid is None:
        return False, f"nothing is listening on :{port}"
    command = command_of(pid)
    if command is None:
        return False, f":{port} is held by pid {pid}, which is already gone"
    if marker not in command:
        return False, (
            f":{port} is held by pid {pid}, which is not {marker!r}: {command}"
        )
    return True, f":{port} held by pid {pid} ({marker})"
