"""Routing --server-log / --metrics-url to the right reader (#356).

A vLLM speculative arm needs BOTH a file (the prefill-failure scanner, #266) and
a URL (the draft counters from /metrics). One `--server-log` value cannot be
both, and the old code handed a URL to the file scanner -- surfacing an Errno 2
on a path-collapsed `http:/host` rather than a clear message. `resolve_counter_
sources` splits them; these check each combination lands where it can be read and
the wrong ones refuse plainly.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import run


def test_url_looks_like_url_but_a_path_does_not():
    assert run._looks_like_url("http://127.0.0.1:8030")
    assert run._looks_like_url("https://host/x")
    assert not run._looks_like_url("/home/evan/bench-logs/vllm.log")
    assert not run._looks_like_url(None)


def test_file_engine_reads_server_log_and_scans_it():
    # ds4: the draft counters and the prefill scanner both read the one file.
    draft, prefill = run.resolve_counter_sources("ds4", "/logs/ds4.log", None)
    assert draft == "/logs/ds4.log"
    assert prefill == "/logs/ds4.log"


def test_vllm_reads_metrics_url_and_still_scans_the_file():
    # The whole point of #356: one run satisfies BOTH readers.
    draft, prefill = run.resolve_counter_sources(
        "vllm", "/logs/vllm.log", "http://127.0.0.1:8030"
    )
    assert draft == "http://127.0.0.1:8030"
    assert prefill == "/logs/vllm.log"


def test_vllm_url_as_server_log_is_back_compat_and_skips_the_file_scan():
    # #319 usage: URL passed as --server-log, no --metrics-url. Draft counters
    # still work; the file scanner is skipped rather than crashing on the URL.
    draft, prefill = run.resolve_counter_sources("vllm", "http://127.0.0.1:8030", None)
    assert draft == "http://127.0.0.1:8030"
    assert prefill is None


def test_vllm_with_a_file_and_no_metrics_url_refuses_plainly():
    # A file given where the endpoint is needed: refuse, naming --metrics-url,
    # instead of scraping "file/metrics" and reporting a quiet engine.
    with pytest.raises(run.CounterSourceError, match="--metrics-url"):
        run.resolve_counter_sources("vllm", "/logs/vllm.log", None)


def test_metrics_url_on_a_file_engine_refuses():
    # --metrics-url only means something for endpoint-scraped engines.
    with pytest.raises(run.CounterSourceError, match="endpoint-scraped"):
        run.resolve_counter_sources("ds4", "/logs/ds4.log", "http://127.0.0.1:8030")


def test_no_flags_at_all_is_no_sources():
    draft, prefill = run.resolve_counter_sources("ds4", None, None)
    assert draft is None
    assert prefill is None
