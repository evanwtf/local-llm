"""#319: draftless speculation must still prove it drafted.

The #148/#151 guard exists because an arm that silently never speculated looks
exactly like one that did. n-gram speculation has no draft model, so the old
proof -- a draft engine writing counters to a file -- does not apply. These
tests pin the replacement: vLLM's own acceptance counters, scraped over HTTP.
"""

from __future__ import annotations

import run
import vllm_spec

# A trimmed scrape, in the exposition format vLLM actually serves: HELP and
# TYPE lines, labels carrying engine index and model name, float-rendered
# counters.
SCRAPE = """\
# HELP vllm:spec_decode_num_drafts_total Number of spec decoding drafts.
# TYPE vllm:spec_decode_num_drafts_total counter
vllm:spec_decode_num_drafts_total{engine="0",model_name="m"} 713.0
# HELP vllm:spec_decode_num_draft_tokens_total Number of draft tokens.
# TYPE vllm:spec_decode_num_draft_tokens_total counter
vllm:spec_decode_num_draft_tokens_total{engine="0",model_name="m"} 3556.0
# HELP vllm:spec_decode_num_accepted_tokens_total Number of accepted tokens.
# TYPE vllm:spec_decode_num_accepted_tokens_total counter
vllm:spec_decode_num_accepted_tokens_total{engine="0",model_name="m"} 1615.0
"""

NO_SPEC = """\
# HELP vllm:generation_tokens_total Number of generation tokens.
# TYPE vllm:generation_tokens_total counter
vllm:generation_tokens_total{engine="0",model_name="m"} 40.0
"""


def _scrape(monkeypatch, text):
    monkeypatch.setattr(vllm_spec, "scrape", lambda url, timeout=10.0: text)


def test_counters_parse_out_of_labelled_float_rendered_lines():
    got = vllm_spec.parse(SCRAPE)
    assert got == {"drafts": 713, "drafted": 3556, "accepted": 1615}


def test_a_server_with_no_speculative_config_exports_no_family():
    assert vllm_spec.present(SCRAPE)
    assert not vllm_spec.present(NO_SPEC)


def test_first_read_reports_nothing_then_deltas(monkeypatch):
    vllm_spec.reset()
    _scrape(monkeypatch, SCRAPE)
    first = vllm_spec.read_since("http://127.0.0.1:8030")
    assert first.counters.accepted == 0, "the first read is a baseline, not data"

    grown = SCRAPE.replace("1615.0", "1700.0").replace("3556.0", "3700.0")
    _scrape(monkeypatch, grown)
    second = vllm_spec.read_since("http://127.0.0.1:8030", first.offset)
    assert second.counters.accepted == 85
    assert second.counters.drafted == 144
    assert second.counters.used


def test_an_arm_that_accepted_nothing_is_still_caught(monkeypatch):
    """The whole point of the guard: drafting is not accepting."""
    vllm_spec.reset()
    _scrape(monkeypatch, SCRAPE)
    vllm_spec.read_since("http://127.0.0.1:8030")
    drafted_more = SCRAPE.replace("3556.0", "3700.0")  # accepted unchanged
    _scrape(monkeypatch, drafted_more)
    got = vllm_spec.read_since("http://127.0.0.1:8030").counters
    assert got.drafted == 144
    assert got.accepted == 0
    assert not got.used


def test_a_restarted_server_rebaselines_instead_of_going_negative(monkeypatch):
    vllm_spec.reset()
    _scrape(monkeypatch, SCRAPE)
    vllm_spec.read_since("http://127.0.0.1:8030")
    _scrape(monkeypatch, SCRAPE.replace("1615.0", "3.0").replace("3556.0", "9.0"))
    got = vllm_spec.read_since("http://127.0.0.1:8030").counters
    assert got.accepted == 0 and got.drafted == 0


def test_an_unreachable_server_does_not_take_a_run_down(monkeypatch):
    vllm_spec.reset()
    _scrape(monkeypatch, "")
    assert vllm_spec.read_since("http://127.0.0.1:8030").counters is vllm_spec.EMPTY


# --- the wiring into run.py ------------------------------------------------


def test_vllm_counters_need_no_switch_to_be_on():
    """ds4 and mtplx need an env var or an argv flag. vLLM exports the family
    whenever a speculative config is loaded, so searching for a flag that
    cannot exist would refuse every vLLM speculative arm forever."""
    assert run.counters_on("vllm", ps_text="")
    assert not run.counters_on("ds4", ps_text="")


def test_a_url_probed_engine_keeps_its_base_url_intact():
    """pathlib.Path collapses the double slash in a URL, which scrapes nothing
    and reads as a quiet engine rather than a broken probe."""
    probe = run.DraftProbe("http://127.0.0.1:8030", "vllm")
    assert probe.path == "http://127.0.0.1:8030"
    assert probe.source == "vllm-spec-metrics"
