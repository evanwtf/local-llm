"""Tests for the OpenCode tool-call-retry counter (#219).

The counter reads the CLIENT side of a trial -- the OpenCode transcript -- so
it can compare llama.cpp against ds4, which the server-log instrument cannot
(only ds4 emits "TOOLS invalid tool call; continuing"). It runs unattended, so
refusing a shape it does not recognise is its whole job: a zero that means
"no retries" and a zero that means "I could not read this" must not be the
same value. The tests weight the negative cases accordingly.

The parser is written against a documented schema assumption (v1) because the
real transcripts live on the operator's machine and are not in this repo. The
fixtures here are synthetic, built to that assumption; the peer sends back
deltas when the real shape differs.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from tool_retry_count import TranscriptError, count_transcript


def make_transcript(events: list[dict]) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def write_transcript(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "trial.stdout.jsonl"
    path.write_text(body)
    return path


def test_empty_transcript_raises(tmp_path) -> None:
    """An empty file is a capture failure, not a trial with no tool calls."""
    path = write_transcript(tmp_path, "")
    with pytest.raises(TranscriptError, match="empty"):
        count_transcript(path)


def test_truncated_transcript_raises(tmp_path) -> None:
    """A tool call opened but never closed means the capture was cut off."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                {"type": "tool_use", "part": {"id": "a", "tool": "bash"}},
            ]
        ),
    )
    with pytest.raises(TranscriptError, match="still open"):
        count_transcript(path)


def test_unknown_event_type_raises(tmp_path) -> None:
    """An event type the parser does not know might be a tool-call signal."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                {"type": "tool_use", "part": {"id": "a", "tool": "bash"}},
                {"type": "tool.execution.completed", "id": "a"},
                {"type": "mystery.event", "id": "a"},
            ]
        ),
    )
    with pytest.raises(TranscriptError, match="unknown event type"):
        count_transcript(path)


def test_invalid_json_line_raises(tmp_path) -> None:
    """A line that is not valid JSON is a truncated or corrupt transcript."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                {"type": "tool_use", "part": {"id": "a", "tool": "bash"}},
            ]
        )
        + '{"type": "tool.execution.completed", "id": "a"\n',  # cut mid-object
    )
    with pytest.raises(TranscriptError, match="not valid JSON"):
        count_transcript(path)


def test_close_without_open_raises(tmp_path) -> None:
    """A close event with no open call is a shape the parser does not know."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                {"type": "tool.execution.completed", "id": "a"},
            ]
        ),
    )
    with pytest.raises(TranscriptError, match="no open tool call"):
        count_transcript(path)


def test_error_without_retryable_raises(tmp_path) -> None:
    """An error event must say whether the client retried or gave up."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                {"type": "tool_use", "part": {"id": "a", "tool": "bash"}},
                {"type": "tool.execution.error", "id": "a"},
            ]
        ),
    )
    with pytest.raises(TranscriptError, match="retryable"):
        count_transcript(path)


def test_retry_nested_inside_another_tool_call(tmp_path) -> None:
    """A retry inside another call is counted, not lost or double-counted."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                {"type": "tool_use", "part": {"id": "a", "tool": "bash"}},
                {"type": "tool_use", "part": {"id": "b", "tool": "read"}},
                {"type": "tool.execution.error", "id": "b", "retryable": True},
                {"type": "tool.execution.completed", "id": "a"},
            ]
        ),
    )
    row = count_transcript(path)
    assert row["tool_calls_issued"] == 2
    assert row["tool_calls_retried"] == 1
    assert row["tool_calls_failed_terminal"] == 0


def test_normal_transcript_counts(tmp_path) -> None:
    """Issued, retried and terminal failures are counted separately."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                {"type": "tool_use", "part": {"id": "a", "tool": "bash"}},
                {"type": "tool.execution.completed", "id": "a"},
                {"type": "tool_use", "part": {"id": "b", "tool": "bash"}},
                {"type": "tool.execution.error", "id": "b", "retryable": True},
                {"type": "tool_use", "part": {"id": "c", "tool": "read"}},
                {"type": "tool.execution.error", "id": "c", "retryable": False},
            ]
        ),
    )
    row = count_transcript(path)
    assert row["tool_calls_issued"] == 3
    assert row["tool_calls_retried"] == 1
    assert row["tool_calls_failed_terminal"] == 1


def test_only_bookkeeping_returns_zero(tmp_path) -> None:
    """A trial that made no tool calls is a valid zero, not a refusal."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                {"type": "step_start", "timestamp": 1_000_000},
                {"type": "text", "timestamp": 1_002_400, "part": {"text": "hi"}},
                {
                    "type": "step_finish",
                    "timestamp": 1_002_500,
                    "part": {"tokens": {"input": 100, "output": 10}},
                },
            ]
        ),
    )
    row = count_transcript(path)
    assert row["tool_calls_issued"] == 0
    assert row["tool_calls_retried"] == 0
    assert row["tool_calls_failed_terminal"] == 0


def test_row_is_joinable_on_the_transcript_stem(tmp_path) -> None:
    """The row names the transcript stem, the join key to client_log."""
    path = write_transcript(tmp_path, make_transcript([{"type": "step_start"}]))
    row = count_transcript(path)
    assert row["transcript"] == "trial"
