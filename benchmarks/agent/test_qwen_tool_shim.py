"""The shim must add the format line exactly once, and only where it can matter.

Two failure modes are expensive and silent. Appending on every turn grows the
prompt without bound and moves the KV prefix each time, which would read as the
model getting slower. Appending to a request that offers no tools spends prompt
tokens on an instruction that cannot apply. Both are asserted here.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

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


# ---------------------------------------------------------------------------
# #112: remedy 2 must be switchable, or it can never be measured.

_LOOP = (
    "<tool_call>\n<tool_call>\nI will read it.\n"
    "<tool_call><function=read><parameter=filePath>/tmp/a.py</parameter>"
    "</function></tool_call>"
)


def test_the_strip_is_on_by_default(monkeypatch):
    """The shipped behavior does not change because a toggle exists."""
    monkeypatch.delenv("SHIM_NO_STRIP", raising=False)
    payload = _assistant(_LOOP)
    assert shim.translate_response(payload) is True
    assert "<tool_call>" not in payload["choices"][0]["message"]["content"]


def test_no_strip_leaves_the_scaffolding_in_the_content(monkeypatch):
    """The strip-off arm of the A/B. Without it remedy 2 has no control, and
    #112's item 2 cannot be measured at all."""
    monkeypatch.setenv("SHIM_NO_STRIP", "1")
    payload = _assistant(_LOOP)
    assert shim.translate_response(payload) is True
    assert "<tool_call>" in payload["choices"][0]["message"]["content"]


def test_the_call_itself_is_removed_even_with_the_strip_off(monkeypatch):
    """Only remedy 2 is toggled. Leaving raw call XML in content is a
    different defect and is not part of the experiment."""
    monkeypatch.setenv("SHIM_NO_STRIP", "1")
    payload = _assistant(_LOOP)
    assert shim.translate_response(payload) is True
    message = payload["choices"][0]["message"]
    assert "<function=" not in message["content"]
    assert message["tool_calls"]


def test_an_empty_no_strip_is_not_a_toggle(monkeypatch):
    """An unset-looking value must not silently disable a shipped remedy."""
    monkeypatch.setenv("SHIM_NO_STRIP", "")
    payload = _assistant(_LOOP)
    assert shim.translate_response(payload) is True
    assert "<tool_call>" not in payload["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# #112: the arm the shim announces at startup must be the arm it runs. The
# request path and the startup line read one function, so they cannot
# disagree -- an A/B taken under a shim in the wrong mode is void, and nothing
# downstream records which mode produced a row.


def test_the_request_path_reads_the_announced_toggle(monkeypatch) -> None:
    """`strip_toggle_ab.sh` greps the startup line to decide the arm is safe
    to run. That check is worth nothing unless the line and the behavior come
    from the same reader, so this pins that they do."""
    monkeypatch.delenv("SHIM_NO_STRIP", raising=False)
    monkeypatch.setattr(shim, "strip_scaffolding", lambda: False)
    payload = _assistant(_LOOP)
    assert shim.translate_response(payload) is True
    assert "<tool_call>" in payload["choices"][0]["message"]["content"]

    monkeypatch.setenv("SHIM_NO_STRIP", "1")
    monkeypatch.setattr(shim, "strip_scaffolding", lambda: True)
    payload = _assistant(_LOOP)
    assert shim.translate_response(payload) is True
    assert "<tool_call>" not in payload["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# #78: main() writes the arm record the harness reads back. Without it the
# row cannot say which arm produced it, which is the gap that made the 112
# A/B's arms separable only by a hand-kept manifest.


def test_main_writes_the_arm_record(tmp_path, monkeypatch):
    """The record is the whole point: startup writes it, and the pid it names
    is the live one -- that is what lets a stale record read as `unrecorded`
    later. A shim that served without writing it would leave its rows reading
    'no shim fronts this port', a false absence."""
    import shim_strip

    monkeypatch.setattr(sys, "argv", ["ds4_qwen_tool_shim.py"])
    monkeypatch.setattr(shim_strip, "DEFAULT_RECORD_DIR", tmp_path)
    monkeypatch.setenv("SHIM_NO_STRIP", "1")

    class _Server:
        def __init__(self, addr, handler):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(shim.http.server, "ThreadingHTTPServer", _Server)
    assert shim.main() == 0
    record = json.loads((tmp_path / "8101.json").read_text())
    assert record["strip"] is False, "SHIM_NO_STRIP=1 is the strip-off arm"
    assert record["port"] == shim.DEFAULT_PORT
    assert record["pid"] == os.getpid()


def test_main_writes_the_record_after_the_bind(tmp_path, monkeypatch):
    """A shim that fails to bind must leave no record claiming an arm."""
    import shim_strip

    monkeypatch.setattr(sys, "argv", ["ds4_qwen_tool_shim.py"])
    monkeypatch.setattr(shim_strip, "DEFAULT_RECORD_DIR", tmp_path)

    class _Refuses:
        def __init__(self, addr, handler):
            raise OSError("address in use")

    monkeypatch.setattr(shim.http.server, "ThreadingHTTPServer", _Refuses)
    with pytest.raises(OSError):
        shim.main()
    assert not (tmp_path / "8101.json").exists()


# --- SHIM_TEMPERATURE: making an MTP arm actually an MTP arm (#151) ----------


def body_of(**kw):
    payload = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    payload.update(kw)
    return json.dumps(payload).encode()


def test_no_pin_leaves_the_body_byte_identical(monkeypatch):
    """Off by default. Every backend on :8101 must send exactly what it sent
    before this existed, or 262 rows of history stop comparing."""
    monkeypatch.delenv("SHIM_TEMPERATURE", raising=False)
    body = body_of()
    assert shim.rewrite(body) == body


def test_a_pin_reaches_a_request_that_needs_no_instruction(monkeypatch):
    """Most turns in a trial need no instruction. An arm that was greedy only
    on the instructed ones is not an arm, and the early return in `rewrite`
    used to hand the original body straight back."""
    monkeypatch.setenv("SHIM_TEMPERATURE", "0")
    body = body_of()
    assert not shim.needs_instruction(json.loads(body)), "precondition"
    assert json.loads(shim.rewrite(body))["temperature"] == 0


def test_a_temperature_the_client_asked_for_is_never_overridden(monkeypatch):
    """A client stating a regime is data. Overriding it would make the row
    describe a request nobody sent -- the confound this exists to remove."""
    monkeypatch.setenv("SHIM_TEMPERATURE", "0")
    assert json.loads(shim.rewrite(body_of(temperature=0.7)))["temperature"] == 0.7


def test_an_unparseable_pin_is_ignored_rather_than_defaulted(monkeypatch, caplog):
    """A silent 0 from a typo makes an arm greedy that nobody meant to be,
    and nothing downstream would say so."""
    monkeypatch.setenv("SHIM_TEMPERATURE", "zero")
    assert shim.pinned_temperature() is None
    body = body_of()
    assert shim.rewrite(body) == body


def test_an_empty_pin_is_unset_not_zero(monkeypatch):
    """`SHIM_TEMPERATURE=` in a shell script is the commonest way to mean
    "leave it alone", and reading it as 0.0 would silently make the arm
    greedy."""
    monkeypatch.setenv("SHIM_TEMPERATURE", "")
    assert shim.pinned_temperature() is None


def test_a_nonzero_pin_is_allowed(monkeypatch):
    """The knob is a temperature, not a greedy switch. A 0.2 arm is a
    legitimate thing to ask for, and refusing it here would be this file
    deciding the experiment."""
    monkeypatch.setenv("SHIM_TEMPERATURE", "0.2")
    assert json.loads(shim.rewrite(body_of()))["temperature"] == 0.2
