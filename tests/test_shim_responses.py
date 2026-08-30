"""Tests for the Responses-API fix in the shim.

Codex 0.150.1 sends both a top-level `instructions` string and a
`role="developer"` item inside `input`. llama-server maps each to a system
message, so the Qwen chat template sees [system, system, user, ...] and raises
"System message must be at the beginning". Codex 0.148 did not do this, so the
direct path worked until the client was upgraded.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ollama_claude_shim as shim


def _body(**kw) -> bytes:
    return json.dumps(kw).encode()


def test_a_developer_item_is_folded_into_instructions():
    got = json.loads(
        shim.fold_developer(
            _body(
                instructions="You are a coding agent.",
                input=[
                    {"role": "developer", "type": "message", "content": "Repo rules."},
                    {"role": "user", "type": "message", "content": "Fix it."},
                ],
            )
        )
    )
    assert got["input"] == [{"role": "user", "type": "message", "content": "Fix it."}]
    assert "You are a coding agent." in got["instructions"]
    assert "Repo rules." in got["instructions"]


def test_instructions_come_first_so_the_agent_prompt_still_leads():
    got = json.loads(
        shim.fold_developer(
            _body(
                instructions="FIRST", input=[{"role": "developer", "content": "SECOND"}]
            )
        )
    )
    assert got["instructions"].index("FIRST") < got["instructions"].index("SECOND")


def test_a_system_role_is_folded_too():
    """Older and other clients say "system" where Codex says "developer"."""
    got = json.loads(
        shim.fold_developer(
            _body(
                instructions="A",
                input=[
                    {"role": "system", "content": "B"},
                    {"role": "user", "content": "C"},
                ],
            )
        )
    )
    assert len(got["input"]) == 1
    assert "B" in got["instructions"]


def test_a_request_with_no_developer_item_is_untouched():
    body = _body(instructions="A", input=[{"role": "user", "content": "B"}])
    assert shim.fold_developer(body) == body


def test_a_chat_completions_body_is_untouched():
    """`messages` is the other API's shape and hoist_system owns it."""
    body = _body(messages=[{"role": "system", "content": "A"}])
    assert shim.fold_developer(body) == body


def test_missing_instructions_is_created_rather_than_dropped():
    got = json.loads(
        shim.fold_developer(_body(input=[{"role": "developer", "content": "ONLY"}]))
    )
    assert got["instructions"] == "ONLY"
    assert got["input"] == []


def test_structured_content_blocks_are_flattened_not_stringified():
    """Codex may send content as a list of typed blocks rather than a string."""
    got = json.loads(
        shim.fold_developer(
            _body(
                instructions="A",
                input=[
                    {
                        "role": "developer",
                        "content": [
                            {"type": "input_text", "text": "B"},
                            {"type": "input_text", "text": "C"},
                        ],
                    }
                ],
            )
        )
    )
    assert "B" in got["instructions"] and "C" in got["instructions"]
    assert "input_text" not in got["instructions"]


def test_malformed_json_passes_through_rather_than_raising():
    assert shim.fold_developer(b"not json") == b"not json"
