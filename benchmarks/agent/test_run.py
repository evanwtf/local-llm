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
