"""The shim must add the format line exactly once, and only where it can matter.

Two failure modes are expensive and silent. Appending on every turn grows the
prompt without bound and moves the KV prefix each time, which would read as the
model getting slower. Appending to a request that offers no tools spends prompt
tokens on an instruction that cannot apply. Both are asserted here.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import ds4_qwen_tool_shim as shim

TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {}}}]


def rewrite(payload: dict) -> dict:
    return json.loads(shim.rewrite(json.dumps(payload).encode()))


def test_a_request_without_tools_is_untouched() -> None:
    """No tools means no tool call, so the instruction is pure prompt tax."""
    body = {"messages": [{"role": "system", "content": "hi"}]}
    assert rewrite(body) == body


def test_the_instruction_is_appended_to_the_system_message() -> None:
    out = rewrite({"messages": [{"role": "system", "content": "hi"}], "tools": TOOLS})
    assert out["messages"][0]["content"].startswith("hi")
    assert "Never use XML-style" in out["messages"][0]["content"]
    assert len(out["messages"]) == 1, "must not insert a second system message"


def test_it_is_idempotent() -> None:
    """A long conversation replays the system message every turn."""
    once = rewrite({"messages": [{"role": "system", "content": "hi"}], "tools": TOOLS})
    twice = rewrite(once)
    assert once == twice
    assert twice["messages"][0]["content"].count("Never use XML-style") == 1


def test_a_request_with_no_system_message_gets_one() -> None:
    out = rewrite({"messages": [{"role": "user", "content": "go"}], "tools": TOOLS})
    assert out["messages"][0]["role"] == "system"
    assert "Never use XML-style" in out["messages"][0]["content"]
    assert out["messages"][1]["role"] == "user"


def test_anthropic_style_content_blocks() -> None:
    out = rewrite(
        {
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": "hi"}]}
            ],
            "tools": TOOLS,
        }
    )
    assert "Never use XML-style" in out["messages"][0]["content"][0]["text"]


def test_a_non_json_body_passes_through_unchanged() -> None:
    """The shim sits in front of every request; it must never corrupt one."""
    assert shim.rewrite(b"not json at all") == b"not json at all"


def test_the_instruction_states_the_json_shape() -> None:
    """It must name the shape ds4 parses.

    An earlier version of this test asserted the instruction should also name
    the XML dialect it forbids, on the theory that saying both worked better.
    That was written from a guess, not a measurement, and the measurement
    disagrees: against OpenCode's real 26k system prompt the two variants are
    indistinguishable -- 1/6 valid either way, against 0/6 with no instruction
    at all. Naming the forbidden dialect neither helps nor primes. Only the
    positive shape is asserted here.
    """
    assert '"name" and "arguments"' in shim.INSTRUCTION
    assert "<tool_call>" in shim.INSTRUCTION
