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

The #190 confound is pinned as well. The first run measured every size against
one server and one kv dir, so each reading inherited the previous size's cache
and the 29845 reading reused a 10240 entry written during the 11045
measurement. The fix is `--isolate` (default): a fresh server and a fresh kv
dir per size, so no cross-size reuse is possible. The reason is read out of the
log, not guessed from the number -- `cached_tokens` alone cannot tell a cold
checkpoint from a continued one.
"""

from __future__ import annotations

import pathlib
import shutil
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


# ------------------------------------------------------- the log, and the reason


def test_parse_log_extracts_store_and_hit_events():
    """The reason is read out of the log, not guessed from the number."""
    text = (
        "kv cache stored tokens=2048 trimmed=597 reason=cold key=token-text "
        "size=175.06 MiB save=1.2 ms\n"
        "kv cache stored tokens=10240 trimmed=0 reason=continued key=token-text "
        "size=421.23 MiB save=2.3 ms\n"
        "kv cache hit text tokens=10240 text=100 quant=8 key=token-text "
        "load=1.5 ms file=/tmp/kv/file1\n"
    )
    stores, hits = kpr.parse_log(text)
    assert [(s.reason, s.tokens) for s in stores] == [
        ("cold", 2048),
        ("continued", 10240),
    ]
    assert [(h.file, h.tokens) for h in hits] == [("/tmp/kv/file1", 10240)]


def test_parse_log_ignores_unrelated_lines():
    """Startup and request lines are not store or hit events."""
    text = (
        "ds4-server starting\n"
        "loading weights\n"
        "kv cache stored tokens=2048 trimmed=0 reason=cold key=token-text "
        "size=1.0 MiB save=1.0 ms\n"
        "some other line\n"
    )
    stores, hits = kpr.parse_log(text)
    assert len(stores) == 1 and stores[0].reason == "cold"
    assert hits == []


def test_measure_with_a_log_reader_attributes_events_to_requests(monkeypatch):
    """The log is read between the warm and reading requests, so the store is
    attributed to the warm request and the hit to the reading request."""

    class FakeReader:
        def __init__(self):
            self.reads = 0

        def mark(self):
            pass

        def read_since_mark(self):
            self.reads += 1
            if self.reads == 1:
                return (
                    "kv cache stored tokens=1000 trimmed=0 reason=cold "
                    "key=token-text size=1.0 MiB save=1.0 ms\n"
                )
            return (
                "kv cache hit text tokens=800 text=1 quant=8 key=token-text "
                "load=1.0 ms file=/tmp/kv/f\n"
            )

    monkeypatch.setattr(
        kpr,
        "post_chat",
        lambda *a: {
            "prompt_tokens": 1000,
            "cached_tokens": 800,
            "cache_write_tokens": 200,
        },
    )
    got = kpr.measure(1, "m", 10, FakeReader())
    assert got["warm_stores"][0].reason == "cold"
    assert got["reading_hits"][0].file == "/tmp/kv/f"
    assert got["cached_tokens"] == 800


def test_build_row_carries_the_reason_and_file():
    """A row must name the mechanism that produced its reuse, not just the
    number -- that is how the first #190 read-out published a wrong one."""
    got = {
        "prompt_tokens": 11045,
        "cached_tokens": 10240,
        "cache_write_tokens": 805,
        "reused_pct": 92.7,
        "reprefilled": 805,
        "reading_hits": [kpr.HitEvent(file="/tmp/kv/file1", tokens=10240)],
    }
    all_stores = [(200, "cold", 2048), (200, "continued", 10240)]
    row = kpr.build_row(200, got, all_stores)
    assert row["reason"] == "continued"
    assert row["file"] == "/tmp/kv/file1"
    assert row["cross_size"] is False


def test_build_row_flags_cross_size_reuse():
    """A hit whose file was written during a different size's measurement must
    be flagged in the row itself, not inferred later from timestamps."""
    got = {
        "prompt_tokens": 29845,
        "cached_tokens": 10240,
        "cache_write_tokens": 19605,
        "reused_pct": 34.3,
        "reprefilled": 19605,
        "reading_hits": [kpr.HitEvent(file="/tmp/kv/file1", tokens=10240)],
    }
    # The 29845 reading reused a 10240 entry written during the 11045
    # measurement -- the exact #190 confound. The most recent store is from a
    # different size, so cross_size is True.
    all_stores = [(200, "cold", 2048), (11045, "continued", 10240)]
    row = kpr.build_row(29845, got, all_stores)
    assert row["reason"] == "continued"
    assert row["cross_size"] is True


def test_build_row_matches_the_store_by_length_not_by_recency():
    """The reading request does not always reuse the most recent store.

    At 29,845 tokens the server stores 29,877 with reason=evict and then
    serves the request from a 10,240-token entry written nineteen thousand
    tokens of context earlier. Labelling the row `evict` said the hit was on
    the evict entry, which it was not -- and that label was published on #190
    before the log was read carefully.
    """
    got = {
        "prompt_tokens": 29845,
        "cached_tokens": 10240,
        "cache_write_tokens": 19605,
        "reused_pct": 34.3,
        "reprefilled": 19605,
        "reading_hits": [kpr.HitEvent(file="/tmp/kv/older", tokens=10240)],
    }
    all_stores = [
        (200, "cold", 2048),
        (800, "continued", 10240),
        (2000, "evict", 29877),  # most recent, and NOT what was hit
    ]
    row = kpr.build_row(2000, got, all_stores)
    assert row["reason"] == "continued"
    assert row["cross_size"] is True
    assert row["matched_store"] is True


def test_build_row_says_so_when_no_store_matches_the_hit():
    """A hit on an entry written before this run began has no store line to
    match. That is a fact about the row, not a licence to guess -- reporting
    the nearest store would invent a mechanism."""
    got = {
        "prompt_tokens": 29845,
        "cached_tokens": 20480,
        "cache_write_tokens": 9365,
        "reused_pct": 68.6,
        "reprefilled": 9365,
        "reading_hits": [kpr.HitEvent(file="/tmp/kv/stranger", tokens=20480)],
    }
    row = kpr.build_row(2000, got, [(200, "cold", 2048)])
    assert row["reason"] is None
    assert row["matched_store"] is False
    assert row["file"] == "/tmp/kv/stranger"


def test_build_row_prefers_the_most_recent_store_of_a_repeated_length():
    """A length can be stored more than once; the entry on disk came from the
    latest of them."""
    got = {
        "prompt_tokens": 11045,
        "cached_tokens": 10240,
        "cache_write_tokens": 805,
        "reused_pct": 92.7,
        "reprefilled": 805,
        "reading_hits": [kpr.HitEvent(file="/tmp/kv/f", tokens=10240)],
    }
    all_stores = [(800, "cold", 10240), (2000, "continued", 10240)]
    row = kpr.build_row(2000, got, all_stores)
    assert row["reason"] == "continued"
    assert row["cross_size"] is False


def test_build_row_with_no_hit_has_no_mechanism():
    """A 0% reading has no hit, so no reason and no file -- the honest answer
    for a cache that did not reuse anything."""
    got = {
        "prompt_tokens": 5000,
        "cached_tokens": 0,
        "cache_write_tokens": 5000,
        "reused_pct": 0.0,
        "reprefilled": 5000,
        "reading_hits": [],
    }
    row = kpr.build_row(200, got, [(200, "cold", 2048)])
    assert row["reason"] is None
    assert row["file"] is None
    assert row["cross_size"] is False


# ------------------------------------------------------- the isolation


def test_isolate_is_the_default_mode():
    """The default must be isolate, not sequential -- the first #190 run
    measured every size against one server and published the confound."""
    args = kpr.build_parser().parse_args(["--tree", "t", "--gguf", "g"])
    assert args.mode == "isolate"


def test_isolate_mode_has_no_cross_size_reuse():
    """In isolate mode each size has its own server and kv dir, so every hit's
    file was written during that same size's measurement. The row builder must
    never flag cross-size reuse when the store history is that size's own.

    This is the property #190 assumed and did not have. The same reading that
    is cross_size=True in sequential mode must be cross_size=False here.
    """
    got = {
        "prompt_tokens": 29845,
        "cached_tokens": 10240,
        "cache_write_tokens": 19605,
        "reused_pct": 34.3,
        "reprefilled": 19605,
        "reading_hits": [kpr.HitEvent(file="/tmp/kv/file1", tokens=10240)],
    }
    # Isolate mode: the store history is only this size's own stores.
    all_stores = [(800, "cold", 2048), (800, "continued", 10240)]
    row = kpr.build_row(800, got, all_stores)
    assert row["cross_size"] is False


def test_isolate_mode_uses_a_fresh_kv_dir_per_size():
    """A larger prompt must not share a kv dir with a smaller one, or it would
    reuse the smaller one's cache -- the #190 confound."""
    assert kpr.fresh_kv_dir(8099, 200) != kpr.fresh_kv_dir(8099, 800)
    assert kpr.fresh_kv_dir(8099, 200) == pathlib.Path("/tmp/kv-prefix-reuse-8099-200")


def test_wipe_kv_dir_empties_the_dir(tmp_path):
    """A fresh server must start against an empty kv dir, or the previous size's
    cache leaks into this one's measurement."""
    kv_dir = tmp_path / "kv"
    kv_dir.mkdir()
    (kv_dir / "stale").write_text("x")
    kpr.wipe_kv_dir(kv_dir)
    assert not (kv_dir / "stale").exists()
    assert kv_dir.is_dir()


def test_start_server_wipes_the_kv_dir(monkeypatch, tmp_path):
    """start_server must wipe the kv dir before the server starts. Removing
    that wipe is the mutation this test guards against."""
    wiped = []
    real_rmtree = shutil.rmtree
    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda p, **kw: wiped.append(str(p)) or real_rmtree(p, **kw),
    )
    monkeypatch.setattr(
        kpr.subprocess,
        "Popen",
        lambda *a, **kw: _FakeProc(),
    )
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "ds4-server").write_text("#!/bin/sh\nexit 0\n")
    (tree / "ds4-server").chmod(0o755)
    args = kpr.build_parser().parse_args(
        ["--tree", str(tree), "--gguf", "x", "--port", "8099"]
    )
    kv_dir = tmp_path / "kv"
    log_path = tmp_path / "server.log"
    proc, log, _reader = kpr.start_server(args, kv_dir, log_path)
    assert str(kv_dir) in wiped, "the kv dir must be wiped before the server starts"
    kpr.stop_server(proc, log)


class _FakeProc:
    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


DS4_TREES = ("ds4-ivan-qwen38fn", "ds4-main")


def _ds4_server_source():
    for name in DS4_TREES:
        p = pathlib.Path.home() / "git" / name / "ds4_server.c"
        if p.exists():
            return p
    return None


@pytest.mark.skipif(_ds4_server_source() is None, reason="no ds4 tree checked out")
def test_the_kv_cache_flags_this_script_passes_still_exist_in_the_engine():
    """A renamed engine flag would silently do nothing, not error.

    ds4-server parses its own argv; an unknown flag is not necessarily
    rejected, so a sweep could run to completion against the engine default
    and report it as the swept value. #190 measured the defaults these two
    flags override (cold_max_tokens 30000, continued_interval_tokens 10000),
    so a silent no-op here invalidates the follow-up rather than failing it.
    """
    src = _ds4_server_source().read_text()
    for flag in (
        "--kv-cache-cold-max-tokens",
        "--kv-cache-continued-interval-tokens",
        "--kv-disk-space-mb",
        "--kv-disk-dir",
    ):
        assert f'"{flag}"' in src, f"{flag} is no longer parsed by ds4-server"
