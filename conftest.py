"""Refuse to run the suite while a benchmark holds the machine.

Three paired comparisons were voided on 2026-09-06 for one reason: a test
suite ran on the same machine as a measurement, landing on some arms and not
others. Asymmetric load inside a paired comparison cannot be separated from
the effect afterwards, so the run is lost.

- `f309990` run 2: a peer's pytest suite across arm A and not arm B.
- `knob-gathered-heads` run 2: my own single test file inside one arm.
- `main-vs-pr952` run 1: a peer's full suite across three of six arms.

Every one of those was preceded by a clear instruction not to run CPU work,
and every one happened anyway. Three instances from two different agents is a
mechanism problem, not an attention problem. The run lock already records that
the machine is claimed; nothing consulted it before running tests.

Now the suite consults it. `preflight.py` writes the lock when a benchmark
starts and removes it when the benchmark ends, so this needs no discipline
from anyone: while a measurement is on the machine, `pytest` refuses to start
and says what is running.

Set `LOCAL_LLM_ALLOW_TESTS_DURING_RUN=1` to override. There are honest reasons
to -- debugging the harness while a long batch runs, and accepting that the
batch is then not publishable -- so the escape hatch exists and is loud rather
than absent. It is not a way to save time on a run you intend to quote.

CI is unaffected: no benchmark runs there, so no lock file exists, so the check
passes without doing anything.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket

import pytest

#: Where `preflight.py` keeps the machine claim.
#:
#: Duplicated from `preflight.LOCK_PATH` rather than imported: importing
#: preflight needs `benchmarks/agent` on `sys.path`, and a root conftest that
#: reorders `sys.path` before every other conftest is a worse problem than a
#: repeated constant. `tests/test_machine_busy_guard.py` asserts the two are
#: equal, so the duplication cannot drift -- the same treatment a version
#: number in two files gets.
#:
#: The path itself is the part that must not drift. It was derived from
#: `__file__` once, so a run launched from a worktree took a different lock
#: file from one launched in the main checkout: two agents, two locks, both
#: reporting the machine free.
LOCK_PATH = pathlib.Path.home() / ".local-llm-bench" / "run-lock.json"

OVERRIDE_ENV = "LOCAL_LLM_ALLOW_TESTS_DURING_RUN"


def _pid_alive(pid: int) -> bool:
    """Whether a pid exists. Signal 0 checks without delivering anything."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive and owned by somebody else. Still alive, which is the question.
        return True
    except (OverflowError, ValueError):
        return False
    return True


def machine_claim(
    path: pathlib.Path = LOCK_PATH, hostname: str | None = None
) -> dict[str, object] | None:
    """The live claim on THIS machine, or None.

    None covers every reason the lock does not block us: no file, unreadable,
    another host's claim, or a holder that has exited. A stale lock is not a
    reason to refuse -- the benchmark that wrote it is gone, and refusing on it
    would make a crashed run block testing until someone cleaned up by hand.
    """
    host = hostname if hostname is not None else socket.gethostname()
    try:
        lock = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(lock, dict):
        return None
    if lock.get("hostname") != host:
        return None
    pid = lock.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        return None
    return lock


def refusal_message(lock: dict[str, object]) -> str:
    """Say what is running, since when, and how to proceed anyway."""
    return (
        "REFUSING to run tests: a measurement holds this machine.\n"
        f"  what:    {lock.get('what', 'unspecified work')}\n"
        f"  pid:     {lock.get('pid')}\n"
        f"  started: {lock.get('started', 'unknown')}\n"
        f"  lock:    {LOCK_PATH}\n"
        "A test suite inside a paired comparison lands on some arms and not "
        "others, and that cannot be separated from the effect afterwards. "
        "Three runs were voided this way on 2026-09-06.\n"
        f"Wait for the run, or set {OVERRIDE_ENV}=1 and accept that anything "
        "measured while it runs is not publishable."
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    if os.environ.get(OVERRIDE_ENV):
        return
    lock = machine_claim()
    if lock is not None:
        raise pytest.UsageError(refusal_message(lock))
