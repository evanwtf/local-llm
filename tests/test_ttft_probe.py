"""The histogram-delta math in scripts/ttft_probe.py (#346).

The engine TTFT term is a subtraction across a vLLM histogram; these check it
reads the right numbers and never manufactures a 0 ms prefill when no request
landed in the window.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import ttft_probe

_METRICS = """\
# HELP vllm:time_to_first_token_seconds TTFT
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_sum{engine="0",model_name="m"} 0.5
vllm:time_to_first_token_seconds_count{engine="0",model_name="m"} 2.0
vllm:num_requests_running{engine="0"} 0.0
"""


def test_parse_histogram_reads_sum_and_count_past_labels():
    got = ttft_probe.parse_histogram(_METRICS, "vllm:time_to_first_token_seconds")
    assert got == (0.5, 2.0)


def test_parse_histogram_absent_metric_is_none():
    assert ttft_probe.parse_histogram(_METRICS, "vllm:no_such_metric") is None


def test_engine_ttft_is_the_per_request_delta_in_ms():
    # One new request added 0.25 s of sum over one count -> 250 ms.
    assert ttft_probe.engine_ttft_ms((0.5, 2.0), (0.75, 3.0)) == pytest.approx(250.0)


def test_engine_ttft_none_when_no_request_landed():
    # count did not advance: no request in the window, so no TTFT to report --
    # never divide by zero, never invent a 0 ms prefill.
    assert ttft_probe.engine_ttft_ms((0.5, 2.0), (0.5, 2.0)) is None


def test_engine_ttft_averages_multiple_requests_in_the_window():
    # Two requests, 0.4 s total -> 200 ms each on average.
    assert ttft_probe.engine_ttft_ms((1.0, 10.0), (1.4, 12.0)) == pytest.approx(200.0)
