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
import re
import socket
import time

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


#: Seconds between re-reads of the claim once the session is running (#189).
#:
#: The session-start check alone let a suite that started on a free machine run
#: to completion after a benchmark claimed it. Re-reading before every test
#: stops the suite within seconds of the claim. The throttle keeps a free
#: machine's cost at one small file read per interval, not one per test.
RECHECK_INTERVAL_S = 5.0

_clock = time.monotonic
_last_recheck: float | None = None


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Stop the session if a benchmark claimed the machine since it started.

    `pytest.exit`, not a skip: a suite that skips its remaining tests reports
    green-ish, and nobody acts on that.
    """
    global _last_recheck
    if os.environ.get(OVERRIDE_ENV):
        return
    now = _clock()
    if _last_recheck is not None and now - _last_recheck < RECHECK_INTERVAL_S:
        return
    _last_recheck = now
    lock = machine_claim()
    if lock is not None:
        pytest.exit(refusal_message(lock), returncode=2)


#: Set to 1 in CI (#223). A skip whose reason is not in EXPECTED_SKIPS then
#: fails the run. Off by default: a developer machine skips a different set.
STRICT_SKIPS_ENV = "LOCAL_LLM_STRICT_SKIPS"

#: The skips the Linux CI runner is expected to print, as full-match patterns.
#: Taken from the first -rs run (#417, 2026-09-15): 37 skips, 15 reasons. A new
#: reason fails CI until someone adds it here on purpose -- a guard that goes
#: quiet must say so, not blend into a count.
EXPECTED_SKIPS = (
    (
        r"no measured trials for this machine at .+/results\.jsonl; these tests "
        r"read measured data and there is none here \(a dry-run-only ledger does "
        r"not count\)"
    ),
    r"one client version measured everything; nothing to caveat",
    r"results\.jsonl not present",
    r".+/git/(gmail-archive|monitor) not checked out",
    r"script task: nothing is excised from a repo",
    r"script task: the prompt names no repository file",
    r"IOKit thermal sensors are macOS-only; there is no Linux equivalent to read",
    r"gmail-archive at the pinned commit, and uv",
    r"no ledger for this machine",
    r"no ds4 checkout at ~/git/ds4-main",
    r"no ds4 tree here",
    r"no ds4 tree checked out",
    r"this machine \(.+\) is not one we manage",
)


def strict_skips_enabled() -> bool:
    return os.environ.get(STRICT_SKIPS_ENV) == "1"


def unexpected_skips(reasons: list[str]) -> list[str]:
    """The reasons that match no EXPECTED_SKIPS pattern, in order."""
    out = []
    for reason in reasons:
        text = reason.removeprefix("Skipped: ")
        if not any(re.fullmatch(pattern, text) for pattern in EXPECTED_SKIPS):
            out.append(reason)
    return out


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the session on an unexpected skip when STRICT_SKIPS_ENV is 1."""
    if not strict_skips_enabled():
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    reasons = []
    for report in reporter.stats.get("skipped", []):
        longrepr = report.longrepr
        if isinstance(longrepr, tuple) and len(longrepr) == 3:
            reasons.append(str(longrepr[2]))
    unexpected = unexpected_skips(reasons)
    if not unexpected:
        return
    reporter.write_line(
        f"UNEXPECTED SKIPS ({len(unexpected)}): add a reason to EXPECTED_SKIPS in "
        "conftest.py only if the skip is expected on this runner (#223)",
        red=True,
    )
    for reason in unexpected:
        reporter.write_line(f"  {reason}", red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def _isolate_unit_records(tmp_path, monkeypatch):
    """No test writes a unit record into the real machine's state.

    `unitctl.STATE_DIR` is read from the environment at IMPORT time, so the
    obvious isolation -- `monkeypatch.setenv("LOCAL_LLM_UNIT_DIR", tmp)` -- is
    a silent no-op: the module constant is already bound, and the port writes
    its record to `~/.local-llm-bench/units` on the real machine. A peer
    session hit this on 2026-09-09 while writing the #235 retirement
    differentials, wrote it down in a commit message, and warned me.

    A commit message is discipline. This file's own header makes the argument
    against relying on that: three voided runs were "a mechanism problem, not
    an attention problem". So the mechanism goes here, where it costs nobody
    anything to remember.

    `syspath_prepend` and the import happen at FIXTURE time rather than at
    conftest import, so this does not reorder `sys.path` ahead of every other
    conftest -- which is the reason `LOCK_PATH` above is duplicated instead of
    imported.
    """
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).parent / "scripts"))
    import unitctl

    monkeypatch.setattr(unitctl, "STATE_DIR", tmp_path / "units")
