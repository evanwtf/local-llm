"""The prefix-reuse measurement must not report its own warm-up (#190).

Two failure modes are worth an artifact, and both were made by hand on
2026-09-07 before this script existed:

* Reporting the FIRST request. On a cold cache it always reads
  `cached_tokens = 0`, so every configuration looks broken.
* Reporting a run with no `--kv-disk-dir`. That also reads zero at every size,
  and it is a different fact -- an unconfigured cache, not a failing one.

The prompt builder is pinned too. A cache hit depends on the prefix being
byte-identical between the two requests, so a builder that varied by
machine or by call would silently measure a cold prefill twice and report
0% reuse as a finding.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import kv_prefix_reuse as kpr


def test_the_prompt_is_identical_across_calls():
    """The whole measurement rests on the second request matching the first."""
    assert kpr.build_prompt(50) == kpr.build_prompt(50)


def test_the_prompt_grows_with_the_size_knob():
    assert len(kpr.build_prompt(400)) > len(kpr.build_prompt(200))


def test_the_prompt_has_a_shared_prefix_across_sizes():
    """Sizes are measured against one server, so a larger prompt extends the
    smaller one's prefix rather than replacing it. If that stopped being true
    the sizes would stop being comparable."""
    small, large = kpr.build_prompt(100), kpr.build_prompt(300)
    head = small.split("\n\nHow many")[0]
    assert large.startswith(head)


def test_measure_reports_the_second_request_not_the_first(monkeypatch):
    """A cold first request reads 0; reporting it would call every arm broken."""
    seen = []

    def fake_post(port, model, prompt):
        seen.append(prompt)
        first = len(seen) == 1
        return {
            "prompt_tokens": 1000,
            "cached_tokens": 0 if first else 800,
            "cache_write_tokens": 1000 if first else 200,
        }

    monkeypatch.setattr(kpr, "post_chat", fake_post)
    got = kpr.measure(1, "m", 10)
    assert len(seen) == 2, "measure must warm the cache before reading it"
    assert seen[0] == seen[1], "the two requests must be byte-identical"
    assert got["cached_tokens"] == 800
    assert got["reused_pct"] == 80.0
    assert got["reprefilled"] == 200


def test_zero_reuse_is_reported_as_zero_not_hidden(monkeypatch):
    """A genuine 0% must survive to the output -- it is the finding."""
    monkeypatch.setattr(
        kpr,
        "post_chat",
        lambda *a: {
            "prompt_tokens": 5000,
            "cached_tokens": 0,
            "cache_write_tokens": 5000,
        },
    )
    got = kpr.measure(1, "m", 10)
    assert got["reused_pct"] == 0.0
    assert got["reprefilled"] == 5000


def test_a_zero_length_prompt_does_not_divide_by_zero(monkeypatch):
    monkeypatch.setattr(lambda: None, "__doc__", None, raising=False) if False else None
    monkeypatch.setattr(
        kpr,
        "post_chat",
        lambda *a: {"prompt_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0},
    )
    assert kpr.measure(1, "m", 0)["reused_pct"] == 0.0


@pytest.mark.parametrize("n", [1, 10, 1000])
def test_build_prompt_is_deterministic_for_every_size(n):
    assert kpr.build_prompt(n) == kpr.build_prompt(n)


def test_readiness_does_not_depend_on_a_health_endpoint():
    """The bug that cost the first #190 batch, pinned.

    ds4-server has no /health -- it 404s. urllib.request.urlopen *raises*
    HTTPError on a 404, and HTTPError subclasses URLError, so a handler that
    treats URLError as "not ready yet" loops the full timeout beside a server
    that is already serving. The batch sat at 0% GPU for the whole window.

    The hand smoke-test before that run used curl, which exits 0 on a 404, so
    it reported READY. That is why this is a test and not a docstring.
    """
    src = (
        pathlib.Path(__file__).resolve().parents[1] / "scripts" / "kv_prefix_reuse.py"
    ).read_text()
    body = src.split("def wait_ready", 1)[1].split("\ndef ", 1)[0]
    code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    code = code.split('"""')[0] + code.split('"""')[-1]
    assert "/health" not in code, "readiness must not gate on /health; it 404s"
    assert "_wait_ready.ready(" in code, (
        "use benchmarks/agent/wait_ready.ready() -- it probes with a real "
        "one-token completion, which is the only readiness signal ds4 gives"
    )
