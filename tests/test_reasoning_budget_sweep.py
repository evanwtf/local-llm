"""The response classifier in scripts/reasoning_budget_sweep.py (#349).

The sweep's finding is one bit per arm: did the model return empty content? These
check that bit is read correctly, and that reasoning is reported from the engine's
own counter when present and a proxy otherwise, so a starved turn is legible.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import reasoning_budget_sweep as rbs


def _resp(content, reasoning="", reasoning_tokens=None, completion=None, finish="stop"):
    usage = {"completion_tokens": completion}
    if reasoning_tokens is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return {
        "choices": [
            {
                "message": {"content": content, "reasoning_content": reasoning},
                "finish_reason": finish,
            }
        ],
        "usage": usage,
    }


def test_empty_content_after_reasoning_is_flagged():
    # The exact failure: reasoning was spent, content came back empty, and the
    # engine stopped on length -- the budget ran out mid-think.
    c = rbs.classify_response(
        _resp("", reasoning="lots of thinking", reasoning_tokens=32000, finish="length")
    )
    assert c["content_empty"] is True
    assert c["reasoning_tokens"] == 32000
    assert c["finish_reason"] == "length"


def test_nonempty_content_is_not_flagged_empty():
    c = rbs.classify_response(_resp("$0.05", reasoning_tokens=120))
    assert c["content_empty"] is False
    assert c["content_chars"] == len("$0.05")


def test_whitespace_only_content_counts_as_empty():
    # A turn that returns only whitespace produced no answer.
    c = rbs.classify_response(_resp("   \n  ", reasoning_tokens=5000))
    assert c["content_empty"] is True


def test_reasoning_tokens_fall_back_to_char_length_when_engine_omits_them():
    # No completion_tokens_details: the proxy is the reasoning_content length, so
    # the arm is still interpretable rather than silently zero.
    c = rbs.classify_response(_resp("", reasoning="abcde"))
    assert c["reasoning_tokens"] == 5


def test_missing_choices_does_not_crash():
    c = rbs.classify_response({})
    assert c["content_empty"] is True
    assert c["reasoning_tokens"] == 0
