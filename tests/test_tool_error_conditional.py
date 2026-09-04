"""Does an error raise the odds the next tool call fails (#112)."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import tool_error_conditional as tec


def line(session, ts, status):
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": ts,
            "part": {"sessionID": session, "state": {"status": status}},
        }
    )


def transcript(rows):
    return "\n".join(line(*r) for r in rows)


def test_only_completed_and_error_calls_count():
    """A call still running is not evidence either way."""
    text = transcript([("s", 1, "completed"), ("s", 2, "running"), ("s", 3, "error")])
    assert len(tec.calls(text)) == 2


def test_non_tool_lines_are_ignored():
    assert tec.calls('{"type":"step_start"}\nnot json\n') == []


def test_prior_count_rises_only_on_an_error():
    got = tec.conditional(
        tec.calls(
            transcript(
                [
                    ("s", 1, "completed"),
                    ("s", 2, "completed"),
                    ("s", 3, "error"),
                    ("s", 4, "completed"),
                ]
            )
        )
    )
    assert got[0] == (1, 3)  # three calls seen with no prior error, one failed
    assert got[1] == (0, 1)  # one call after the error, it passed


def test_sessions_do_not_contaminate_each_other():
    """An error in one conversation says nothing about the next."""
    got = tec.conditional(
        tec.calls(transcript([("a", 1, "error"), ("b", 2, "completed")]))
    )
    assert got[0] == (1, 2)
    assert 1 not in got


def test_calls_are_ordered_by_timestamp_not_file_order():
    """Transcripts interleave; 'prior' must mean earlier in the conversation."""
    got = tec.conditional(
        tec.calls(transcript([("s", 9, "completed"), ("s", 1, "error")]))
    )
    assert got[0] == (1, 1)
    assert got[1] == (0, 1)


def test_report_names_the_small_sample():
    """A 5-failure result must not read as a measured effect."""
    text = tec.report(tec.conditional(tec.calls(transcript([("s", 1, "error")]))))
    assert "not a measured effect" in text
    assert "only 1 failures" in text


def test_no_calls_says_so():
    assert tec.report({}) == "no tool calls found"
