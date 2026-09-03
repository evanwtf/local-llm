"""#96: per-turn TTFT from OpenCode's own transcript, as a first metric.

The issue's cheapest step said "we do not currently have a TTFT-per-turn
metric. That is arguably worth adding regardless." This tests the parser that
adds one.

The metric is NOT wire TTFT from ds4's perspective -- OpenCode gives no wire
timing. It is the time from `step_start` to the first `text`/`tool_use` event
in the same step, i.e. what the agent user experienced including any client-
side buffering. Which is the honest metric for a local coding agent: it names
what a person waiting at their laptop actually waits for.

A tool-response acknowledgment step has a TTFT of a few milliseconds because
no model call happens. Those are filtered above 100 ms so the recorded median
describes real model turns rather than stream bookkeeping.
"""

from __future__ import annotations

import json

import run


def make_transcript(events: list[dict]) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_ttft_is_step_start_to_first_content() -> None:
    """A 2400 ms wait between step_start and the first text is a 2400 ms TTFT."""
    stdout = make_transcript(
        [
            {"type": "step_start", "timestamp": 1_000_000},
            {"type": "text", "timestamp": 1_002_400, "part": {"text": "hi"}},
            {
                "type": "step_finish",
                "timestamp": 1_002_500,
                "part": {"tokens": {"input": 100, "output": 10}},
            },
        ]
    )
    row = run.opencode_parse(stdout)
    assert row["step_ttft_ms_median"] == 2400
    assert row["num_turns"] == 1
    assert row["output_tokens"] == 10


def test_tool_acknowledgment_steps_are_filtered_from_the_model_median() -> None:
    """A 5 ms step is OpenCode bookkeeping, not a model call.

    Recorded median (all steps) can be tiny; model-only median must not.
    """
    stdout = make_transcript(
        [
            # A real model call at 3000 ms
            {"type": "step_start", "timestamp": 1_000_000},
            {"type": "text", "timestamp": 1_003_000, "part": {}},
            {"type": "step_finish", "timestamp": 1_003_100, "part": {"tokens": {}}},
            # A tool acknowledgment at 5 ms
            {"type": "step_start", "timestamp": 1_003_200},
            {"type": "tool_use", "timestamp": 1_003_205, "part": {}},
            {"type": "step_finish", "timestamp": 1_003_210, "part": {"tokens": {}}},
            # Another tool acknowledgment at 3 ms
            {"type": "step_start", "timestamp": 1_003_300},
            {"type": "tool_use", "timestamp": 1_003_303, "part": {}},
            {"type": "step_finish", "timestamp": 1_003_310, "part": {"tokens": {}}},
        ]
    )
    row = run.opencode_parse(stdout)
    assert row["num_steps"] == 3
    assert row["num_model_steps"] == 1
    assert row["model_step_ttft_ms_median"] == 3000
    # The overall median mixes acknowledgments in; that is why the model-only
    # median exists.
    assert row["step_ttft_ms_median"] == 5


def test_first_content_wins_over_second() -> None:
    """A step with both tool_use and text records the FIRST one."""
    stdout = make_transcript(
        [
            {"type": "step_start", "timestamp": 1_000_000},
            {"type": "tool_use", "timestamp": 1_001_500, "part": {}},
            {"type": "text", "timestamp": 1_002_900, "part": {}},
            {"type": "step_finish", "timestamp": 1_003_000, "part": {"tokens": {}}},
        ]
    )
    row = run.opencode_parse(stdout)
    assert row["step_ttft_ms_median"] == 1500


def test_a_transcript_without_timestamps_records_no_ttft() -> None:
    """An old transcript is still parsed; absence must not become a bogus 0."""
    stdout = make_transcript(
        [
            {"type": "step_start"},
            {"type": "text", "part": {}},
            {"type": "step_finish", "part": {"tokens": {"input": 100, "output": 5}}},
        ]
    )
    row = run.opencode_parse(stdout)
    assert row["num_turns"] == 1
    assert "step_ttft_ms_median" not in row


def test_p90_over_ten_steps_returns_the_ninth_ranked_value() -> None:
    """Nearest-rank, not interpolation -- ms/token quantities are integer."""
    ttfts = [100, 200, 300, 400, 500, 600, 700, 800, 900, 10_000]
    events = [{"type": "step_start", "timestamp": 0}]
    for i, ttft in enumerate(ttfts):
        base = i * 100_000
        events += [
            {"type": "step_start", "timestamp": base},
            {"type": "text", "timestamp": base + ttft, "part": {}},
            {
                "type": "step_finish",
                "timestamp": base + ttft + 10,
                "part": {"tokens": {}},
            },
        ]
    row = run.opencode_parse(make_transcript(events))
    # There are actually 11 step_starts (the seed + 10 real) but only 10 have
    # a following text before the next step_start; check the count.
    assert row["num_steps"] == 10
    # p90 of ten values is the 9th-ranked (index 8): 900 ms.
    assert row["step_ttft_ms_p90"] == 900


def test_percentile_helper_is_bounded() -> None:
    """One sample: p90 is the sample itself, not itself minus something."""
    assert run._percentile_int([42], 0.90) == 42
    assert run._percentile_int([], 0.90) == 0
