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
