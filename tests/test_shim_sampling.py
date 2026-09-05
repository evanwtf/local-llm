"""#148: an unrecorded temperature makes the MTP question unanswerable."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ds4_qwen_tool_shim as shim


def setup_function():
    shim._seen_sampling.clear()


def test_it_records_the_sampler_the_client_asked_for():
    got = shim.sampling_of({"temperature": 0.7, "top_p": 0.95, "messages": []})
    assert got == {"temperature": 0.7, "top_p": 0.95}


def test_it_never_carries_prompt_content():
    """Numbers only -- this is why it can be on by default and SHIM_DUMP cannot."""
    got = shim.sampling_of(
        {
            "temperature": 0,
            "messages": [{"role": "user", "content": "secret"}],
            "tools": [1],
        }
    )
    assert got == {"temperature": 0}
    assert "secret" not in json.dumps(got)


def test_a_repeated_sampler_logs_once(caplog):
    """136 requests a trial must not become 136 identical log lines."""
    with caplog.at_level("INFO", logger=shim.logger.name):
        for _ in range(5):
            shim.note_sampling({"temperature": 0.7})
    assert caplog.text.count("client sampling") == 1


def test_a_changed_sampler_logs_again(caplog):
    with caplog.at_level("INFO", logger=shim.logger.name):
        shim.note_sampling({"temperature": 0.7})
        shim.note_sampling({"temperature": 0.0})
    assert caplog.text.count("client sampling") == 2


def test_an_absent_sampler_is_recorded_as_absent_not_as_zero(caplog):
    """'the client said nothing' and 'the client said 0' argue for opposite
    conclusions about whether MTP could have run."""
    with caplog.at_level("INFO", logger=shim.logger.name):
        shim.note_sampling({"messages": []})
    assert "none specified" in caplog.text


def test_rewrite_records_sampling_even_when_it_does_not_instruct(caplog):
    """An uninstructed request still carries a sampler; recording only the
    instructed ones would miss most of a trial."""
    body = json.dumps({"temperature": 0.7, "messages": []}).encode()
    with caplog.at_level("INFO", logger=shim.logger.name):
        out = shim.rewrite(body)
    assert out == body, "no tools, so no instruction is added"
    assert "client sampling" in caplog.text
