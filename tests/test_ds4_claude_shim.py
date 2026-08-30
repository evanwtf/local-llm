"""Tests for the ds4 Claude Code shim (#50).

This rewrites request bodies in flight. A silent mistake here does not crash --
it changes what the model is asked, and every downstream number moves with it.
So the rewrites are tested before they are trusted.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ds4_claude_shim as shim


def _rewrite(payload: dict) -> dict:
    return json.loads(shim.rewrite(json.dumps(payload).encode()))


def test_adaptive_thinking_becomes_disabled() -> None:
    out = _rewrite({"thinking": {"type": "adaptive"}, "messages": []})
    assert out["thinking"] == {"type": "disabled"}


def test_explicit_client_choices_are_left_alone() -> None:
    """Only `adaptive` is ours to reinterpret.

    `disabled` and `enabled` are deliberate client choices; rewriting them
    would silently override the caller.
    """
    for kind in ("disabled", "enabled"):
        out = _rewrite({"thinking": {"type": kind}, "messages": []})
        assert out["thinking"]["type"] == kind


def test_absent_thinking_stays_absent() -> None:
    assert "thinking" not in _rewrite({"messages": []})


def test_token_counter_is_pinned_in_a_string_message() -> None:
    out = _rewrite(
        {
            "messages": [
                {
                    "role": "system",
                    "content": "<total_tokens>14969546 tokens left</total_tokens>",
                }
            ]
        }
    )
    assert out["messages"][0]["content"] == shim.COUNTER_PINNED


def test_token_counter_is_pinned_in_a_content_block() -> None:
    """The form Claude Code actually sends, carrying cache_control."""
    out = _rewrite(
        {
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": "<total_tokens>14972686 tokens left</total_tokens>",
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            ]
        }
    )
    block = out["messages"][0]["content"][0]
    assert block["text"] == shim.COUNTER_PINNED
    assert block["cache_control"] == {"type": "ephemeral"}, (
        "must not drop cache_control"
    )


def test_two_different_counters_pin_to_the_same_text() -> None:
    """The whole point: consecutive turns must render an identical prefix."""
    a = _rewrite(
        {
            "messages": [
                {
                    "role": "system",
                    "content": "<total_tokens>15000000 tokens left</total_tokens>",
                }
            ]
        }
    )
    b = _rewrite(
        {
            "messages": [
                {
                    "role": "system",
                    "content": "<total_tokens>14969546 tokens left</total_tokens>",
                }
            ]
        }
    )
    assert a == b


def test_surrounding_text_is_preserved() -> None:
    out = _rewrite(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "before <total_tokens>123 tokens left</total_tokens> after",
                }
            ]
        }
    )
    assert out["messages"][0]["content"] == f"before {shim.COUNTER_PINNED} after"


def test_comma_formatted_counter_is_matched() -> None:
    out = _rewrite(
        {
            "messages": [
                {
                    "role": "system",
                    "content": "<total_tokens>14,969,546 tokens left</total_tokens>",
                }
            ]
        }
    )
    assert out["messages"][0]["content"] == shim.COUNTER_PINNED


def test_non_json_body_passes_through_untouched() -> None:
    assert shim.rewrite(b"not json") == b"not json"


def test_unrelated_body_is_returned_byte_identical() -> None:
    """No rewrite means no re-serialisation: key order must not shift."""
    body = b'{"messages":[{"role":"user","content":"hi"}],"model":"glm-5.3-flash"}'
    assert shim.rewrite(body) == body
