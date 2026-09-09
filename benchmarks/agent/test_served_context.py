"""The served context must match the declared one, or the run refuses.

`context_tokens` is written into every row. Until 2026-09-02 nothing read it
back from the server, and the two disagreed by 32x: Ollama serves a 4096
default unless a Modelfile sets `num_ctx`, while the desktop backends declared
131072 copied from the Mac's entries. `ornith-1.5:9b` then ran a repository
task in a 4096-token window, thrashed for 1566.9s, failed, and wrote a row
stamped `context_tokens: 131072`. The same task at 32768 passed in 93.5s.

Refusing correctly is this check's whole job, so the negative cases carry the
weight here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import preflight  # noqa: E402


BACKEND = {
    "model": "ornith-1.5-9b-32k",
    "base_url": "http://127.0.0.1:11434",
    "context_tokens": 32768,
}


def test_a_smaller_served_context_is_reported(monkeypatch):
    """The 4096-vs-131072 case: the window truncates the task silently."""
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _fake({"models": [{"name": "m", "context_length": 4096}]}),
    )
    gaps = preflight.check_served_context({"b": {**BACKEND, "model": "m"}})
    assert len(gaps) == 1
    assert "4096" in gaps[0] and "32768" in gaps[0]


def test_a_matching_context_is_silent(monkeypatch):
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _fake({"models": [{"name": "m", "context_length": 32768}]}),
    )
    assert preflight.check_served_context({"b": {**BACKEND, "model": "m"}}) == []


def test_a_larger_served_context_is_not_a_failure(monkeypatch):
    """Bigger than asked for cannot truncate anything, so it is not a gap."""
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _fake({"models": [{"name": "m", "context_length": 131072}]}),
    )
    assert preflight.check_served_context({"b": {**BACKEND, "model": "m"}}) == []


def test_an_unreachable_server_is_not_reported_as_a_mismatch(monkeypatch):
    """None means "cannot tell". Reporting it as a gap would block every run
    that starts before the server is warm."""
    import urllib.request

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert preflight.check_served_context({"b": BACKEND}) == []
    assert preflight.served_context("m", "http://127.0.0.1:11434") is None


def test_a_model_that_is_not_resident_is_not_a_mismatch(monkeypatch):
    """An empty /api/ps means nothing is loaded yet, not a zero-length window."""
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake({"models": []}))
    assert preflight.check_served_context({"b": BACKEND}) == []


def test_a_backend_that_declares_nothing_is_skipped(monkeypatch):
    """Not every backend carries context_tokens; absence is not a gap."""
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _fake({"models": [{"name": "m", "context_length": 8}]}),
    )
    assert preflight.check_served_context({"b": {"model": "m", "base_url": "u"}}) == []


def _fake(payload):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    return lambda *a, **k: FakeResponse()
