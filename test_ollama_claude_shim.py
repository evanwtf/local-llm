"""Tests for the system-message hoist.

The proxy must not change any request that Ollama already accepts, and must
rewrite exactly the shape Claude Code sends that Ollama rejects.
"""
import json

from ollama_claude_shim import hoist_system


def rewrite(payload):
    return json.loads(hoist_system(json.dumps(payload).encode()))


def unchanged(payload):
    """The body is returned byte-identical when there is nothing to hoist."""
    body = json.dumps(payload).encode()
    return hoist_system(body) == body


# --- requests that must pass through untouched --------------------------


def test_no_system_message_is_untouched():
    assert unchanged({"messages": [{"role": "user", "content": "hi"}]})


def test_string_system_is_untouched():
    assert unchanged(
        {"system": "You are terse.", "messages": [{"role": "user", "content": "hi"}]}
    )


def test_block_system_is_untouched():
    assert unchanged(
        {
            "system": [{"type": "text", "text": "You are terse."}],
            "messages": [{"role": "user", "content": "hi"}],
        }
    )


def test_tool_round_trip_is_untouched():
    assert unchanged(
        {
            "system": "x",
            "messages": [
                {"role": "user", "content": "read a"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "data"}
                    ],
                },
            ],
        }
    )


def test_non_json_body_is_untouched():
    assert hoist_system(b"not json") == b"not json"


def test_messages_not_a_list_is_untouched():
    assert unchanged({"messages": "nonsense"})


# --- the shape Claude Code actually sends -------------------------------


def test_trailing_system_message_is_hoisted():
    """This is the exact failing shape: role=system at index 1."""
    out = rewrite(
        {
            "system": [{"type": "text", "text": "You are Claude Code."}],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                {
                    "role": "system",
                    "content": [{"type": "text", "text": "Available agent types:"}],
                },
            ],
        }
    )
    assert [m["role"] for m in out["messages"]] == ["user"]
    assert [b["text"] for b in out["system"]] == [
        "You are Claude Code.",
        "Available agent types:",
    ]


def test_hoisted_blocks_follow_existing_system():
    """Order matters: the agent listing must stay after the main prompt."""
    out = rewrite(
        {
            "system": [{"type": "text", "text": "first"}],
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "system", "content": [{"type": "text", "text": "second"}]},
            ],
        }
    )
    assert [b["text"] for b in out["system"]] == ["first", "second"]


def test_string_system_is_promoted_to_blocks():
    out = rewrite(
        {
            "system": "plain string",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "system", "content": [{"type": "text", "text": "extra"}]},
            ],
        }
    )
    assert out["system"] == [
        {"type": "text", "text": "plain string"},
        {"type": "text", "text": "extra"},
    ]


def test_absent_system_field_is_created():
    out = rewrite(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "system", "content": [{"type": "text", "text": "extra"}]},
            ]
        }
    )
    assert out["system"] == [{"type": "text", "text": "extra"}]


def test_string_content_system_message_is_hoisted():
    out = rewrite(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "system", "content": "be terse"},
            ]
        }
    )
    assert out["system"] == [{"type": "text", "text": "be terse"}]
    assert out["messages"] == [{"role": "user", "content": "hi"}]


def test_cache_control_is_stripped_from_hoisted_blocks():
    """The block moves, so a cache breakpoint on it is meaningless."""
    out = rewrite(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": "extra",
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                },
            ]
        }
    )
    assert out["system"] == [{"type": "text", "text": "extra"}]


def test_non_text_blocks_in_system_message_are_dropped():
    out = rewrite(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "keep"},
                        {"type": "image", "source": {}},
                    ],
                },
            ]
        }
    )
    assert out["system"] == [{"type": "text", "text": "keep"}]


def test_multiple_system_messages_are_all_hoisted():
    out = rewrite(
        {
            "messages": [
                {"role": "system", "content": "a"},
                {"role": "user", "content": "hi"},
                {"role": "system", "content": "b"},
            ]
        }
    )
    assert [b["text"] for b in out["system"]] == ["a", "b"]
    assert [m["role"] for m in out["messages"]] == ["user"]


def test_other_fields_survive():
    out = rewrite(
        {
            "model": "qwen3.8:27b-mlx",
            "max_tokens": 8,
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "tools": [{"name": "Read"}],
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "system", "content": "extra"},
            ],
        }
    )
    assert out["model"] == "qwen3.8:27b-mlx"
    assert out["max_tokens"] == 8
    assert out["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert out["tools"] == [{"name": "Read"}]
