"""The #112 transcript classifier — the logic a wrong number would hide in.

`degeneration_cascade.py` reads what the model did per step and decides whether
a step was a degeneration turn, a clean tool call, or a tool error, then builds
the conditional P(malformed | prior tool errors). A silent bug here does not
crash -- it ships a wrong rate onto an issue. So the classifier's edges are
pinned: a format complaint that still lands a tool call is NOT malformed (the
script-reverse trial 3 case, which passed), a null finishReason with a
completed call still counts as a call, and the conditional buckets by the
errors that preceded each attempt.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import degeneration_cascade as dc


def _write(tmp_path: pathlib.Path, name: str, events: list[dict]) -> pathlib.Path:
    f = tmp_path / f"{name}.stdout.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return f


def _start() -> dict:
    return {"type": "step_start", "part": {}}


def _text(s: str) -> dict:
    return {"type": "text", "part": {"text": s}}


def _tool(tool: str, status: str) -> dict:
    return {"type": "tool_use", "part": {"tool": tool, "state": {"status": status}}}


def _finish(reason: str, out: int = 10) -> dict:
    return {
        "type": "step_finish",
        "part": {"finishReason": reason, "tokens": {"output": out}},
    }


def test_format_complaint_with_no_call_is_malformed(tmp_path):
    f = _write(
        tmp_path,
        "death",
        [_start(), _text("The tool call format was wrong. </think>"), _finish("stop")],
    )
    t = dc.parse_transcript(f)
    assert t.malformed_steps == [0]
    assert not t.recovered()


def test_complaint_that_still_lands_a_call_is_not_malformed(tmp_path):
    # script-reverse trial 3: narrates about the format, then the tool completes
    # in the same step, and the trial passes. A null finishReason must not make
    # this look like a degeneration turn.
    f = _write(
        tmp_path,
        "recovered-in-step",
        [
            _start(),
            _text("The tool call failed? invalid Qwen tool call. I'll retry."),
            _tool("write", "completed"),
            _finish("", out=50),
        ],
    )
    t = dc.parse_transcript(f)
    assert t.malformed_steps == []


def test_stacked_opens_are_malformed(tmp_path):
    f = _write(
        tmp_path,
        "stacked",
        [
            _start(),
            _text("</think> <tool_call> <tool_call> <tool_call>"),
            _finish("unknown"),
        ],
    )
    t = dc.parse_transcript(f)
    assert t.malformed_steps == [0]


def test_recovery_is_a_clean_call_after_a_malformed_turn(tmp_path):
    f = _write(
        tmp_path,
        "recover",
        [
            _start(),
            _text("The tool call format is wrong."),
            _finish("unknown"),
            _start(),
            _tool("bash", "completed"),
            _finish("tool-calls"),
        ],
    )
    t = dc.parse_transcript(f)
    assert t.malformed_steps == [0]
    assert t.recovered()


def test_tool_error_is_counted_and_not_malformed(tmp_path):
    f = _write(
        tmp_path,
        "err",
        [_start(), _tool("read", "error"), _finish("tool-calls")],
    )
    t = dc.parse_transcript(f)
    assert t.tool_errors == 1
    assert t.malformed_steps == []


def test_conditional_buckets_by_prior_errors(tmp_path):
    # One trial: a clean call, then a tool error, then a malformed turn.
    # The malformed attempt sits at k=1 prior error; the first clean call at k=0.
    t = dc.Trial(
        name="x",
        steps=[
            dc.Step(
                tool_names=["read"],
                tool_statuses=["completed"],
                finish_reason="tool-calls",
            ),
            dc.Step(
                tool_names=["read"], tool_statuses=["error"], finish_reason="tool-calls"
            ),
            dc.Step(text="the tool call format is wrong", finish_reason="stop"),
        ],
    )
    cond = dc.conditional([t])
    assert cond[0] == (0, 1)  # first attempt, no prior error, clean
    assert cond[1] == (1, 1)  # malformed attempt, one prior error
