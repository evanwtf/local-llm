"""#334: the load generator's arithmetic, which decides a published number.

`scripts/vllm_load.py` is the only thing on the DGX Spark that can measure
aggregate multi-stream throughput -- the agent harness is single-stream by
construction. Its two rates mean different things and are easy to conflate:
aggregate is what a third-party claim quotes, per-stream is what one user
experiences, and the interesting result is that they move in opposite
directions as concurrency rises.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import vllm_load


def test_aggregate_is_summed_tokens_over_wall_not_a_mean_of_rates(monkeypatch):
    """Two streams finishing at different times still share one wall clock."""
    calls = iter(
        [
            {"wall": 10.0, "output_tokens": 100, "prompt_tokens": 5},
            {"wall": 20.0, "output_tokens": 200, "prompt_tokens": 5},
        ]
    )
    monkeypatch.setattr(vllm_load, "one_stream", lambda *a, **k: next(calls))
    got = vllm_load.level("http://x", "m", 2, 512)
    assert got["output_tokens"] == 300
    # 300 tokens over the wall of the whole level, which is at least the
    # slowest stream. Averaging 10 and 10 tok/s would report 10 and be wrong.
    assert got["aggregate_tps"] == 300 / got["wall"]
    assert got["slowest_stream"] == 20.0


def test_per_stream_is_a_median_not_the_aggregate_divided(monkeypatch):
    """One slow straggler must not be hidden by dividing the total."""
    calls = iter(
        [
            {"wall": 10.0, "output_tokens": 100, "prompt_tokens": 5},
            {"wall": 10.0, "output_tokens": 100, "prompt_tokens": 5},
            {"wall": 100.0, "output_tokens": 100, "prompt_tokens": 5},
        ]
    )
    monkeypatch.setattr(vllm_load, "one_stream", lambda *a, **k: next(calls))
    got = vllm_load.level("http://x", "m", 3, 512)
    assert got["per_stream_tps"] == 10.0


def test_a_level_with_no_tokens_does_not_divide_by_zero(monkeypatch):
    """A refused or empty response is a real outcome, not a crash."""
    monkeypatch.setattr(
        vllm_load,
        "one_stream",
        lambda *a, **k: {"wall": 1.0, "output_tokens": 0, "prompt_tokens": 0},
    )
    got = vllm_load.level("http://x", "m", 2, 512)
    assert got["aggregate_tps"] == 0.0
    assert got["per_stream_tps"] == 0.0
