"""Tests for scripts/run_envelope.py (#1456): window, parsing, node mapping.

Offline: the fixtures in tests/fixtures/run_envelope/ are gcx JSON captured
for the window 2026-11-21T22:59:48Z to 23:11:13Z, with every host label
anonymized (`head-host`, `worker-host`, `other-N`). The hand-run envelope for
that window was: GPU temperature maximum 68 / 66, power average 25.8 / 26.8 W,
power peak 38.8 / 39.9 W, MemAvailable minimum 5.9 / 5.8 GiB (head / worker).
The live query is one `integration` test that skips without gcx and the host
names.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import dgx_hosts
import dgx_metrics
import run_envelope as env

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "run_envelope"
HOSTS = {"head": "head-host", "worker": "worker-host"}
SINCE = "2026-11-21T22:59:48Z"
UNTIL = "2026-11-21T23:11:13Z"
#: 2026-11-21T23:11:13Z as Unix seconds.
END = 1795302673
#: The readout the operator wrote by hand for this window.
HAND_RUN = [
    (
        "head: GPU temp max 68 C, power avg 25.8 W, peak 38.8 W, "
        "MemAvailable min 5.9 GiB"
    ),
    (
        "worker: GPU temp max 66 C, power avg 26.8 W, peak 39.9 W, "
        "MemAvailable min 5.8 GiB"
    ),
]


def _fixture(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


def _fixture_prom(promql: str) -> str:
    for name, template in env.QUERIES:
        if promql == template.format(w=685, end=END):
            return _fixture(name)
    raise AssertionError(f"unexpected query: {promql}")


# ---------------------------------------------------------------- window ----


def test_window_from_z_timestamps() -> None:
    assert env.window(SINCE, UNTIL) == (685, END)


def test_window_from_an_offset_timestamp() -> None:
    # The same instants, written in New York time.
    assert env.window("2026-11-21T17:59:48-05:00", "2026-11-21T18:11:13-05:00") == (
        685,
        END,
    )


def test_window_refuses_a_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="offset"):
        env.window("2026-11-21T22:59:48", UNTIL)


def test_window_refuses_an_empty_or_reversed_window() -> None:
    with pytest.raises(ValueError, match="after"):
        env.window(UNTIL, SINCE)
    with pytest.raises(ValueError, match="after"):
        env.window(UNTIL, UNTIL)


def test_window_refuses_garbage() -> None:
    with pytest.raises(ValueError):
        env.window("yesterday", UNTIL)


def test_promql_puts_the_window_and_end_in_place() -> None:
    got = dict(env.promql(685, END))
    assert got["gpu_temp_max"] == (
        "max_over_time(DCGM_FI_DEV_GPU_TEMP[685s] @ 1795302673)"
    )
    assert got["power_avg"] == (
        "avg_over_time(DCGM_FI_DEV_POWER_USAGE[685s] @ 1795302673)"
    )
    assert got["power_max"] == (
        "max_over_time(DCGM_FI_DEV_POWER_USAGE[685s] @ 1795302673)"
    )
    assert got["mem_avail_min"] == (
        "min_over_time(node_memory_MemAvailable_bytes[685s] @ 1795302673)"
    )


# --------------------------------------------------------------- parsing ----


def test_by_instance_reads_every_series() -> None:
    got = env.by_instance(_fixture("power_max"))
    assert got == {
        "worker-host:9400": 39.919,
        "head-host:9400": 38.758,
        "other-1:9400": 590.006,
    }


def test_by_instance_survives_a_failed_call() -> None:
    assert env.by_instance("") == {}
    assert env.by_instance("not json") == {}
    assert env.by_instance('{"data": {"result": [{"metric": {}}]}}') == {}


def test_by_instance_skips_a_bad_value() -> None:
    raw = json.dumps(
        {"data": {"result": [{"metric": {"instance": "a:1"}, "value": [0, "x"]}]}}
    )
    assert env.by_instance(raw) == {}


# --------------------------------------------------------------- mapping ----


def test_for_role_keeps_only_the_two_nodes() -> None:
    got = env.for_role(env.by_instance(_fixture("power_max")), HOSTS, 9400)
    assert got == {"head": 38.758, "worker": 39.919}


def test_for_role_drops_an_unknown_host() -> None:
    # other-1 draws 590 W: a different machine. It must not reach either node.
    got = env.for_role({"other-1:9400": 590.006}, HOSTS, 9400)
    assert got == {"head": None, "worker": None}


def test_for_role_matches_the_port() -> None:
    # The node-exporter series of the head must not answer a DCGM question.
    got = env.for_role({"head-host:9100": 1.0}, HOSTS, 9400)
    assert got == {"head": None, "worker": None}


def test_envelope_from_the_fixtures() -> None:
    got = env.envelope(SINCE, UNTIL, prom=_fixture_prom, hosts=HOSTS)
    head, worker = got
    assert head.role == "head"
    assert head.gpu_temp_max_c == 68.0
    assert head.power_avg_w == pytest.approx(25.79041605839416)
    assert head.power_max_w == 38.758
    assert head.mem_avail_min_gib == pytest.approx(6312071168 / 2**30)
    assert worker.role == "worker"
    assert worker.gpu_temp_max_c == 66.0
    assert worker.power_avg_w == pytest.approx(26.783693430656935)
    assert worker.power_max_w == 39.919
    assert worker.mem_avail_min_gib == pytest.approx(6250881024 / 2**30)


def test_lines_match_the_hand_run_readout() -> None:
    got = env.lines(env.envelope(SINCE, UNTIL, prom=_fixture_prom, hosts=HOSTS))
    assert got == HAND_RUN


def test_as_json_rounds_like_the_lines() -> None:
    got = env.as_json(
        env.envelope(SINCE, UNTIL, prom=_fixture_prom, hosts=HOSTS), SINCE, UNTIL
    )
    assert json.loads(got) == {
        "since": SINCE,
        "until": UNTIL,
        "window_s": 685,
        "nodes": {
            "head": {
                "gpu_temp_max_c": 68.0,
                "power_avg_w": 25.8,
                "power_max_w": 38.8,
                "mem_avail_min_gib": 5.9,
            },
            "worker": {
                "gpu_temp_max_c": 66.0,
                "power_avg_w": 26.8,
                "power_max_w": 39.9,
                "mem_avail_min_gib": 5.8,
            },
        },
    }


def test_a_missing_series_reads_n_a_not_zero() -> None:
    got = env.lines(env.envelope(SINCE, UNTIL, prom=lambda _q: "", hosts=HOSTS))
    assert got == [
        "head: GPU temp max n/a, power avg n/a, peak n/a, MemAvailable min n/a",
        "worker: GPU temp max n/a, power avg n/a, peak n/a, MemAvailable min n/a",
    ]


def test_main_fails_when_no_data_comes_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dgx_metrics, "query", lambda _q: "")
    monkeypatch.setenv("LOCAL_LLM_DGX_HEAD", "head-host")
    monkeypatch.setenv("LOCAL_LLM_DGX_WORKER", "worker-host")
    assert env.main(["--since", SINCE, "--until", UNTIL]) == 1


def test_main_logs_one_line_per_node(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(dgx_metrics, "query", _fixture_prom)
    monkeypatch.setenv("LOCAL_LLM_DGX_HEAD", "head-host")
    monkeypatch.setenv("LOCAL_LLM_DGX_WORKER", "worker-host")
    with caplog.at_level(logging.INFO):
        assert env.main(["--since", SINCE, "--until", UNTIL]) == 0
    assert "head: GPU temp max 68 C, power avg 25.8 W" in caplog.text
    assert "worker: GPU temp max 66 C, power avg 26.8 W" in caplog.text


def test_main_refuses_a_bad_window() -> None:
    with pytest.raises(SystemExit):
        env.main(["--since", UNTIL, "--until", SINCE])


# ------------------------------------------------------------------ live ----


def _live_ready() -> bool:
    if shutil.which("gcx") is None:
        return False
    try:
        dgx_hosts.lookup("head")
        dgx_hosts.lookup("worker")
    except dgx_hosts.HostUnknown:
        return False
    return True


@pytest.mark.integration
@pytest.mark.skipif(not _live_ready(), reason="needs gcx and the DGX host names")
def test_the_live_window_reproduces_the_hand_run() -> None:
    nodes = env.envelope(SINCE, UNTIL)
    if all(n.power_max_w is None for n in nodes):
        pytest.skip("gcx returned no power data (not authenticated?)")
    assert env.lines(nodes) == HAND_RUN
