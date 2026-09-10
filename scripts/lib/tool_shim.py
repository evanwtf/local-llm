"""The ds4 tool-format shim's lifecycle. #112, #235

Three drivers start this shim and each had its own copy: `targets_ab.sh` and
`strip_toggle_ab.sh` on :8101, `greedy_mtp_ab` on :8102 with a pinned
temperature. All three began with `pkill -f qwen_tool_shim`, which matches by
name across the whole machine and can never be scoped to "the shim I started".

On 2026-09-06 that cost a live batch. Five dry runs of `targets_ab.sh` were
verifying a cutoff fix; each reached the unguarded `pkill` at the end and killed
the shim belonging to the **real** batch in progress. Run 4's smoke gate got
"Connection refused", the batch ended after three runs -- legacy once, sandbox
twice -- and could not satisfy its own pre-registration. The machine lock did
not catch it because a dry run skips the lock.

Here the shim is a `unitctl` unit: stopping it signals a pid this process
recorded, so a run that started no shim can stop none. That removes the failure
structurally rather than by remembering a guard.

## Read the mode back, never trust the variable

The strip is worth **23 points of pass rate** (#112). A shim in the other mode
does not fail, it produces a different experiment, and nothing downstream would
ever show it. So `serving` waits for the shim's own startup line and refuses
unless it says what the caller asked for -- `SHIM_NO_STRIP=1` being set is not
evidence that the shim honoured it.
"""

from __future__ import annotations

import contextlib
import logging
import pathlib
import sys
import time
from collections.abc import Iterator, Mapping

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ports
import unitctl

logger = logging.getLogger(__name__)

SCRIPT = "ds4_qwen_tool_shim.py"
#: What to look for in the holder's command line. The same string the shell
#: gave `pgrep -f`, kept explicit rather than derived from SCRIPT: a marker
#: computed by string surgery is one rename away from matching nothing, and
#: matching nothing here reads as "the shim is not up".
MARKER = "qwen_tool_shim"
DEFAULT_PORT = 8101
STRIP_MARK = "scaffolding strip:"
STRIP_ON = f"{STRIP_MARK} ON"
STRIP_OFF = f"{STRIP_MARK} OFF"


class WrongMode(RuntimeError):
    """The shim started, and started in the other mode."""


class NeverReady(RuntimeError):
    """The shim did not come up."""


def unit_name(port: int) -> str:
    """One unit per port, so two shims on two ports are two units.

    `greedy_mtp_ab` runs a temperature-pinned shim on :8102 while :8101 keeps
    serving the 262 rows that already compare. A single unit name would have
    the second stop the first.
    """
    return f"tool-shim-{port}"


def argv(port: int, upstream_port: int) -> list[str]:
    return [
        "uv",
        "run",
        "python",
        SCRIPT,
        "--port",
        str(port),
        "--upstream",
        f"http://127.0.0.1:{upstream_port}",
    ]


def strip_env(strip: bool) -> tuple[dict[str, str], tuple[str, ...]]:
    """(set, unset) for the strip arm.

    Strip-on **removes** `SHIM_NO_STRIP` rather than setting it empty: the shim
    tests the variable's presence, and an operator with it exported would
    otherwise hand the off mode to the on arm. This is the same absence the
    #149 R arm is defined by, and the reason `unitctl.start` takes `unset`.
    """
    if strip:
        return {}, ("SHIM_NO_STRIP",)
    return {"SHIM_NO_STRIP": "1"}, ()


def mode_line(log: pathlib.Path) -> str | None:
    """The shim's own `scaffolding strip: ...` line, or None if it has not said."""
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if STRIP_MARK in line:
            return line.strip()
    return None


class NotServing(RuntimeError):
    """A shim this run does not own is supposed to be up, and is not."""


def require_running(port: int = DEFAULT_PORT) -> str:
    """Refuse unless a shim this run did NOT start is serving `port`.

    Two drivers need the long-lived :8101 shim and must not start or stop it:
    #149's route A/B and the #112 disk-KV test both say so in their headers.
    Both asked `pgrep -f qwen_tool_shim`, which reports that a process exists
    somewhere and says nothing about :8101.

    The port is what is actually in conflict, so it is found first; the name
    only confirms what was found. A bare ds4-server on :8101 answers a
    connection and strips nothing, and the strip is worth 23 points of pass
    rate (#112) -- so "something answers" is not the question.
    """
    ok, why = ports.held_by(port, MARKER)
    if not ok:
        raise NotServing(
            f"the tool-format shim is not serving :{port}: {why}. Start it: "
            f"uv run python {SCRIPT} --port {port} "
            "--upstream http://127.0.0.1:8000"
        )
    return why


@contextlib.contextmanager
def serving(
    log: pathlib.Path,
    *,
    repo: pathlib.Path,
    port: int = DEFAULT_PORT,
    upstream_port: int = 8000,
    strip: bool | None = True,
    env: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> Iterator[unitctl.Unit]:
    """Run the shim for the duration of the block, and always stop it.

    `strip=None` asks for no mode assertion, for a caller whose experiment is
    not about the strip. It still waits for the port.
    """
    name = unit_name(port)
    unitctl.stop(name)
    set_env, unset = strip_env(bool(strip)) if strip is not None else ({}, ())
    merged = {**set_env, **dict(env or {})}
    unit = unitctl.start(
        name,
        argv(port, upstream_port),
        log=log,
        cwd=repo,
        env=merged,
        unset=unset,
    )
    try:
        _wait(name, log, port, strip, timeout)
        logger.info("shim up on :%d (pid %d): %s", port, unit.pid, mode_line(log) or "")
        yield unit
    finally:
        unitctl.stop(name)


def _wait(
    name: str, log: pathlib.Path, port: int, strip: bool | None, timeout: float
) -> None:
    """Wait for the port and, when asked, for the right mode. Raises.

    Inside `serving`'s `try`, so a refusal here still stops the shim. A raise
    placed before the `try` leaked a payload-dumping shim in #256, one failure
    mode over from the one it was fixing.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if unitctl.state(unitctl.read(name)) != unitctl.RUNNING:
            raise NeverReady(f"the shim exited; see {log}")
        if ports.answers(port) and (strip is None or mode_line(log)):
            break
        time.sleep(0.25)
    else:
        raise NeverReady(f"the shim did not answer on :{port} within {timeout}s")
    if strip is None:
        return
    want = STRIP_ON if strip else STRIP_OFF
    got = mode_line(log)
    if got is None or want not in got:
        raise WrongMode(
            f"the shim did not report {want!r}; it said {got!r}. The strip is "
            "worth 23 points of pass rate (#112), so the other mode is a "
            "different experiment, not a worse one."
        )
