"""Tests for the #69 config check.

An undeclared `provider/model` makes `opencode run` exit in 0.6s with an empty
stderr_tail, and the harness records a model failure. Six such rows became
GLM-5.3's entire published OpenCode record.
"""

from __future__ import annotations

import json

import opencode_config


def write(tmp_path, providers):
    p = tmp_path / "opencode.json"
    p.write_text(json.dumps({"provider": providers}))
    return p


def test_declared_models_flattens_provider_and_model(tmp_path) -> None:
    cfg = write(tmp_path, {"ds4": {"models": {"a": {}, "b": {}}}})
    assert opencode_config.declared_models(cfg) == {"ds4/a", "ds4/b"}


def test_an_undeclared_model_is_reported(tmp_path) -> None:
    cfg = write(tmp_path, {"ds4": {"models": {"deepseek-v4-flash": {}}}})
    backends = {"glm53ds4": {"opencode_model": "ds4/glm-5.3-flash"}}
    assert opencode_config.missing(backends, cfg) == ["glm53ds4 -> ds4/glm-5.3-flash"]


def test_a_declared_model_is_not_reported(tmp_path) -> None:
    cfg = write(tmp_path, {"ds4": {"models": {"glm-5.3-flash": {}}}})
    backends = {"glm53ds4": {"opencode_model": "ds4/glm-5.3-flash"}}
    assert opencode_config.missing(backends, cfg) == []


def test_a_backend_without_an_opencode_model_is_ignored(tmp_path) -> None:
    cfg = write(tmp_path, {"ds4": {"models": {}}})
    assert opencode_config.missing({"x": {"model": "y"}}, cfg) == []


def test_an_unreadable_config_reports_nothing_missing(tmp_path) -> None:
    """Cannot-tell is not the same as nothing-declared. Reporting every model
    as missing because the file moved would be a false alarm that trains people
    to ignore the check."""
    assert opencode_config.declared_models(tmp_path / "absent.json") is None
    backends = {"glm53ds4": {"opencode_model": "ds4/glm-5.3-flash"}}
    assert opencode_config.missing(backends, tmp_path / "absent.json") == []


def test_malformed_json_is_treated_as_unreadable(tmp_path) -> None:
    p = tmp_path / "opencode.json"
    p.write_text("{not json")
    assert opencode_config.declared_models(p) is None


def test_a_provider_with_no_models_key_does_not_crash(tmp_path) -> None:
    cfg = write(tmp_path, {"ds4": {}})
    assert opencode_config.declared_models(cfg) == set()


def test_the_tracked_reference_config_is_valid_and_declares_providers() -> None:
    """config/opencode.json is the copy a reader can actually review."""
    import pathlib

    ref = pathlib.Path(__file__).resolve().parents[2] / "config/opencode.json"
    data = json.loads(ref.read_text())
    assert data["provider"], "reference config declares no providers"
    assert opencode_config.declared_models(ref)


def test_other_machines_backends_are_not_reported_missing(tmp_path):
    """A desktop-tier backend will never run on this Mac.

    Warning that it is undeclared is noise, and noise in the check that exists
    to catch #69 is how a real warning gets skimmed past.
    """
    config = tmp_path / "opencode.json"
    config.write_text('{"provider": {"ollama": {"models": {"here:1b": {}}}}}')
    backends = {
        "local": {"opencode_model": "ollama/here:1b"},
        "elsewhere": {"opencode_model": "ollama/there:1b", "tier": "desktop-3080ti"},
        "gone": {"opencode_model": "ollama/old:1b", "retired": "superseded"},
        "real": {"opencode_model": "ollama/undeclared:1b"},
    }
    got = opencode_config.missing(backends, config)
    assert got == ["real -> ollama/undeclared:1b"]
