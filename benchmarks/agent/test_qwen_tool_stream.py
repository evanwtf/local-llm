"""The streaming path is where the tool calls were being lost (issue #94).

ds4's own log says it is returning the failed tool call as assistant text --
`invalid tool call returned as assistant text finish=stop [text_len=231 ...]`
-- and in a non-streaming response that text does arrive. In a **streaming**
response it does not: the client sees no content, no tool_calls, and
finish=stop. Measured on one identical request, interleaved so session drift
hit both arms equally:

    stream:true    tool_calls  1/12    empty      11/12
    stream:false   tool_calls  7/12    text        5/12

OpenCode sets `stream: true`, which is why the 45-trial run scored 0/45 while
the same prompts answered correctly off-stream. The dialect coin-flip is real
and underlies both arms, but it is not what made the agent runs unrecoverable.

So the shim asks upstream for a **non-streaming** completion, translates the
XML dialect if it appears, and synthesises the SSE stream the client asked
for. These tests pin the two pure halves of that: the XML parse and the SSE
synthesis.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import ds4_qwen_tool_shim as shim

TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {}}}]

# Copied verbatim from a ds4 server log on 2026-09-03, including the newlines
# inside the parameter -- the model indents these and a naive parser keeps the
# whitespace, producing a path that does not exist.
XML_FROM_THE_WIRE = """The user is asking me to read the file located at /tmp/x.py.
</think>

<tool_call>
<function=read>
<parameter=filePath>
/tmp/x.py
</parameter>
</function>
</tool_call>"""


def test_it_parses_the_xml_dialect_off_the_wire() -> None:
    calls = shim.parse_xml_tool_calls(XML_FROM_THE_WIRE)
    assert calls is not None
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "read"
    assert json.loads(calls[0]["function"]["arguments"]) == {"filePath": "/tmp/x.py"}


def test_the_parameter_value_is_stripped() -> None:
    """The model puts the value on its own line; the leading newline is not path."""
    calls = shim.parse_xml_tool_calls(XML_FROM_THE_WIRE)
    assert calls is not None
    assert json.loads(calls[0]["function"]["arguments"])["filePath"] == "/tmp/x.py"


def test_a_single_line_call_parses_too() -> None:
    text = (
        "<tool_call><function=read><parameter=filePath>/tmp/a.py"
        "</parameter></function></tool_call>"
    )
    calls = shim.parse_xml_tool_calls(text)
    assert calls is not None
    assert json.loads(calls[0]["function"]["arguments"]) == {"filePath": "/tmp/a.py"}


def test_multiple_parameters_are_all_captured() -> None:
    text = (
        "<tool_call><function=edit>"
        "<parameter=filePath>/tmp/a.py</parameter>"
        "<parameter=oldString>a</parameter>"
        "<parameter=newString>b</parameter>"
        "</function></tool_call>"
    )
    calls = shim.parse_xml_tool_calls(text)
    assert calls is not None
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "filePath": "/tmp/a.py",
        "oldString": "a",
        "newString": "b",
    }


def test_two_calls_in_one_message() -> None:
    text = (
        "<tool_call><function=read><parameter=filePath>/a</parameter>"
        "</function></tool_call>\n"
        "<tool_call><function=read><parameter=filePath>/b</parameter>"
        "</function></tool_call>"
    )
    calls = shim.parse_xml_tool_calls(text)
    assert calls is not None and len(calls) == 2
    assert json.loads(calls[1]["function"]["arguments"]) == {"filePath": "/b"}


def test_ordinary_prose_is_not_a_tool_call() -> None:
    """The translator must not fire on text that merely discusses tools."""
    assert shim.parse_xml_tool_calls("I will call the read function now.") is None
    assert shim.parse_xml_tool_calls("") is None


def test_valid_json_dialect_is_left_alone() -> None:
    """ds4 parses this one itself; if it reached us as text it is not ours to fix.

    Returning None here matters: a message carrying the JSON dialect has
    already been handled upstream, and re-parsing it would risk emitting the
    same call twice.
    """
    text = '<tool_call>{"name": "read", "arguments": {"filePath": "/a"}}</tool_call>'
    assert shim.parse_xml_tool_calls(text) is None


def test_each_call_gets_a_distinct_id() -> None:
    """OpenCode keys tool results by id; two calls sharing one id lose a result."""
    text = (
        "<tool_call><function=read><parameter=filePath>/a</parameter>"
        "</function></tool_call>"
        "<tool_call><function=read><parameter=filePath>/b</parameter>"
        "</function></tool_call>"
    )
    calls = shim.parse_xml_tool_calls(text)
    assert calls is not None
    assert calls[0]["id"] != calls[1]["id"]
    assert calls[0]["index"] == 0 and calls[1]["index"] == 1


# --- SSE synthesis -------------------------------------------------------


def sse_events(chunks: list[bytes]) -> list[dict]:
    """Decode the synthesised stream the way a client would read it."""
    events = []
    for raw in chunks:
        for line in raw.decode().splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                continue
            events.append(json.loads(payload))
    return events


def test_the_synthesised_stream_ends_with_done() -> None:
    body = {
        "id": "x",
        "model": "m",
        "created": 1,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
    }
    chunks = shim.synthesise_sse(body)
    assert chunks[-1] == b"data: [DONE]\n\n"


def test_content_survives_the_round_trip() -> None:
    body = {
        "id": "x",
        "model": "m",
        "created": 1,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello world"},
                "finish_reason": "stop",
            }
        ],
    }
    events = sse_events(shim.synthesise_sse(body))
    text = "".join(e["choices"][0]["delta"].get("content", "") for e in events)
    assert text == "hello world"
    assert events[-1]["choices"][0]["finish_reason"] == "stop"


def test_tool_calls_survive_the_round_trip() -> None:
    """The whole point: a tool call must reach the client as a tool call."""
    body = {
        "id": "x",
        "model": "m",
        "created": 1,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read",
                                "arguments": '{"filePath":"/a"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    events = sse_events(shim.synthesise_sse(body))
    deltas = [e["choices"][0]["delta"] for e in events]
    calls = [d for d in deltas if d.get("tool_calls")]
    assert calls, "no tool_calls delta was emitted"
    call = calls[0]["tool_calls"][0]
    assert call["function"]["name"] == "read"
    assert json.loads(call["function"]["arguments"]) == {"filePath": "/a"}
    assert events[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_the_first_event_announces_the_assistant_role() -> None:
    """Clients that build a message from deltas need the role before content."""
    body = {
        "id": "x",
        "model": "m",
        "created": 1,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
    }
    events = sse_events(shim.synthesise_sse(body))
    assert events[0]["choices"][0]["delta"].get("role") == "assistant"


def test_reasoning_content_is_preserved() -> None:
    """ds4 streams thinking separately; dropping it would change what we log."""
    body = {
        "id": "x",
        "model": "m",
        "created": 1,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "reasoning_content": "thinking",
                    "content": "hi",
                },
                "finish_reason": "stop",
            }
        ],
    }
    events = sse_events(shim.synthesise_sse(body))
    reasoning = "".join(
        e["choices"][0]["delta"].get("reasoning_content", "") for e in events
    )
    assert reasoning == "thinking"


def test_usage_is_carried_through_when_present() -> None:
    """The harness reads output_tokens off the final chunk; losing it loses a metric."""
    body = {
        "id": "x",
        "model": "m",
        "created": 1,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }
    events = sse_events(shim.synthesise_sse(body))
    assert any(e.get("usage", {}).get("completion_tokens") == 2 for e in events)


# --- the two halves together --------------------------------------------


def test_an_xml_fallback_response_becomes_a_real_tool_call() -> None:
    """End to end over the exact shape ds4 returns when it gives up."""
    body = {
        "id": "x",
        "model": "m",
        "created": 1,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": XML_FROM_THE_WIRE},
                "finish_reason": "stop",
            }
        ],
    }
    assert shim.translate_response(body) is True
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "read"
    assert "<function=" not in (choice["message"].get("content") or "")


def test_a_response_that_already_has_tool_calls_is_untouched() -> None:
    body = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "a",
                            "type": "function",
                            "function": {"name": "read", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    before = json.dumps(body)
    assert shim.translate_response(body) is False
    assert json.dumps(body) == before


def test_a_plain_text_answer_is_untouched() -> None:
    body = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "The file adds."},
                "finish_reason": "stop",
            }
        ]
    }
    assert shim.translate_response(body) is False
    assert body["choices"][0]["finish_reason"] == "stop"


# --- the request rewrite -------------------------------------------------


def test_a_streaming_tool_request_is_sent_upstream_unstreamed() -> None:
    """This is the fix. ds4's streaming path drops the fallback text entirely."""
    prepared = shim.prepare(
        json.dumps(
            {
                "messages": [{"role": "system", "content": "hi"}],
                "tools": TOOLS,
                "stream": True,
            }
        ).encode()
    )
    assert prepared.client_wants_stream is True
    assert json.loads(prepared.body)["stream"] is False


def test_a_streaming_request_without_tools_is_left_streaming() -> None:
    """No tools means no tool call to lose, so keep real streaming."""
    prepared = shim.prepare(
        json.dumps(
            {"messages": [{"role": "user", "content": "hi"}], "stream": True}
        ).encode()
    )
    assert prepared.client_wants_stream is False, "should pass through untouched"
    assert json.loads(prepared.body)["stream"] is True


def test_a_non_streaming_request_stays_non_streaming() -> None:
    prepared = shim.prepare(
        json.dumps(
            {"messages": [{"role": "system", "content": "hi"}], "tools": TOOLS}
        ).encode()
    )
    assert prepared.client_wants_stream is False
    assert json.loads(prepared.body).get("stream") in (None, False)


def test_a_non_json_body_is_passed_through() -> None:
    prepared = shim.prepare(b"not json")
    assert prepared.body == b"not json"
    assert prepared.client_wants_stream is False


# --- the third dialect ----------------------------------------------------
#
# Found in a trial transcript from the 45-trial arm A run on 2026-09-03. Under
# pressure the model does not only fall back to the Qwen XML dialect -- it also
# reaches for Claude's, which uses name= attributes rather than an `=` in the
# tag itself. The 45 trials did NOT run through the code below; it is an
# addition made after them and is so far unmeasured against the suite.


INVOKE_FROM_THE_WIRE = """The tool call format was invalid. Please use correct JSON.
</think>

<tool_call>
<invoke name="read">
<parameter name="filePath">/Users/evanhoffman/git/gmail-archive/src/storage.py</parameter>
</invoke>
</tool_call>"""


def test_it_parses_the_claude_invoke_dialect() -> None:
    calls = shim.parse_xml_tool_calls(INVOKE_FROM_THE_WIRE)
    assert calls is not None and len(calls) == 1
    assert calls[0]["function"]["name"] == "read"
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "filePath": "/Users/evanhoffman/git/gmail-archive/src/storage.py"
    }


def test_the_invoke_dialect_survives_stray_tool_call_opens() -> None:
    """The real transcript had bare <tool_call> opens stacked before the call."""
    text = (
        "</think>\n<tool_call>\n<tool_call>\n<tool_call>\n"
        '<invoke name="read">\n'
        '<parameter name="filePath">/a.py</parameter>\n'
        "</invoke>\n</tool_call>"
    )
    calls = shim.parse_xml_tool_calls(text)
    assert calls is not None
    assert json.loads(calls[0]["function"]["arguments"]) == {"filePath": "/a.py"}


def test_the_degeneration_loop_yields_nothing() -> None:
    """Stacked bare opens carry no function name, so there is no call to recover.

    This is the residual failure mode after the streaming fix: 9 of 45 trials.
    The translator must decline it rather than invent a call -- a fabricated
    tool call would be far worse than an empty turn, because it would run.
    """
    assert shim.parse_xml_tool_calls("<tool_call>\n" * 38) is None
    assert (
        shim.parse_xml_tool_calls(
            "The tool call format is wrong. I have to use the correct format: "
            '<tool_call>{"name": "read", "arguments": {...}}</tool_call>'
        )
        is None
    )


def test_the_two_dialects_do_not_double_count() -> None:
    """A message carrying both shapes must yield each call exactly once."""
    text = (
        "<tool_call><function=read><parameter=filePath>/a</parameter>"
        "</function></tool_call>\n"
        '<tool_call><invoke name="read">'
        '<parameter name="filePath">/b</parameter></invoke></tool_call>'
    )
    calls = shim.parse_xml_tool_calls(text)
    assert calls is not None and len(calls) == 2
    paths = [json.loads(c["function"]["arguments"])["filePath"] for c in calls]
    assert sorted(paths) == ["/a", "/b"]
