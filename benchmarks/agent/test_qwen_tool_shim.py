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


def test_prefix_block_log_is_off_unless_asked(tmp_path, monkeypatch):
    """An audit aid must not write anything by default (#50)."""
    monkeypatch.delenv("SHIM_PREFIX_LOG", raising=False)
    shim.log_prefix_blocks(b'{"messages":[{"role":"user","content":"hi"}]}')
    assert not list(tmp_path.iterdir())


def test_prefix_block_log_writes_digests_not_prompts(tmp_path, monkeypatch):
    """SHIM_DUMP writes whole payloads; this must not be able to leak one."""
    out = tmp_path / "blocks.jsonl"
    monkeypatch.setenv("SHIM_PREFIX_LOG", str(out))
    body = json.dumps(
        {
            "messages": [
                {
                    "role": "system",
                    "content": "SECRET CLAUDE.md CONTENTS",
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        }
    ).encode()
    shim.log_prefix_blocks(body)
    text = out.read_text()
    assert "SECRET" not in text
    assert "CLAUDE.md" not in text
    row = json.loads(text.splitlines()[0])
    assert row["horizon"] == 0
    assert row["blocks"][0]["cacheable"] is True


def test_a_broken_prefix_log_never_breaks_the_request(tmp_path, monkeypatch):
    """Mid-trial, an audit aid failing must be invisible."""
    monkeypatch.setenv("SHIM_PREFIX_LOG", str(tmp_path / "nope" / "x.jsonl"))
    shim.log_prefix_blocks(b"not json at all")
    shim.log_prefix_blocks(b'{"messages":[]}')


# --- #112 remedy 2: the shim strips its own scaffolding -------------------


def _assistant(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_a_translated_call_leaves_no_raw_xml_in_content():
    """Leaving it would show the user raw XML beside a tool result, and some
    clients echo content back into the next prompt."""
    payload = _assistant(
        "I will read it.\n"
        "<tool_call><function=read><parameter=filePath>/tmp/a.py</parameter>"
        "</function></tool_call>"
    )
    assert shim.translate_response(payload) is True
    content = payload["choices"][0]["message"]["content"]
    assert "<tool_call>" not in content
    assert "<function=" not in content
    assert "I will read it." in content


def test_stacked_bare_opens_are_stripped_from_content():
    """#112's degeneration loop: the model stops calling tools and emits
    stacked bare opens. Echoing them back invites more of them, which is what
    remedy 2 is for -- shipped 2026-09-03 and until now untested."""
    payload = _assistant(
        "<tool_call>\n<tool_call>\n<tool_call>\n"
        "<tool_call><function=read><parameter=filePath>/tmp/a.py</parameter>"
        "</function></tool_call>"
    )
    assert shim.translate_response(payload) is True
    content = payload["choices"][0]["message"]["content"]
    assert "<tool_call>" not in content
    assert content.strip() == ""


def test_prose_around_a_call_survives():
    """Stripping scaffolding must not eat the model's actual words."""
    payload = _assistant(
        "First I check the file.\n"
        "<tool_call><function=read><parameter=filePath>/tmp/a.py</parameter>"
        "</function></tool_call>\n"
        "Then I will patch it."
    )
    shim.translate_response(payload)
    content = payload["choices"][0]["message"]["content"]
    assert "First I check the file." in content
    assert "Then I will patch it." in content


def test_a_response_with_no_call_is_left_alone():
    """A turn that is pure narration -- the #112 failure shape -- must not be
    rewritten, or the row stops showing what the model actually produced."""
    payload = _assistant("The tool call format was invalid. I will try again.")
    assert shim.translate_response(payload) is False
    assert payload["choices"][0]["message"]["content"].startswith("The tool call")


def test_bare_opens_with_no_real_call_are_left_alone():
    """No call means nothing to translate, so the content stays verbatim --
    otherwise the evidence of the loop would be erased from the transcript."""
    payload = _assistant("<tool_call>\n<tool_call>\n<tool_call>\n")
    assert shim.translate_response(payload) is False
    assert "<tool_call>" in payload["choices"][0]["message"]["content"]
