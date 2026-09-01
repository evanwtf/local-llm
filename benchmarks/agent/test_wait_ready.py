"""Tests for the readiness poller.

The bug it exists to prevent: on 2026-08-31 a llama.cpp server answered
`/health` with `{"status":"ok"}` and HTTP 200 while every completion returned
503, because an 84 GB model was still loading. A batch started on that signal
failed its smoke gate three times in the same second.
"""

from __future__ import annotations

import urllib.error

import wait_ready


def test_returns_true_as_soon_as_a_completion_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(wait_ready, "serves", lambda *a, **k: (True, "ok"))
    assert wait_ready.ready("http://x", "m", sleep=lambda _: None)


def test_a_healthy_health_endpoint_is_not_enough(monkeypatch) -> None:
    """The whole point: /health saying ok does not mean it can serve.

    If this ever passes on health alone, the poller has regressed into the
    thing that broke.
    """
    monkeypatch.setattr(wait_ready, "health", lambda *a, **k: '200 {"status":"ok"}')
    monkeypatch.setattr(wait_ready, "serves", lambda *a, **k: (False, "HTTP 503"))
    ticks: list[float] = []
    clock = iter([0, 1, 2, 3, 4, 5, 999])
    assert not wait_ready.ready(
        "http://x",
        "m",
        timeout=5,
        interval=1,
        sleep=ticks.append,
        now=lambda: next(clock),
    )
    assert ticks, "should have polled at least once before giving up"


def test_gives_up_at_the_timeout(monkeypatch) -> None:
    monkeypatch.setattr(wait_ready, "serves", lambda *a, **k: (False, "HTTP 503"))
    monkeypatch.setattr(wait_ready, "health", lambda *a, **k: "503")
    clock = iter([0, 10, 20, 30, 40])
    assert not wait_ready.ready(
        "http://x",
        "m",
        timeout=15,
        interval=5,
        sleep=lambda _: None,
        now=lambda: next(clock),
    )


def test_becomes_ready_partway_through(monkeypatch) -> None:
    answers = iter([(False, "HTTP 503"), (False, "HTTP 503"), (True, "ok")])
    monkeypatch.setattr(wait_ready, "serves", lambda *a, **k: next(answers))
    monkeypatch.setattr(wait_ready, "health", lambda *a, **k: "503")
    assert wait_ready.ready(
        "http://x", "m", timeout=60, interval=1, sleep=lambda _: None
    )


def test_a_refused_connection_is_just_not_ready_yet(monkeypatch) -> None:
    """A server not yet started must not raise out of the poller."""

    def boom(*a, **k):
        raise ConnectionRefusedError("nothing listening")

    monkeypatch.setattr(wait_ready, "urllib", wait_ready.urllib)
    monkeypatch.setattr(wait_ready.urllib.request, "urlopen", boom)
    ok, detail = wait_ready.serves("http://x", "m", "t")
    assert not ok and "ConnectionRefused" in detail


def test_an_http_error_is_reported_with_its_code(monkeypatch) -> None:
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(wait_ready.urllib.request, "urlopen", boom)
    ok, detail = wait_ready.serves("http://x", "m", "t")
    assert not ok and detail == "HTTP 503"


def test_health_never_raises(monkeypatch) -> None:
    """Advisory probe: it must degrade to a string, never break the poll."""

    def boom(*a, **k):
        raise OSError("network gone")

    monkeypatch.setattr(wait_ready.urllib.request, "urlopen", boom)
    assert isinstance(wait_ready.health("http://x"), str)
