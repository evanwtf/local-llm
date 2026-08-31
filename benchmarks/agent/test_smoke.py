"""Tests for the pre-batch smoke gate (#63).

Weighted towards refusal. This guard runs unattended before every batch, and
the only way it earns its place is by stopping a run that would have produced
meaningless rows -- so the cases that matter are the ones where it must say no.
"""

from __future__ import annotations

import pytest

import smoke

GOOD = {
    "reverse": "```python\ndef reverse_string(s):\n    return s[::-1]\n```",
    "fib": (
        "```python\ndef fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n"
        "        a, b = b, a + b\n    return a\n```"
    ),
    "mergesorted": (
        "```python\ndef merge_sorted(a, b):\n    return sorted(a + b)\n```"
    ),
}

BACKEND = {"base_url": "http://127.0.0.1:8000", "auth_token": "t", "model": "m"}


def _post_good(base_url, token, model, prompt, timeout):  # noqa: ARG001
    for name, text in GOOD.items():
        if name == "reverse" and "reverse_string" in prompt:
            return text
        if name == "fib" and "Fibonacci" in prompt:
            return text
        if name == "mergesorted" and "merge_sorted" in prompt:
            return text
    raise AssertionError("unexpected prompt")


def test_gate_passes_when_every_answer_is_correct() -> None:
    rows = smoke.gate(BACKEND, "ok", post=_post_good)
    assert len(rows) == 3
    assert all(r["correct"] for r in rows)


def test_gate_refuses_one_wrong_answer() -> None:
    """The 2026-08-31 case: the server answers, and the answer is wrong."""

    def post(base_url, token, model, prompt, timeout):  # noqa: ARG001
        if "Fibonacci" in prompt:
            return "```python\ndef fib(n):\n    return n\n```"   # fib(10) -> 10, not 55
        return _post_good(base_url, token, model, prompt, timeout)

    with pytest.raises(smoke.SmokeFailure, match="fib"):
        smoke.gate(BACKEND, "degraded", post=post)


def test_gate_refuses_when_the_model_writes_nothing() -> None:
    """An empty reply is the failure that looked like a hard task all morning."""

    def post(base_url, token, model, prompt, timeout):  # noqa: ARG001
        return "I have already implemented that for you."

    with pytest.raises(smoke.SmokeFailure) as excinfo:
        smoke.gate(BACKEND, "empty", post=post)
    assert "reverse" in str(excinfo.value)


def test_gate_refuses_a_refused_connection() -> None:
    """A dead server must fail the gate, not raise a traceback out of it."""

    def post(base_url, token, model, prompt, timeout):  # noqa: ARG001
        raise ConnectionRefusedError("nothing listening")

    with pytest.raises(smoke.SmokeFailure):
        smoke.gate(BACKEND, "down", post=post)


def test_gate_refuses_a_task_over_the_deadline() -> None:
    """A wedged or looping server is caught by the clock, not by correctness."""
    import time

    def post(base_url, token, model, prompt, timeout):  # noqa: ARG001
        time.sleep(0.05)
        return _post_good(base_url, token, model, prompt, timeout)

    with pytest.raises(smoke.SmokeFailure, match="over 0s"):
        smoke.gate(BACKEND, "slow", deadline=0, post=post)


def test_hosted_backend_is_skipped_not_failed() -> None:
    """No base_url means the metered API; billing a smoke test every run is wrong."""
    assert smoke.gate({"model": "claude-opus-5"}, "opus5", post=_post_good) == []


def test_extract_code_prefers_the_fenced_block() -> None:
    text = "Here you go:\n```python\ndef f():\n    return 1\n```\nHope that helps!"
    assert "Hope that helps" not in smoke.extract_code(text)
    assert "def f()" in smoke.extract_code(text)


def test_unfenced_code_still_passes() -> None:
    """Ignoring the formatting instruction is not a capability failure."""
    assert smoke.verify("def reverse_string(s):\n    return s[::-1]\n",
                        "assert reverse_string('ab') == 'ba'")


def test_verify_rejects_code_that_does_not_parse() -> None:
    assert not smoke.verify("```python\ndef broken(:\n```", "assert True")
