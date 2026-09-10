"""Tests for the machine claim (#160).

The claim is the intent on the run lock: who holds the machine, what for,
when it finishes, and whether it is quiet. The wrong answer here is a claim
that cannot say who holds the machine -- the #146 confound wearing a name --
or a claim that is taken while another agent is being timed. So the tests pin
the identity requirement and the intent fields.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import machine_claim
import preflight


def _lock_path(tmp_path: pathlib.Path, monkeypatch) -> pathlib.Path:
    p = tmp_path / "run-lock.json"
    monkeypatch.setattr(preflight, "LOCK_PATH", p)
    return p


def test_acquire_refuses_when_identity_unidentified(tmp_path, monkeypatch):
    """An unattributed claim is worth less than no claim (#160 amendment 1)."""
    for var in (
        machine_claim.agent_identity.AGENT_VAR,
        machine_claim.agent_identity.MODEL_VAR,
        machine_claim.agent_identity.EFFORT_VAR,
    ):
        monkeypatch.delenv(var, raising=False)
    _lock_path(tmp_path, monkeypatch)
    assert machine_claim.acquire("decode A/B", None, False) == 2
    assert not (tmp_path / "run-lock.json").exists(), (
        "an unattributed claim must not be written"
    )


def test_acquire_writes_intent_fields(tmp_path, monkeypatch):
    """The claim records who, what, when and whether quiet."""
    monkeypatch.setenv(machine_claim.agent_identity.AGENT_VAR, "opus-llama")
    monkeypatch.setenv(machine_claim.agent_identity.MODEL_VAR, "claude-opus-5")
    monkeypatch.setenv(machine_claim.agent_identity.EFFORT_VAR, "high")
    lock = _lock_path(tmp_path, monkeypatch)
    assert machine_claim.acquire("decode A/B", "11:45", True) == 0
    got = json.loads(lock.read_text())
    assert got["agent"] == "opus-llama"
    assert got["agent_model"] == "claude-opus-5"
    assert got["agent_effort"] == "high"
    assert got["expected_finish"] == "11:45"
    assert got["quiet"] is True
    assert got["what"] == "decode A/B"


def test_acquire_refuses_when_held(tmp_path, monkeypatch):
    """A live claim by another process is a refusal, not a warning."""
    monkeypatch.setenv(machine_claim.agent_identity.AGENT_VAR, "opus-llama")
    monkeypatch.setenv(machine_claim.agent_identity.MODEL_VAR, "claude-opus-5")
    monkeypatch.setenv(machine_claim.agent_identity.EFFORT_VAR, "high")
    _lock_path(tmp_path, monkeypatch)
    assert machine_claim.acquire("first", None, False) == 0
    # A second acquire from a different pid must refuse.
    assert machine_claim.acquire("second", None, False) == 1


def test_status_free_when_no_lock(tmp_path, monkeypatch):
    _lock_path(tmp_path, monkeypatch)
    assert machine_claim.status() == 0


def test_status_busy_when_held(tmp_path, monkeypatch):
    """A held claim reports who holds it and exits non-zero (the gate)."""
    lock = _lock_path(tmp_path, monkeypatch)
    # pid 1 is always alive, so the claim reads as held by another process,
    # not ours -- the gate must fire for a live foreign holder.
    lock.write_text(
        json.dumps(
            {
                "hostname": "MacBook-Pro.internal",
                "pid": 1,
                "what": "decode A/B",
                "started": "2026-09-06T11:00:00",
                "agent": "opus-llama",
                "agent_model": "claude-opus-5",
                "agent_effort": "high",
                "quiet": True,
            }
        )
    )
    assert machine_claim.status() == 1


def test_release_drops_our_claim(tmp_path, monkeypatch):
    monkeypatch.setenv(machine_claim.agent_identity.AGENT_VAR, "opus-llama")
    monkeypatch.setenv(machine_claim.agent_identity.MODEL_VAR, "claude-opus-5")
    monkeypatch.setenv(machine_claim.agent_identity.EFFORT_VAR, "high")
    lock = _lock_path(tmp_path, monkeypatch)
    assert machine_claim.acquire("decode A/B", None, False) == 0
    assert machine_claim.release() == 0
    assert not lock.exists()


# --- #275: a claim held by an agent session outlives the CLI process --------
#
# `machine_claim.py acquire` runs as a short-lived CLI process and exits at
# once, but the thing it claims the machine FOR is an agent session that keeps
# working. A claim keyed on the CLI's own pid is dead the instant it is
# written: the machine reads FREE to every other run, and `release` refuses
# because the pid it would match is gone. These tests pin the claim to the
# agent identity, not to a process, so it survives the exit. They simulate the
# exit the only way a single test process can -- by reporting the recorded pid
# as dead.


_DEAD_PID = 2147483646  # not a running process on any real system


def _set_identity(monkeypatch, agent: str) -> None:
    monkeypatch.setenv(machine_claim.agent_identity.AGENT_VAR, agent)
    monkeypatch.setenv(machine_claim.agent_identity.MODEL_VAR, "claude-opus-5")
    monkeypatch.setenv(machine_claim.agent_identity.EFFORT_VAR, "high")


def _simulate_acquiring_process_exit(lock: pathlib.Path, monkeypatch) -> None:
    """The claim was written by a CLI process that has since exited.

    A single test process cannot really fork-and-die, and its own pid always
    reads as the lock's owner (`lock_state` returns `ours` before it ever
    checks liveness). So rewrite the recorded pid to a foreign, dead one --
    which is exactly what the recorded pid becomes the instant the CLI exits --
    and report it dead.
    """
    data = json.loads(lock.read_text())
    data["pid"] = _DEAD_PID
    lock.write_text(json.dumps(data))
    monkeypatch.setattr(preflight, "_pid_alive", lambda pid: pid != _DEAD_PID)


def test_claim_reads_busy_after_acquiring_process_exits(tmp_path, monkeypatch):
    """The documented acquire must NOT leave the machine reading free (#275)."""
    _set_identity(monkeypatch, "opus-llama")
    lock = _lock_path(tmp_path, monkeypatch)
    assert machine_claim.acquire("decode A/B", "11:45", False) == 0
    _simulate_acquiring_process_exit(lock, monkeypatch)
    assert machine_claim.status() == 1, (
        "a claim whose acquiring process has exited must still read BUSY -- "
        "otherwise another run starts against a machine someone reserved"
    )


def test_claim_released_by_its_agent_after_process_exits(tmp_path, monkeypatch):
    """release must work from a new process, keyed on agent not pid (#275)."""
    _set_identity(monkeypatch, "opus-llama")
    lock = _lock_path(tmp_path, monkeypatch)
    assert machine_claim.acquire("decode A/B", None, False) == 0
    _simulate_acquiring_process_exit(lock, monkeypatch)
    assert machine_claim.release() == 0
    assert not lock.exists()


def test_claim_not_released_by_a_different_agent(tmp_path, monkeypatch):
    """Ownership is the agent identity: another agent must not drop the claim."""
    _set_identity(monkeypatch, "opus-llama")
    lock = _lock_path(tmp_path, monkeypatch)
    assert machine_claim.acquire("decode A/B", None, False) == 0
    _simulate_acquiring_process_exit(lock, monkeypatch)
    _set_identity(monkeypatch, "sonnet-codex")
    assert machine_claim.release() == 1, (
        "a claim carries an owner; a different agent releasing it is the same "
        "mistake as stealing a stale lock"
    )
    assert lock.exists()
