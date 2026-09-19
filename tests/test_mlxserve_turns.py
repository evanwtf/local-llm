"""Tests for scripts/mlxserve_turns.py (#479): per-turn prefill from a server log."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mlxserve_turns

STREAMED = (
    "  <- 63463+84 tokens streamed [prefill: 395.2 tok/s "
    "(31392 cached / 63463 total), decode: 38.4 tok/s] [tool_calls]"
)
UNCACHED = "  <- 26+90 tokens (2578ms) [prefill: 22.9 tok/s, decode: 63.7 tok/s] [stop]"


def test_a_streamed_turn_reads_every_field():
    t = mlxserve_turns.parse_line(STREAMED)
    assert t is not None
    assert t.total == 63463
    assert t.cached == 31392
    assert t.generated == 84
    assert t.prefill_tps == 395.2
    assert t.decode_tps == 38.4


def test_the_re_prefilled_tokens_are_total_minus_cached():
    t = mlxserve_turns.parse_line(STREAMED)
    assert t is not None
    assert t.uncached == 63463 - 31392
    # Seconds spent on prefill: the uncached tokens at the reported rate.
    assert t.prefill_seconds == pytest.approx((63463 - 31392) / 395.2)


def test_a_turn_with_no_cache_figure_counts_every_token_as_uncached():
    t = mlxserve_turns.parse_line(UNCACHED)
    assert t is not None
    assert t.cached == 0
    assert t.uncached == 26


def test_other_lines_are_not_turns():
    assert mlxserve_turns.parse_line("Hot prefix cache: ENABLED (capacity=32)") is None
    assert (
        mlxserve_turns.parse_line("  [hot-cache] resident=91.81 / 2048.00 MB") is None
    )


def test_buckets_group_turns_by_context_size():
    turns = [
        mlxserve_turns.parse_line(STREAMED),
        mlxserve_turns.parse_line(UNCACHED),
    ]
    got = mlxserve_turns.by_bucket([t for t in turns if t], edges=(16384, 32768))
    assert [b.label for b in got] == ["0-16K", "32K+"]
    assert got[0].turns == 1
    assert got[1].turns == 1
    assert got[1].median_uncached == 63463 - 31392


def test_render_gives_seconds_beside_every_rate():
    turns = [mlxserve_turns.parse_line(STREAMED)]
    md = mlxserve_turns.render(
        mlxserve_turns.by_bucket([t for t in turns if t], edges=(16384, 32768))
    )
    # 32071 uncached tokens at 395.2 tok/s is 81.2 s.
    assert "| 32K+ | 1 | 32071 | 395.2 | 81.2 | 38.4 |" in md
