"""The remote client's preflight reports each check and fails closed. #562/#579"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import client_preflight as cp


def test_a_failed_check_is_counted_and_printed(capsys):
    rep = cp.Report()
    assert rep.check("a", True, "fine") is True
    assert rep.check("b", False, "broken") is False
    out = capsys.readouterr().out
    assert "PASS  a: fine" in out and "FAIL  b: broken" in out
    assert rep.failed == 1


def test_an_unknown_backend_fails_closed(monkeypatch, capsys):
    monkeypatch.delenv("LOCAL_LLM_SERVER_HOST", raising=False)
    monkeypatch.delenv("LOCAL_LLM_SERVER_FACTS", raising=False)
    assert cp.main(["--backend", "no-such-backend"]) == 1
    assert "FAIL  backend" in capsys.readouterr().out


def test_the_backend_must_serve_its_model_id_exactly():
    """A substring match took `qwen3.8-flash-next-q3-nothink` for
    `qwen3.8-flash-next-q3`: the check named a backend it had not verified."""
    body = '{"object": "list", "data": [{"id": "qwen3.8-flash-next-q3-nothink"}]}'
    assert not cp.serves_model(body, "qwen3.8-flash-next-q3")
    assert cp.serves_model(body, "qwen3.8-flash-next-q3-nothink")


def test_a_model_named_outside_the_id_field_is_not_served():
    """vLLM puts the weights path in `root`; a name there is not the id."""
    body = '{"data": [{"id": "glm53", "root": "/models/GLM-5.3-Flash-EXL3"}]}'
    assert not cp.serves_model(body, "GLM-5.3-Flash-EXL3")


def test_a_malformed_models_answer_fails_closed():
    for body in (None, "", "not json", "[]", '{"data": "x"}', '{"data": [1, "m"]}'):
        assert not cp.serves_model(body, "m"), body
