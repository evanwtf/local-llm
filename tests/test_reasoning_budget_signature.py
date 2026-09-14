"""The budget-exhaustion classifier in scripts/reasoning_budget_signature.py (#349).

The claim under test is that an empty agent solution can be a *spent reasoning
budget* rather than degeneration. The classifier must (1) only ever count a
thinking-on row as a suspect -- a thinking-off empty is a different failure --
and (2) require both an empty/near-empty solution and a large reasoning spend, so
a normal short answer or a healthy long-reasoning answer is not swept in.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import reasoning_budget_signature as rbs


def _row(**kw):
    return kw


def test_high_reasoning_zero_output_solution_is_a_suspect():
    # Zero surviving content after 32k reasoning tokens: classic starvation.
    rows = [
        _row(
            backend="b",
            task="t",
            reasoning_tokens=32000,
            output_tokens=0,
            solution_empty=True,
        )
    ]
    c = rbs.classify(rows)
    assert len(c["suspects"]) == 1


def test_high_reasoning_near_empty_output_is_a_suspect():
    # Not flagged solution_empty, but a handful of output tokens cannot carry a
    # tool call plus a patch -- reasoning ate the turn.
    rows = [_row(backend="b", task="t", reasoning_tokens=32000, output_tokens=6)]
    c = rbs.classify(rows)
    assert len(c["suspects"]) == 1


def test_thinking_off_empty_is_never_a_suspect():
    # reasoning_tokens == 0 means thinking off: an empty solution here cannot be
    # budget exhaustion and must be counted only as a control.
    rows = [_row(backend="b", task="t", reasoning_tokens=0, solution_empty=True)]
    c = rbs.classify(rows)
    assert c["suspects"] == []
    assert len(c["thinking_off_empty"]) == 1


def test_content_starved_by_ratio_is_a_suspect():
    # Not near-empty (127 > 64) but reasoning is 256x the output: the budget was
    # spent reasoning and little answer survived. This is the ratio path.
    rows = [_row(backend="b", task="t", reasoning_tokens=32493, output_tokens=127)]
    c = rbs.classify(rows)
    assert len(c["suspects"]) == 1


def test_healthy_long_reasoning_answer_is_not_a_suspect():
    # Lots of reasoning AND a full solution: the budget was not exhausted.
    rows = [_row(backend="b", task="t", reasoning_tokens=34000, output_tokens=31356)]
    c = rbs.classify(rows)
    assert c["suspects"] == []


def test_empty_flagged_with_substantial_output_is_degeneration_not_starvation():
    # Heavy reasoning, flagged solution_empty, but 31k output tokens: the model
    # reasoned AND emitted a large answer that failed to parse. That is
    # degeneration, and must land in its own bucket, never among the suspects.
    rows = [
        _row(
            backend="b",
            task="t",
            reasoning_tokens=34793,
            output_tokens=31356,
            solution_empty=True,
        )
    ]
    c = rbs.classify(rows)
    assert c["suspects"] == []
    assert len(c["degenerate"]) == 1


def test_short_reasoning_empty_is_not_a_suspect():
    # Empty solution but almost no reasoning: this is degeneration, not the
    # budget mechanism, so it must not be attributed to a spent budget.
    rows = [_row(backend="b", task="t", reasoning_tokens=130, output_tokens=6)]
    c = rbs.classify(rows)
    assert c["suspects"] == []


def test_rows_without_reasoning_accounting_are_skipped():
    # No reasoning_tokens field at all: cannot classify, must not crash or count.
    rows = [_row(backend="b", task="t", output_tokens=0)]
    c = rbs.classify(rows)
    assert c["thinking_on"] == [] and c["thinking_off"] == []


def test_boolean_is_not_treated_as_a_number():
    # A stray reasoning_tokens=True must not read as 1 and land in thinking-on.
    rows = [_row(backend="b", task="t", reasoning_tokens=True, solution_empty=True)]
    c = rbs.classify(rows)
    assert c["thinking_on"] == [] and c["thinking_off"] == []
