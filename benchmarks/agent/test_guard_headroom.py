"""The memory guards must not collide, and a dead server must stop the batch. #485

2026-09-17 23:18: 41 GiB available, a 24 GiB client cap, a 14 GiB server
floor. The client cap fired first, as designed, and the server watcher fired
12 s later anyway -- 41 - 24 leaves 17 GiB, 3 above the floor, and a tmpfs
/tmp plus page cache used that up. run.py then kept scoring trials against the
dead server. These tests pin both halves.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error

import pytest
import run

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))


def test_the_incident_numbers_are_refused():
    ok, headroom, why = run.headroom_verdict(41.0, 24.0, 14.0, run.HEADROOM_MARGIN_GIB)
    assert not ok
    assert headroom == pytest.approx(3.0)
    # The message names the knob to turn, not just the arithmetic.
    assert "LOCAL_LLM_CLIENT_MEM_CAP_GIB" in why
    assert "41.0" in why and "24" in why and "14" in why


def test_the_rerun_numbers_pass():
    """The same box re-run that night with a 20 GiB cap: 41 - 20 - 14 = 7."""
    ok, headroom, why = run.headroom_verdict(41.0, 20.0, 14.0, run.HEADROOM_MARGIN_GIB)
    assert ok and why == ""
    assert headroom == pytest.approx(7.0)


def test_exactly_the_margin_is_enough():
    ok, _, _ = run.headroom_verdict(42.0, 24.0, 14.0, run.HEADROOM_MARGIN_GIB)
    assert ok


def test_the_margin_is_more_than_the_incidents_headroom():
    """3 GiB of headroom collided on 2026-09-17. A margin at or below it
    would have let that exact run start."""
    assert run.HEADROOM_MARGIN_GIB > 3.0


def _args(gate=22.0, floor=14.0):
    return argparse.Namespace(memory_gate_gib=gate, server_floor_gib=floor)


def test_the_gate_refuses_before_the_trial(monkeypatch):
    monkeypatch.setattr(run, "CLIENT_MEM_CAP_GIB", 24.0)
    monkeypatch.setattr(run, "mem_available_gib", lambda: 41.0)
    with pytest.raises(SystemExit, match="#485"):
        run._headroom_gate(_args())


def test_the_gate_returns_the_headroom_for_the_row(monkeypatch):
    monkeypatch.setattr(run, "CLIENT_MEM_CAP_GIB", 6.0)
    monkeypatch.setattr(run, "mem_available_gib", lambda: 24.5)
    assert run._headroom_gate(_args()) == pytest.approx(4.5)


@pytest.mark.parametrize(
    "gate, floor, cap, avail",
    [
        (None, 14.0, 24.0, 1.0),  # no --memory-gate-gib: not a shared-pool run
        (22.0, 0.0, 24.0, 1.0),  # --server-floor-gib 0 disables it
        (22.0, 14.0, 0.0, 1.0),  # no client cap, nothing to budget
        (22.0, 14.0, 24.0, None),  # no /proc/meminfo (macOS)
    ],
)
def test_the_gate_stands_aside_when_it_does_not_apply(
    monkeypatch, gate, floor, cap, avail
):
    monkeypatch.setattr(run, "CLIENT_MEM_CAP_GIB", cap)
    monkeypatch.setattr(run, "mem_available_gib", lambda: avail)
    assert run._headroom_gate(_args(gate, floor)) is None


def test_the_floor_matches_the_server_watchers(monkeypatch):
    """Two copies of one number drift; this is the one the watcher enforces."""
    import dgx_server

    if "LOCAL_LLM_MEM_FLOOR_GIB" not in dgx_server.os.environ:
        assert run.SERVER_FLOOR_GIB == dgx_server.MEM_FLOOR_GIB
    assert (
        run.build_parser().parse_args(["--server-floor-gib", "0"]).server_floor_gib == 0
    )


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_an_http_error_is_still_a_live_server(monkeypatch):
    """A GET-blind shim answers 405. That is an answer."""

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 405, "no", None, None)

    monkeypatch.setattr(run.urllib.request, "urlopen", boom)
    assert run.backend_answers({"base_url": "http://127.0.0.1:1"}, wait=0)


def test_a_refused_connection_is_asked_again_then_called_dead(monkeypatch):
    calls = []

    def refused(*a, **k):
        calls.append(1)
        raise urllib.error.URLError(ConnectionRefusedError())

    monkeypatch.setattr(run.urllib.request, "urlopen", refused)
    assert not run.backend_answers({"base_url": "http://127.0.0.1:1"}, wait=0)
    assert len(calls) == 3


def test_one_slow_reply_is_not_a_death(monkeypatch):
    replies = iter([TimeoutError(), _Resp()])

    def flaky(*a, **k):
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(run.urllib.request, "urlopen", flaky)
    assert run.backend_answers({"base_url": "http://127.0.0.1:1"}, wait=0)


def _row(passed):
    return {
        "task": "mbox-scan",
        "backend": "b",
        "trial": 1,
        "passed": passed,
        "client_version": "1",
        "confinement": {"memory": "harness:8GiB"},
    }


def _written(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_failed_trial_against_a_dead_server_is_excluded_and_stops_the_batch(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(run, "backend_answers", lambda backend: False)
    out = tmp_path / "r.jsonl"
    with pytest.raises(SystemExit, match="stopped answering"):
        run.finish_row(_row(False), "b", {}, 3.5, out, dry_run=False)
    (row,) = _written(out)
    assert row["excluded"] is True
    assert "#485" in row["exclusion_reason"]
    assert row["confinement"]["headroom_gib"] == 3.5


def test_a_pass_before_the_server_died_is_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "backend_answers", lambda backend: False)
    out = tmp_path / "r.jsonl"
    with pytest.raises(SystemExit):
        run.finish_row(_row(True), "b", {}, None, out, dry_run=False)
    (row,) = _written(out)
    assert not row.get("excluded")
    assert "headroom_gib" not in row["confinement"]


def test_a_live_server_writes_the_row_and_carries_on(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "backend_answers", lambda backend: True)
    out = tmp_path / "r.jsonl"
    run.finish_row(_row(False), "b", {}, 7.0, out, dry_run=False)
    (row,) = _written(out)
    assert not row.get("excluded")


def test_a_dry_run_never_probes(monkeypatch, tmp_path):
    def probe(backend):
        raise AssertionError("probed on a dry run")

    monkeypatch.setattr(run, "backend_answers", probe)
    run.finish_row(_row(False), "b", {}, None, tmp_path / "r.jsonl", dry_run=True)
