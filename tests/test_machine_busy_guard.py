"""The suite must refuse to run while a benchmark holds the machine.

Three paired comparisons were voided on 2026-09-06 because a test suite ran
during a measurement. The rule was stated clearly each time and broken each
time, by two different agents, so it is an artifact now rather than an
instruction.

The negative cases are the point. A guard that refuses when it should not is
worse than none: it would block the suite after any crashed benchmark, and
somebody would then set the override permanently.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import socket
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
# preflight and its siblings are imported by name; the agent directory has to
# be on the path for that.
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))


def _load_root_conftest():
    """Load the ROOT conftest by path, never by name.

    `benchmarks/agent/conftest.py` is also called `conftest`, and this file
    puts that directory first on `sys.path` so `preflight` resolves. A plain
    `import conftest` would then depend on which one pytest happened to have
    cached -- it would pass or fail on import order rather than on anything
    real. Addressing the file directly removes the ambiguity.
    """
    spec = importlib.util.spec_from_file_location(
        "local_llm_root_conftest", ROOT / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_root_conftest()


def _write_lock(tmp_path, **fields) -> pathlib.Path:
    lock = {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "what": "decode_ab_engine.sh main vs pr952",
        "started": "2026-09-06T16:38:19",
    }
    lock.update(fields)
    p = tmp_path / "run-lock.json"
    p.write_text(json.dumps(lock))
    return p


def test_the_guard_reads_the_same_lock_preflight_writes():
    """The duplication that matters is the PATH, so pin it to preflight's.

    preflight.LOCK_PATH was once derived from __file__, so a run launched
    from a worktree took a different lock file from one launched in the main
    checkout -- two agents, two locks, both reporting the machine free. If
    these two ever disagree the guard silently protects nothing.
    """
    import preflight

    assert guard.LOCK_PATH == preflight.LOCK_PATH


def test_a_live_claim_on_this_machine_blocks(tmp_path):
    lock = _write_lock(tmp_path)
    assert guard.machine_claim(lock) is not None


def test_no_lock_file_does_not_block(tmp_path):
    assert guard.machine_claim(tmp_path / "absent.json") is None


def test_a_dead_holder_does_not_block(tmp_path):
    """A crashed benchmark must not lock testing out until someone tidies up."""
    lock = _write_lock(tmp_path, pid=2**31 - 1)
    assert guard.machine_claim(lock) is None


def test_another_machines_claim_does_not_block(tmp_path):
    """A lock is a claim on one machine and must not travel."""
    lock = _write_lock(tmp_path, hostname="some-other-host")
    assert guard.machine_claim(lock, hostname=socket.gethostname()) is None


def test_an_unreadable_lock_does_not_block(tmp_path):
    p = tmp_path / "run-lock.json"
    p.write_text("{not json")
    assert guard.machine_claim(p) is None


def test_a_lock_that_is_not_an_object_does_not_block(tmp_path):
    p = tmp_path / "run-lock.json"
    p.write_text("[1, 2, 3]")
    assert guard.machine_claim(p) is None


def test_a_lock_with_no_usable_pid_does_not_block(tmp_path):
    lock = _write_lock(tmp_path, pid="not-a-pid")
    assert guard.machine_claim(lock) is None


def test_the_refusal_names_what_is_running_and_the_way_out(tmp_path):
    """A refusal nobody can act on gets overridden blind."""
    lock = json.loads(_write_lock(tmp_path).read_text())
    msg = guard.refusal_message(lock)
    assert "decode_ab_engine.sh main vs pr952" in msg
    assert "16:38:19" in msg
    assert guard.OVERRIDE_ENV in msg


def test_the_session_hook_raises_when_the_machine_is_claimed(monkeypatch):
    monkeypatch.delenv(guard.OVERRIDE_ENV, raising=False)
    monkeypatch.setattr(guard, "machine_claim", lambda: {"pid": 1, "what": "a run"})
    with pytest.raises(pytest.UsageError, match="REFUSING to run tests"):
        guard.pytest_sessionstart(None)


def test_the_override_lets_the_session_start(monkeypatch):
    monkeypatch.setenv(guard.OVERRIDE_ENV, "1")
    monkeypatch.setattr(guard, "machine_claim", lambda: {"pid": 1, "what": "a run"})
    guard.pytest_sessionstart(None)


def test_a_free_machine_lets_the_session_start(monkeypatch):
    monkeypatch.delenv(guard.OVERRIDE_ENV, raising=False)
    monkeypatch.setattr(guard, "machine_claim", lambda: None)
    guard.pytest_sessionstart(None)


# --- #189: the claim is re-read between tests, not only at session start ---


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(guard, "_clock", c)
    monkeypatch.setattr(guard, "_last_recheck", None)
    monkeypatch.delenv(guard.OVERRIDE_ENV, raising=False)
    return c


def test_a_lock_that_appears_between_tests_stops_the_session(monkeypatch, clock):
    claims = iter([None, {"pid": 1, "what": "a run", "started": "t"}])
    monkeypatch.setattr(guard, "machine_claim", lambda: next(claims))
    guard.pytest_runtest_setup(None)
    clock.now += guard.RECHECK_INTERVAL_S
    with pytest.raises(pytest.exit.Exception) as exc:
        guard.pytest_runtest_setup(None)
    assert "REFUSING to run tests" in str(exc.value)
    assert "a run" in str(exc.value)
    assert guard.OVERRIDE_ENV in str(exc.value)
    assert exc.value.returncode == 2


def test_the_recheck_is_throttled(monkeypatch, clock):
    calls = []
    monkeypatch.setattr(guard, "machine_claim", lambda: calls.append(1))
    for _ in range(50):
        guard.pytest_runtest_setup(None)
        clock.now += 0.01
    assert len(calls) == 1
    clock.now += guard.RECHECK_INTERVAL_S
    guard.pytest_runtest_setup(None)
    assert len(calls) == 2


def test_a_released_lock_does_not_wedge_a_later_check(monkeypatch, clock):
    # A crashed benchmark leaves a dead pid; machine_claim() already reports
    # that as None. The recheck must not remember an earlier refusal.
    claims = iter([{"pid": 1, "what": "a run"}, None])
    monkeypatch.setattr(guard, "machine_claim", lambda: next(claims))
    with pytest.raises(pytest.exit.Exception):
        guard.pytest_runtest_setup(None)
    clock.now += guard.RECHECK_INTERVAL_S
    guard.pytest_runtest_setup(None)


def test_the_override_skips_the_recheck(monkeypatch, clock):
    monkeypatch.setenv(guard.OVERRIDE_ENV, "1")
    monkeypatch.setattr(guard, "machine_claim", lambda: {"pid": 1, "what": "a run"})
    guard.pytest_runtest_setup(None)


def test_a_free_machine_passes_every_recheck(monkeypatch, clock):
    monkeypatch.setattr(guard, "machine_claim", lambda: None)
    for _ in range(5):
        guard.pytest_runtest_setup(None)
        clock.now += guard.RECHECK_INTERVAL_S
