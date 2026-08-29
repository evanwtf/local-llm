"""Tests for the harness helpers that record provenance.

`probe_server` runs before every batch and touches the network. It records
what the server is really serving -- including the sampling parameters, whose
absence let #26 blame a KV cache for ordinary sampling spread. It must record
what it can and return {} for everything else: losing provenance is bad, and
losing a half-hour trial to a provenance bug is worse.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest
import run

PROPS = {
    "model_path": "/models/GLM-5.3-Flash-GGUF/UD-Q2_K_XL/GLM-00001-of-00004.gguf",
    "model_alias": "glm-5.3-flash-q2",
    "build_info": "b10677-8a8d0bcc4",
    "total_slots": 1,
    "chat_template": "[gMASK]<sop>" + "x" * 5000,
    "default_generation_settings": {
        "n_ctx": 65536,
        "params": {
            "temperature": 1.0, "top_p": 0.95, "top_k": 40,
            "min_p": 0.05, "seed": 4294967295, "samplers": ["top_k", "temperature"],
            "n_predict": -1,
        },
    },
}


class _Serves:
    """A stand-in for one /props endpoint: set `payload`, read back `url`."""

    def __init__(self):
        self.payload = None
        self.url = None

    def open(self, url, timeout=None):
        self.url = url
        if isinstance(self.payload, Exception):
            raise self.payload
        body = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return _closing(io.StringIO(body))


class _closing:
    def __init__(self, fh): self.fh = fh
    def __enter__(self): return self.fh
    def __exit__(self, *a): return False


@pytest.fixture
def serves(monkeypatch):
    stub = _Serves()
    monkeypatch.setattr(run.urllib.request, "urlopen", stub.open)
    return stub


def test_records_the_sampling_parameters(serves):
    serves.payload = PROPS
    got = run.probe_server({"base_url": "http://127.0.0.1:8030"})
    assert got["sampling"]["temperature"] == 1.0
    assert got["sampling"]["seed"] == 4294967295


def test_records_the_exact_model_file_being_served(serves):
    """The point of the probe: not every GGUF in the directory, the one loaded."""
    serves.payload = PROPS
    got = run.probe_server({"base_url": "http://127.0.0.1:8030"})
    assert got["model_path"].endswith("GLM-00001-of-00004.gguf")
    assert got["build_info"] == "b10677-8a8d0bcc4"


def test_does_not_haul_the_chat_template_into_every_row(serves):
    """/props carries a multi-kilobyte template. It must not land in the row."""
    serves.payload = PROPS
    got = run.probe_server({"base_url": "http://127.0.0.1:8030"})
    assert "chat_template" not in got
    assert len(json.dumps(got)) < 1000


def test_props_url_overrides_base_url_for_a_shimmed_backend(serves):
    serves.payload = PROPS
    run.probe_server({"base_url": "http://127.0.0.1:11501",
                      "props_url": "http://127.0.0.1:8030"})
    assert serves.url == "http://127.0.0.1:8030/props"


def test_a_trailing_slash_does_not_produce_a_double_slash(serves):
    serves.payload = PROPS
    run.probe_server({"base_url": "http://127.0.0.1:8030/"})
    assert serves.url == "http://127.0.0.1:8030/props"


def test_a_hosted_backend_has_no_server_to_probe():
    assert run.probe_server({}) == {}


def test_a_refused_connection_is_not_an_error(serves):
    serves.payload = urllib.error.URLError("Connection refused")
    assert run.probe_server({"base_url": "http://127.0.0.1:9999"}) == {}


def test_a_timeout_is_not_an_error(serves):
    serves.payload = TimeoutError("timed out")
    assert run.probe_server({"base_url": "http://127.0.0.1:8030"}) == {}


def test_a_server_that_answers_with_junk_is_not_an_error(serves):
    """Ollama answers /props with a 404 page, not JSON."""
    serves.payload = "<html>404</html>"
    assert run.probe_server({"base_url": "http://127.0.0.1:11434"}) == {}


def test_a_server_with_no_sampling_block_still_records_what_it_has(serves):
    serves.payload = {"model_path": "/x.gguf", "build_info": "b1"}
    got = run.probe_server({"base_url": "http://127.0.0.1:8000"})
    assert got == {"model_path": "/x.gguf", "build_info": "b1"}


# --- direction 1: more than one symbol per task ---------------------------

def test_a_single_symbol_task_still_describes_itself_the_old_way():
    """398 rows name tasks defined with file/symbol. Do not break them."""
    task = {"name": "t", "file": "src/a.py", "symbol": "f", "tests": ["tests/"]}
    assert run.targets(task) == [{"file": "src/a.py", "symbol": "f"}]


def test_a_task_may_hollow_out_several_symbols():
    task = {"name": "t", "tests": ["tests/"], "targets": [
        {"file": "src/a.py", "symbol": "f"},
        {"file": "src/b.py", "symbol": "g"},
    ]}
    assert len(run.targets(task)) == 2
    assert {t["file"] for t in run.targets(task)} == {"src/a.py", "src/b.py"}


def test_a_task_with_neither_form_is_refused_loudly():
    """A typo in tasks.toml must not silently produce a task that removes nothing.

    That would excise nothing, pass the control check trivially... no: it would
    make the control check FAIL to fail, and the trial would be recorded with
    `control_fails_as_expected` false. Better to refuse at load.
    """
    with pytest.raises(KeyError):
        run.targets({"name": "t", "tests": ["tests/"]})


def test_targets_are_returned_in_a_stable_order():
    """Excision order decides which file the stub lands in first; keep it fixed."""
    task = {"name": "t", "tests": [], "targets": [
        {"file": "src/z.py", "symbol": "z"},
        {"file": "src/a.py", "symbol": "a"},
    ]}
    assert [t["symbol"] for t in run.targets(task)] == ["z", "a"]


# --- the environment handed to the client --------------------------------

def test_codex_gets_its_api_key_from_the_backend():
    """Codex profiles declare `env_key = "CODEX_API_KEY"` and the harness must
    supply it.

    It did not, for the life of the project. Every Codex row until 2026-08-28
    was produced with the variable exported in the operator's interactive
    shell, so the harness silently depended on ambient state it neither set nor
    recorded. Unattended, Codex exits in 0.7 s with "Missing environment
    variable: CODEX_API_KEY" and the row lands as a model failure, which is
    what it looks like and is not what it is.
    """
    env = run.agent_env({"base_url": "http://127.0.0.1:8000", "model": "m",
                         "auth_token": "tok", "context_tokens": 1})
    assert env["CODEX_API_KEY"] == "tok"


def test_the_hosted_reference_keeps_its_ambient_auth():
    """No base_url means the operator's real login. Do not inject a token."""
    env = run.agent_env({"model": "claude-opus-5"})
    assert "CODEX_API_KEY" not in env or env.get("CODEX_API_KEY") != "tok"
    assert env["ANTHROPIC_MODEL"] == "claude-opus-5"


def test_a_local_backend_never_leaks_a_real_anthropic_key():
    env = run.agent_env({"base_url": "http://127.0.0.1:8000", "model": "m",
                         "auth_token": "tok", "context_tokens": 1})
    assert "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_AUTH_TOKEN"] == "tok"


# --- recording the sampler on every backend (#36) -------------------------

def test_ollama_modelfile_parameters_are_recorded():
    """A modelfile that sets the sampler is the one case Ollama tells us about."""
    show = {"modelfile": "FROM x\nPARAMETER temperature 0.6\nPARAMETER top_p 0.95\n"}
    got = run.parse_ollama_show(show)
    assert got["sampling"] == {"temperature": "0.6", "top_p": "0.95"}
    assert got["sampling_source"] == "modelfile"


def test_an_empty_modelfile_records_that_defaults_apply_rather_than_nothing():
    """The case that caused #28 and #36.

    `ornith-1.5:35b` sets no PARAMETER lines, so Ollama's built-in defaults
    applied -- top_p 0.9, repeat_penalty 1.1 -- while llamacpp-up used 0.95 and
    never set repeat_penalty. Recording silence as silence is what made the
    confound invisible. An explicit "engine defaults, unrecorded" is a warning;
    a missing key is not.
    """
    got = run.parse_ollama_show({"modelfile": "FROM x\nTEMPLATE y\n"})
    assert got["sampling"] == {}
    assert got["sampling_source"] == "engine defaults (unrecorded)"


def test_a_malformed_parameter_line_is_skipped_not_guessed():
    got = run.parse_ollama_show({"modelfile": "PARAMETER\nPARAMETER top_k 40\n"})
    assert got["sampling"] == {"top_k": "40"}


def test_a_show_response_without_a_modelfile_yields_nothing():
    assert run.parse_ollama_show({}) == {}
    assert run.parse_ollama_show({"modelfile": ""}) == {}


def test_ds4_records_that_its_sampler_is_unreported_not_that_it_is_absent():
    """ds4 exposes which parameters it accepts, never the values in force.

    `/v1/models` lists `supported_parameters`; there is no endpoint for the
    effective sampler, and the source carries two conflicting sets -- ds4.h has
    TOP_P 1.0 / MIN_P 0.05, ds4_cli.c overrides to 0.95 / 0.0 for CLI and agent
    paths. Which applies to the server cannot be settled by reading it.

    So the row records the ambiguity rather than a guess. #28 and #36 both came
    from a sampler nobody wrote down; a confident wrong value would be worse
    than an explicit unknown.
    """
    models = {"data": [{"id": "deepseek-v4-flash", "context_length": 100000,
                        "supported_parameters": ["temperature", "top_p",
                                                 "top_k", "min_p", "seed"]}]}
    got = run.parse_ds4_models(models)
    assert got["sampling"] == {}
    assert got["sampling_source"] == "engine defaults (not reported by ds4)"
    assert set(got["accepts_sampling"]) == {"temperature", "top_p", "top_k",
                                            "min_p", "seed"}
    assert got["context_length"] == 100000


def test_a_models_response_from_something_else_is_ignored():
    assert run.parse_ds4_models({"data": [{"id": "gpt-4"}]}) == {}
    assert run.parse_ds4_models({}) == {}
