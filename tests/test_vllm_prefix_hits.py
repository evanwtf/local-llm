"""scripts/vllm_prefix_hits.py: the #626 prefix-cache probe."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import vllm_prefix_hits as v

METRICS = """\
# HELP vllm:prefix_cache_hits_total Prefix cache hits, in tokens.
# TYPE vllm:prefix_cache_hits_total counter
vllm:prefix_cache_hits_total{engine="0",model_name="m"} 1664.0
vllm:prefix_cache_hits_total{engine="1",model_name="m"} 832.0
vllm:prefix_cache_queries_total{engine="0",model_name="m"} 4000.0
vllm:prefix_cache_hits_created{engine="0",model_name="m"} 1.7e9
"""


def test_a_counter_sums_every_label_set() -> None:
    assert v.counter(METRICS, v.HITS) == 2496.0


def test_a_counter_ignores_the_created_series() -> None:
    assert v.counter(METRICS, v.QUERIES) == 4000.0


def test_a_counter_reads_the_bare_name_too() -> None:
    assert v.counter("vllm:prefix_cache_hits 12.0\n", v.HITS) == 12.0


def test_a_missing_counter_is_an_error_not_zero() -> None:
    """A renamed metric must not read as 'no hits'."""
    with pytest.raises(KeyError):
        v.counter("vllm:other_total 1.0\n", v.HITS)


def test_lengths_put_two_controls_beside_each_multiple() -> None:
    assert v.lengths(832, [1, 2]) == [
        (832, "multiple"),
        (833, "plus1"),
        (1248, "half"),
        (1664, "multiple"),
        (1665, "plus1"),
        (2080, "half"),
    ]


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [(832, 0), (833, 832), (1248, 832), (1664, 832), (1665, 1664), (2080, 1664)],
)
def test_the_ceiling_leaves_the_last_token_out(tokens: int, expected: int) -> None:
    assert v.ceiling(tokens, 832) == expected
