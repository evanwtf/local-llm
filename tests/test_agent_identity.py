"""Tests for the agent identity module (#160 amendment 1 & 2).

The identity is the provenance on every log line and in every finding. The
wrong answer here is a finding that looks attributed but is not -- which is
worse than a blank, because a blank prompts someone to go and find out. So the
module must refuse to guess, and must say `unidentified` loudly when the
environment does not say who the agent is.
"""

from __future__ import annotations

import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib"))

import agent_identity


def test_unset_vars_are_unidentified(monkeypatch):
    """An agent with no identity set must not be guessed at."""
    for var in (
        agent_identity.AGENT_VAR,
        agent_identity.MODEL_VAR,
        agent_identity.EFFORT_VAR,
    ):
        monkeypatch.delenv(var, raising=False)
    assert agent_identity.identity() == ("unidentified", "unidentified", "unidentified")
    assert not agent_identity.is_identified()


def test_set_vars_are_read(monkeypatch):
    """The identity comes from the environment, never from introspection."""
    monkeypatch.setenv(agent_identity.AGENT_VAR, "opus-llama")
    monkeypatch.setenv(agent_identity.MODEL_VAR, "claude-opus-5")
    monkeypatch.setenv(agent_identity.EFFORT_VAR, "high")
    assert agent_identity.identity() == ("opus-llama", "claude-opus-5", "high")
    assert agent_identity.is_identified()


def test_is_identified_requires_all_three(monkeypatch):
    """One missing field is an unidentified session, not a partial one."""
    monkeypatch.setenv(agent_identity.AGENT_VAR, "opus-llama")
    monkeypatch.setenv(agent_identity.MODEL_VAR, "claude-opus-5")
    monkeypatch.delenv(agent_identity.EFFORT_VAR, raising=False)
    assert not agent_identity.is_identified()


def test_log_label_format(monkeypatch):
    """The log field is session/model/effort=X, filterable on each part."""
    monkeypatch.setenv(agent_identity.AGENT_VAR, "opus-llama")
    monkeypatch.setenv(agent_identity.MODEL_VAR, "claude-opus-5")
    monkeypatch.setenv(agent_identity.EFFORT_VAR, "high")
    assert agent_identity.log_label() == "opus-llama/claude-opus-5/effort=high"


def test_filter_injects_agent_into_every_record(monkeypatch):
    """A filter, not a parameter: no call site passes the identity."""
    monkeypatch.setenv(agent_identity.AGENT_VAR, "opus-llama")
    monkeypatch.setenv(agent_identity.MODEL_VAR, "claude-opus-5")
    monkeypatch.setenv(agent_identity.EFFORT_VAR, "high")
    record = logging.LogRecord("m", logging.INFO, "f", 1, "msg", (), None)
    assert agent_identity.AgentFilter().filter(record)
    assert record.agent == "opus-llama/claude-opus-5/effort=high"


def test_install_warns_once_when_unidentified(monkeypatch, caplog):
    """First use warns loudly; the label stays unidentified on every line."""
    monkeypatch.delenv(agent_identity.AGENT_VAR, raising=False)
    monkeypatch.delenv(agent_identity.MODEL_VAR, raising=False)
    monkeypatch.delenv(agent_identity.EFFORT_VAR, raising=False)
    logger = logging.getLogger("test_identity_warn")
    logger.handlers.clear()
    with caplog.at_level(logging.WARNING, logger="test_identity_warn"):
        agent_identity.install(logger)
        agent_identity.install(logger)  # second call must not warn again
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "the unidentified warning must fire once, not per line"
    assert "unidentified" in warnings[0].getMessage()
