"""Tests for the evidence verifier (#160).

The verifier re-runs commands written by another agent, which makes it a small
execution engine pointed at this machine. The wrong answer here is a finding
that looks attributed but is not, or a verification that mutates the machine it
is supposed to be reading. So the tests pin the two boundaries: the schema
refuses an unattributed finding, and the argv gate refuses anything that is
not a read.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import preflight

import evidence

REPO = pathlib.Path(__file__).resolve().parents[1]
DOGFOOD = REPO / "evidence" / "0078-stale-not-broken.json"


def _finding(**overrides) -> dict:
    base = {
        "schema": evidence.SCHEMA,
        "task": "t",
        "issue": 1,
        "agent": "opus-llama",
        "agent_model": "claude-opus-5",
        "agent_effort": "high",
        "statement": "s",
        "claims": [],
    }
    base.update(overrides)
    return base


def _write(tmp_path: pathlib.Path, body: dict) -> pathlib.Path:
    p = tmp_path / "finding.json"
    p.write_text(json.dumps(body))
    return p


def test_load_rejects_missing_identity_keys(tmp_path):
    """A finding that cannot say who produced it is not a finding (#160)."""
    for key in ("agent", "agent_model", "agent_effort"):
        body = _finding()
        del body[key]
        with pytest.raises(evidence.Refused, match=key):
            evidence.load_finding(_write(tmp_path, body))


def test_load_rejects_unidentified_agent(tmp_path):
    """An unattributed finding is worth less than no finding."""
    body = _finding(agent="unidentified")
    with pytest.raises(evidence.Refused, match="unattributed"):
        evidence.load_finding(_write(tmp_path, body))


def test_load_rejects_wrong_schema(tmp_path):
    body = _finding(schema="local-llm/evidence-1")
    with pytest.raises(evidence.Refused, match="schema"):
        evidence.load_finding(_write(tmp_path, body))


def test_new_refuses_when_identity_unidentified(tmp_path, monkeypatch):
    """The write path must refuse, not write a blank (#160 amendment 1)."""
    for var in (
        evidence.agent_identity.AGENT_VAR,
        evidence.agent_identity.MODEL_VAR,
        evidence.agent_identity.EFFORT_VAR,
    ):
        monkeypatch.delenv(var, raising=False)
    out = tmp_path / "finding.json"
    assert evidence.new_finding(1, "t", out) == 2
    assert not out.exists(), "an unattributed finding must not be written"


def test_new_writes_skeleton_with_identity(tmp_path, monkeypatch):
    """The identity lands in the artifact, taken from the environment."""
    monkeypatch.setenv(evidence.agent_identity.AGENT_VAR, "opus-llama")
    monkeypatch.setenv(evidence.agent_identity.MODEL_VAR, "claude-opus-5")
    monkeypatch.setenv(evidence.agent_identity.EFFORT_VAR, "high")
    out = tmp_path / "finding.json"
    assert evidence.new_finding(158, "A", out) == 0
    got = json.loads(out.read_text())
    assert got["agent"] == "opus-llama"
    assert got["agent_model"] == "claude-opus-5"
    assert got["agent_effort"] == "high"
    assert got["schema"] == evidence.SCHEMA


def test_gate_refuses_denylisted_rm():
    """rm is denylisted even though it is not in the reader allowlist."""
    claim = {"id": "x", "argv": ["rm", "-rf", "/"]}
    with pytest.raises(evidence.Refused, match="denylisted"):
        evidence.gate_argv(claim)


def test_gate_refuses_git_push():
    """git is allowed, but only its read verbs -- push is absent."""
    claim = {"id": "x", "argv": ["git", "push", "origin", "main"]}
    with pytest.raises(evidence.Refused, match="not a read-only verb"):
        evidence.gate_argv(claim)


def test_gate_refuses_gh_entirely():
    """gh is denylisted wholesale: its API verbs blur read and write."""
    claim = {"id": "x", "argv": ["gh", "issue", "view", "1"]}
    with pytest.raises(evidence.Refused, match="denylisted entirely"):
        evidence.gate_argv(claim)


def test_gate_refuses_run_py():
    """run.py is denylisted: a verifier that can launch trials is a runner."""
    claim = {"id": "x", "argv": ["run.py", "--task", "A"]}
    with pytest.raises(evidence.Refused, match="denylisted"):
        evidence.gate_argv(claim)


def test_gate_refuses_a_non_reader():
    """A tool not in the allowlist is refused, not discussed."""
    claim = {"id": "x", "argv": ["python", "-c", "print(1)"]}
    with pytest.raises(evidence.Refused, match="allowlist"):
        evidence.gate_argv(claim)


def test_gate_allows_git_log():
    """A read-only git verb passes the gate."""
    claim = {"id": "x", "argv": ["git", "log", "-1", "35a8f59"]}
    evidence.gate_argv(claim)  # must not raise


def test_dogfood_lints_clean():
    """The dogfood artifact is valid under the current schema."""
    finding = evidence.load_finding(DOGFOOD)
    assert finding["schema"] == evidence.SCHEMA
    assert finding["agent"] == "glm-5.3"


def test_verify_dogfood_passes(monkeypatch):
    """The dogfood claims reproduce: the finding is not stale."""
    monkeypatch.setattr(preflight, "read_lock", lambda *a, **k: None)
    assert evidence.verify(evidence.load_finding(DOGFOOD), DOGFOOD, False) == 0


def _command_claim(**overrides) -> dict:
    base = {
        "id": "c",
        "statement": "s",
        "argv": ["git", "rev-parse", "--is-inside-work-tree"],
        "cwd": "/Users/evanhoffman/git/local-llm",
        "cwd_repo_rel": ".",
        "readonly": True,
        "cost": "cheap",
        "expect": {"exit": 0, "stdout_equals": "true"},
        "observed": {"exit": 0, "stdout": "true"},
        "expect_authored": "post-hoc",
        "falsifier": "f",
    }
    base.update(overrides)
    return base


def test_load_rejects_claim_with_neither_cwd_rel_nor_machine(tmp_path):
    """An absolute cwd with no portable form and no machine marker is refused.

    This is the CI-red defect: a claim whose cwd is machine-absolute, with
    neither a repo-relative form nor a machine it is bound to, cannot be
    verified on any host but its own and does not say which one that is.
    """
    body = _finding(claims=[_command_claim(cwd_repo_rel=None, machine=None)])
    with pytest.raises(
        evidence.Refused, match="neither cwd_repo_rel nor a machine marker"
    ):
        evidence.load_finding(_write(tmp_path, body))


def test_load_rejects_cwd_rel_escaping_repo(tmp_path):
    """cwd_repo_rel must stay inside the repo -- no absolute, no .."""
    for bad in ("/etc", "../outside"):
        body = _finding(claims=[_command_claim(cwd_repo_rel=bad)])
        with pytest.raises(evidence.Refused, match="repo-relative"):
            evidence.load_finding(_write(tmp_path, body))


def test_verify_portable_claim_runs_on_any_host(monkeypatch):
    """A claim with cwd_repo_rel verifies against the verifier's own checkout."""
    monkeypatch.setattr(preflight, "read_lock", lambda *a, **k: None)
    body = _finding(claims=[_command_claim()])
    assert evidence.verify(body, pathlib.Path("/x/finding.json"), False) == 0


def test_verify_machine_bound_skips_off_host(monkeypatch, caplog):
    """A machine-bound claim skips on a host that is not its own, and says so."""
    monkeypatch.setattr(preflight, "read_lock", lambda *a, **k: None)
    monkeypatch.setattr(evidence, "_current_machine", lambda: "this-host")
    claim = _command_claim(
        cwd_repo_rel=None,
        machine="other-host",
        argv=["cat", "/definitely/not/here"],
    )
    body = _finding(claims=[claim])
    with caplog.at_level("INFO", logger="evidence"):
        assert evidence.verify(body, pathlib.Path("/x/finding.json"), False) == 0
    assert any("machine-bound to other-host" in r.getMessage() for r in caplog.records)
    assert any(
        "0 verified, 1 skipped (machine-bound), 0 failed" in r.getMessage()
        for r in caplog.records
    )


def test_verify_machine_bound_runs_on_own_host(monkeypatch):
    """A machine-bound claim runs on its own host."""
    monkeypatch.setattr(preflight, "read_lock", lambda *a, **k: None)
    monkeypatch.setattr(evidence, "_current_machine", lambda: "this-host")
    claim = _command_claim(cwd_repo_rel=None, machine="this-host")
    body = _finding(claims=[claim])
    assert evidence.verify(body, pathlib.Path("/x/finding.json"), False) == 0
