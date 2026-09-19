"""The engine's own TTFT per trial, from the histogram delta. #444"""

from __future__ import annotations

import engine_ttft as et

VLLM = """\
# HELP vllm:time_to_first_token_seconds Histogram of time to first token in seconds.
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{engine="0",le="0.5",model_name="m"} 3.0
vllm:time_to_first_token_seconds_sum{engine="0",model_name="m"} %s
vllm:time_to_first_token_seconds_count{engine="0",model_name="m"} %s
"""


def test_parse_reads_vllm_sum_and_count():
    snap = et.parse(VLLM % ("12.5", "10.0"))
    assert snap == et.Snapshot("vllm", 12.5, 10)


def test_parse_sums_across_engines():
    text = (VLLM % ("2.0", "4.0")) + (
        'vllm:time_to_first_token_seconds_sum{engine="1",model_name="m"} 3.0\n'
        'vllm:time_to_first_token_seconds_count{engine="1",model_name="m"} 2.0\n'
    )
    assert et.parse(text) == et.Snapshot("vllm", 5.0, 6)


def test_parse_reads_sglang():
    text = (
        'sglang:time_to_first_token_seconds_sum{model_name="m"} 1.5\n'
        'sglang:time_to_first_token_seconds_count{model_name="m"} 3\n'
    )
    assert et.parse(text) == et.Snapshot("sglang", 1.5, 3)


def test_an_engine_without_the_histogram_reads_as_absent():
    assert et.parse("llamacpp:prompt_tokens_total 10\n") is None


def test_the_trial_mean_is_the_delta_of_sum_over_count():
    before = et.Snapshot("vllm", 10.0, 8)
    after = et.Snapshot("vllm", 16.0, 12)  # 4 requests, 6.0 s between them
    assert et.fields(before, after) == {
        "engine_ttft_source": "vllm",
        "engine_ttft_requests": 4,
        "engine_ttft_ms_mean": 1500.0,
    }


def test_a_trial_with_no_requests_records_zero_requests_and_no_mean():
    snap = et.Snapshot("vllm", 10.0, 8)
    assert et.fields(snap, snap) == {
        "engine_ttft_source": "vllm",
        "engine_ttft_requests": 0,
    }


def test_a_restart_during_the_trial_records_nothing():
    assert et.fields(et.Snapshot("vllm", 50.0, 40), et.Snapshot("vllm", 2.0, 3)) == {}


def test_a_missing_side_records_nothing():
    snap = et.Snapshot("vllm", 1.0, 1)
    assert et.fields(None, snap) == {}
    assert et.fields(snap, None) == {}


def test_scrape_without_a_base_url_is_none():
    assert et.scrape(None) is None
    assert et.scrape("") is None
