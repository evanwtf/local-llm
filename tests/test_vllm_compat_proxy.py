"""The field-stripping contract for the vLLM compat proxy (#331)."""

from __future__ import annotations

import importlib.util
import json
import pathlib

_spec = importlib.util.spec_from_file_location(
    "vllm_compat_proxy",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "vllm_compat_proxy.py",
)
proxy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proxy)


def test_strips_nonstandard_fields_from_a_completion():
    obj = {
        "id": "x",
        "object": "chat.completion",
        "prompt_token_ids": [1, 2],
        "prompt_text": "hi",
        "choices": [
            {
                "index": 0,
                "token_ids": [9, 9],
                "message": {"role": "assistant", "content": "hi", "reasoning": "why"},
            }
        ],
    }
    out = proxy.strip_completion(obj)
    assert "prompt_token_ids" not in out and "prompt_text" not in out
    c = out["choices"][0]
    assert "token_ids" not in c
    assert "reasoning" not in c["message"]
    # standard fields survive untouched
    assert c["message"] == {"role": "assistant", "content": "hi"}
    assert out["id"] == "x"


def test_strips_reasoning_from_a_stream_delta():
    chunk = {
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"reasoning": "We"}, "token_ids": None}],
    }
    out = proxy.strip_completion(chunk)
    assert out["choices"][0]["delta"] == {}
    assert "token_ids" not in out["choices"][0]


def test_tool_calls_are_preserved():
    obj = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning": "plan",
                    "tool_calls": [{"id": "t1", "type": "function", "function": {}}],
                }
            }
        ]
    }
    out = proxy.strip_completion(obj)
    m = out["choices"][0]["message"]
    assert "reasoning" not in m
    assert m["tool_calls"] == [{"id": "t1", "type": "function", "function": {}}]


def test_sse_data_line_is_rewritten():
    line = b'data: {"choices":[{"delta":{"reasoning":"x","content":"y"}}]}\n'
    out = proxy.strip_sse_data(line)
    obj = json.loads(out[5:].strip())
    assert obj["choices"][0]["delta"] == {"content": "y"}


def test_sse_done_and_blank_pass_through():
    assert proxy.strip_sse_data(b"data: [DONE]\n") == b"data: [DONE]\n"
    assert proxy.strip_sse_data(b"\n") == b"\n"
    assert proxy.strip_sse_data(b": keep-alive\n") == b": keep-alive\n"


def test_non_json_data_is_left_alone():
    assert proxy.strip_sse_data(b"data: not json\n") == b"data: not json\n"


def test_non_dict_is_returned_unchanged():
    assert proxy.strip_completion([1, 2]) == [1, 2]
