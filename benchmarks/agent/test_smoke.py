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


def _post_good(base_url, token, model, prompt, timeout):
    for name, text in GOOD.items():
        if name == "reverse" and "reverse_string" in prompt:
            return text, "end_turn"
        if name == "fib" and "fib(" in prompt:
            return text, "end_turn"
        if name == "mergesorted" and "merge_sorted" in prompt:
            return text, "end_turn"
    raise AssertionError("unexpected prompt")


def test_gate_passes_when_every_answer_is_correct() -> None:
    rows = smoke.gate(BACKEND, "ok", post=_post_good)
    assert len(rows) == 3
    assert all(r["correct"] for r in rows)


def test_gate_refuses_one_wrong_answer() -> None:
    """The 2026-08-31 case: the server answers, and the answer is wrong."""

    def post(base_url, token, model, prompt, timeout):
        if "fib(" in prompt:
            # fib(10) -> 10, not 55
            return "```python\ndef fib(n):\n    return n\n```", "end_turn"
        return _post_good(base_url, token, model, prompt, timeout)

    with pytest.raises(smoke.SmokeFailure, match="fib"):
        smoke.gate(BACKEND, "degraded", post=post)


def test_gate_refuses_when_the_model_writes_nothing() -> None:
    """An empty reply is the failure that looked like a hard task all morning."""

    def post(base_url, token, model, prompt, timeout):
        return "I have already implemented that for you.", "end_turn"

    with pytest.raises(smoke.SmokeFailure) as excinfo:
        smoke.gate(BACKEND, "empty", post=post)
    assert "reverse" in str(excinfo.value)


def test_gate_refuses_a_refused_connection() -> None:
    """A dead server must fail the gate, not raise a traceback out of it."""

    def post(base_url, token, model, prompt, timeout):
        raise ConnectionRefusedError("nothing listening")

    with pytest.raises(smoke.SmokeFailure):
        smoke.gate(BACKEND, "down", post=post)


def test_over_deadline_warns_and_does_not_refuse() -> None:
    """Superseded contract, kept as a test so the change is deliberate.

    This asserted that a task over the deadline refuses the batch. It no longer
    does: on 2026-08-31 that rule blocked qwen3.6-coding, which is 24/24 on real
    tasks, for spending 900s on fib(10). The gate refuses a *wrong* answer --
    fast, confident and incorrect, which is what a degraded backend looks like --
    and warns about a slow one, because slowness is what the trials measure.
    """
    import time

    def post(base_url, token, model, prompt, timeout):
        time.sleep(0.05)
        return _post_good(base_url, token, model, prompt, timeout)

    rows = smoke.gate(BACKEND, "slow", deadline=0, post=post)
    assert len(rows) == 3
    assert all(not r["within_deadline"] for r in rows)


def test_hosted_backend_is_skipped_not_failed() -> None:
    """No base_url means the metered API; billing a smoke test every run is wrong."""
    assert smoke.gate({"model": "claude-opus-5"}, "opus5", post=_post_good) == []


def test_extract_code_prefers_the_fenced_block() -> None:
    text = "Here you go:\n```python\ndef f():\n    return 1\n```\nHope that helps!"
    assert "Hope that helps" not in smoke.extract_code(text)
    assert "def f()" in smoke.extract_code(text)


def test_unfenced_code_still_passes() -> None:
    """Ignoring the formatting instruction is not a capability failure."""
    assert smoke.verify(
        "def reverse_string(s):\n    return s[::-1]\n",
        "assert reverse_string('ab') == 'ba'",
    )


def test_verify_rejects_code_that_does_not_parse() -> None:
    assert not smoke.verify("```python\ndef broken(:\n```", "assert True")


def test_all_thinking_no_answer_is_reported_as_a_budget_failure() -> None:
    """The 2026-08-31 false positive: a 24/24 backend refused for our bug.

    `qwen3.6:27b-coding-mxfp8` returned only `thinking` blocks and stopped at
    `max_tokens`. Reading just `text` blocks yielded "", which scored as a wrong
    answer -- so the gate blocked a batch because the model was still thinking.
    The reason must say so, not claim the function was wrong.
    """

    def post(base_url, token, model, prompt, timeout):
        return "", "max_tokens"

    rows = smoke.check(BACKEND, deadline=5, post=post)
    assert all(not r["correct"] for r in rows)
    assert all("thinking" in (r["error"] or "") for r in rows)
    assert all(r["stop_reason"] == "max_tokens" for r in rows)


def test_thinking_blocks_do_not_count_as_answer_text() -> None:
    """A thinking block is reasoning, not the answer -- but code inside one
    must not be mistaken for a solution either."""
    assert not smoke.verify("", "assert True is False")


def test_slow_does_not_refuse_the_batch() -> None:
    """A model still thinking is not a model that got it wrong.

    2026-08-31: qwen3.6-coding, 24/24 on real tasks, was refused because it
    spent 900s on fib(10) while answering reverse in 22.8s and mergesorted
    correctly. Slowness is what the trials measure; the gate must not block on it.
    """

    def post(base_url, token, model, prompt, timeout):
        if "fib(" in prompt:
            return "", "max_tokens"
        return _post_good(base_url, token, model, prompt, timeout)

    rows = smoke.gate(BACKEND, "slow-but-fine", post=post)
    assert len(rows) == 3


def test_a_wrong_answer_still_refuses_even_beside_a_slow_one() -> None:
    """Slowness is forgiven; a confidently wrong answer is not."""

    def post(base_url, token, model, prompt, timeout):
        if "fib(" in prompt:
            return "", "max_tokens"
        if "reverse_string" in prompt:
            return "```python\ndef reverse_string(s):\n    return s\n```", "end_turn"
        return _post_good(base_url, token, model, prompt, timeout)

    with pytest.raises(smoke.SmokeFailure, match="reverse"):
        smoke.gate(BACKEND, "degraded", post=post)


def test_a_timeout_is_slow_not_wrong() -> None:
    def post(base_url, token, model, prompt, timeout):
        if "fib(" in prompt:
            raise TimeoutError("timed out")
        return _post_good(base_url, token, model, prompt, timeout)

    assert len(smoke.gate(BACKEND, "timeout-is-slow", post=post)) == 3


def test_every_prompt_is_self_contained() -> None:
    """A probe must not require world knowledge to answer.

    `fib` originally named the Fibonacci sequence and supplied only the two base
    cases, so a model that did not recall the recurrence had nothing to derive
    it from -- making it a knowledge probe, not a capability one. It cannot then
    distinguish a degraded backend from an ignorant one, which is the gate's
    entire job. qwen3.6-coding spent >900s on it while answering the two
    self-contained tasks correctly.

    This guards the property, not the wording: no prompt may lean on a named
    concept the model has to look up.
    """
    named_concepts = ("fibonacci", "quicksort", "levenshtein", "fizzbuzz", "ackermann")
    for name, prompt, _ in smoke.SMOKE_TASKS:
        low = prompt.lower()
        for concept in named_concepts:
            assert concept not in low, f"{name} leans on the name {concept!r}"


def test_fib_states_its_own_recurrence() -> None:
    """The one task that could not be inferred from its description now can."""
    _, prompt, _ = smoke.SMOKE_TASKS[1]
    assert "fib(n - 1) + fib(n - 2)" in prompt
    assert "fib(0) = 0" in prompt and "fib(1) = 1" in prompt
