"""Tests for the #346 engine-timing reader.

The reader's whole job is to hand back the engine's own prompt-eval time so a
harness-overhead subtraction can be done against a client-observed first token.
Two shapes decide whether it is trustworthy: a cached prompt (no prompt-eval
line, engine TTFT ~0) and concurrent slots (blocks interleaved by task id). Both
are the cases #346 is about, so both are tested here rather than only the happy
path.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import engine_timing

# A full block for one request: prompt eval, eval, total, in order.
ONE = """\
slot print_timing: id  0 | task 32478 | prompt eval time =    1421.34 ms /   123 tokens (   11.56 ms per token,    86.54 tokens per second)
slot print_timing: id  0 | task 32478 |        eval time =    9385.05 ms /   239 tokens (   39.43 ms per token,    25.36 tokens per second)
slot print_timing: id  0 | task 32478 |       total time =   10806.39 ms /   362 tokens
"""


def test_a_full_block_yields_one_request_with_every_field() -> None:
    (r,) = engine_timing.parse(ONE)
    assert r.task == 32478
    assert r.slot == 0
    assert r.prompt_ms == 1421.34
    assert r.prompt_tokens == 123
    assert r.eval_ms == 9385.05
    assert r.eval_tokens == 239
    assert r.total_ms == 10806.39


def test_engine_ttft_is_the_prompt_eval_time() -> None:
    """The number #346 subtracts from the client-observed first token."""
    (r,) = engine_timing.parse(ONE)
    assert r.engine_ttft_ms == 1421.34
    assert r.prompt_cached is False


def test_a_cached_prompt_has_no_eval_line_and_reads_as_zero() -> None:
    """A fully cached prompt drops the prompt-eval line. The request is kept and
    marked cached, engine TTFT ~0 -- so an observed first token on it is nearly
    all harness overhead, which is exactly the case the issue is chasing."""
    cached = """\
slot print_timing: id  0 | task 9 |        eval time =    500.00 ms /    20 tokens ( ... )
slot print_timing: id  0 | task 9 |       total time =    520.00 ms /    20 tokens
"""
    (r,) = engine_timing.parse(cached)
    assert r.prompt_cached is True
    assert r.prompt_ms is None
    assert r.engine_ttft_ms == 0.0


def test_concurrent_slots_are_grouped_by_task_not_adjacency() -> None:
    """Two in-flight requests interleave their lines. Grouping by task id keeps
    them apart; grouping by adjacency would fuse one request's prompt eval with
    the other's eval and report a timing that never happened."""
    interleaved = """\
slot print_timing: id  0 | task 100 | prompt eval time =    100.00 ms /    10 tokens ( ... )
slot print_timing: id  1 | task 200 | prompt eval time =    200.00 ms /    20 tokens ( ... )
slot print_timing: id  0 | task 100 |        eval time =    900.00 ms /    30 tokens ( ... )
slot print_timing: id  1 | task 200 |        eval time =    800.00 ms /    40 tokens ( ... )
slot print_timing: id  0 | task 100 |       total time =   1000.00 ms /    40 tokens
slot print_timing: id  1 | task 200 |       total time =   1000.00 ms /    60 tokens
"""
    a, b = engine_timing.parse(interleaved)
    assert (a.task, a.slot, a.prompt_ms, a.eval_ms) == (100, 0, 100.0, 900.0)
    assert (b.task, b.slot, b.prompt_ms, b.eval_ms) == (200, 1, 200.0, 800.0)


def test_first_seen_task_order_is_preserved() -> None:
    two = (
        ONE
        + "slot print_timing: id 0 | task 5 | prompt eval time = 1.0 ms / 1 tokens ( )\n"
    )
    tasks = [r.task for r in engine_timing.parse(two)]
    assert tasks == [32478, 5]


def test_a_truncated_block_still_appears_with_what_was_logged() -> None:
    """A server that is still running cuts its last block off mid-way. The
    request should not vanish -- it appears with the fields that were logged."""
    truncated = "slot print_timing: id 0 | task 7 | prompt eval time = 300.00 ms / 40 tokens ( )\n"
    (r,) = engine_timing.parse(truncated)
    assert r.prompt_ms == 300.00
    assert r.eval_ms is None
    assert r.total_ms is None


def test_non_timing_lines_are_ignored() -> None:
    noisy = "INFO some other log line\n" + ONE + "srv update_slots: all slots idle\n"
    assert len(engine_timing.parse(noisy)) == 1


def test_empty_input_is_no_requests_not_an_error() -> None:
    assert engine_timing.parse("") == []


def test_render_marks_cached_and_reports_a_median_over_measured_only() -> None:
    """The median engine TTFT must ignore cached requests -- averaging a real
    prefill against a ~0 cached one understates the engine's cost."""
    text = (
        ONE
        + """\
slot print_timing: id 0 | task 40 |        eval time = 10.0 ms / 2 tokens ( )
slot print_timing: id 0 | task 40 |       total time = 12.0 ms / 2 tokens
"""
    )
    lines = engine_timing.render(engine_timing.parse(text))
    table = "\n".join(lines)
    assert "cached" in table
    assert "1421.3" in table
    # one measured request -> median is its own prompt eval, not an average with 0
    assert "median 1421.3 ms over 1 request" in table
    assert "1 cached" in table


def test_render_when_everything_was_cached_says_so() -> None:
    cached = "slot print_timing: id 0 | task 9 | total time = 5.0 ms / 1 tokens\n"
    lines = engine_timing.render(engine_timing.parse(cached))
    assert any("no request logged a prompt eval" in line for line in lines)
