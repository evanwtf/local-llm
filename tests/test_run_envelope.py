"""Tests for scripts/run_envelope.py (#1456): window, parsing, node mapping.

Offline: the fixtures in tests/fixtures/run_envelope/ are gcx JSON captured
for the window 2026-11-21T22:59:48Z to 23:11:13Z. Each series keeps only its
`instance` label, anonymized (`head-host`, `worker-host`, `other-N`). The
hand-run envelope for that window was: GPU temperature maximum 68 / 66, power
average 25.8 / 26.8 W, power peak 38.8 / 39.9 W, MemAvailable minimum 5.9 /
5.8 GiB (head / worker). The exact values are pinned here, on the fixtures.

The live test reads a recent window and checks only its shape: a pinned
window leaves Prometheus retention after about 90 days. It skips without gcx
and the host names.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import time
from collections.abc import Callable
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


def _vector(*series: tuple[str, str]) -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {"instance": i}, "value": [END, v]} for i, v in series
                ],
            },
        }
    )


def _prom(**overrides: str) -> Callable[[str], str]:
    """gcx answers from the fixtures, with some queries' answers replaced."""
    names = {q: name for name, q in env.promql(685, END)}

    def prom(promql: str) -> str:
        name = names[promql]
        return overrides[name] if name in overrides else _fixture(name)

    return prom


def _hosts_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_DGX_HEAD", "head-host")
    monkeypatch.setenv("LOCAL_LLM_DGX_WORKER", "worker-host")


# ---------------------------------------------------------------- window ----


def test_window_from_z_timestamps() -> None:
    assert env.window(SINCE, UNTIL) == (685, END)


def test_window_from_an_offset_timestamp() -> None:
    # The same instants, written in New York time.
    got = env.window("2026-11-21T17:59:48-05:00", "2026-11-21T18:11:13-05:00")
    assert got == (685, END)


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


def test_window_refuses_an_end_in_the_future() -> None:
    # 61 s past now is refused; 60 s (clock skew) is allowed.
    with pytest.raises(ValueError, match="future"):
        env.window(SINCE, UNTIL, now=END - 61)
    assert env.window(SINCE, UNTIL, now=END - 60) == (685, END)


def test_promql_aggregates_by_instance() -> None:
    got = dict(env.promql(685, END))
    a = "[685s] @ 1795302673"
    assert got == {
        "gpu_temp_max": f"max by (instance) (max_over_time(DCGM_FI_DEV_GPU_TEMP{a}))",
        "gpu_temp_count": (
            f"sum by (instance) (count_over_time(DCGM_FI_DEV_GPU_TEMP{a}))"
        ),
        "power_sum": f"sum by (instance) (sum_over_time(DCGM_FI_DEV_POWER_USAGE{a}))",
        "power_count": (
            f"sum by (instance) (count_over_time(DCGM_FI_DEV_POWER_USAGE{a}))"
        ),
        "power_max": f"max by (instance) (max_over_time(DCGM_FI_DEV_POWER_USAGE{a}))",
        "mem_min": (
            f"min by (instance) (min_over_time(node_memory_MemAvailable_bytes{a}))"
        ),
        "mem_count": (
            f"sum by (instance) (count_over_time(node_memory_MemAvailable_bytes{a}))"
        ),
    }


# --------------------------------------------------------------- parsing ----


def test_parse_reads_every_series() -> None:
    got = env.parse(_fixture("power_max"))
    assert got.ok
    assert got.series == 3
    assert got.values == {
        "worker-host:9400": 39.919,
        "head-host:9400": 38.758,
        "other-1:9400": 590.006,
    }


def test_parse_marks_a_failed_call() -> None:
    for raw in ("", "not json", "[]"):
        assert env.parse(raw) == env.Answer(ok=False, series=0, values={})


def test_parse_marks_an_error_status() -> None:
    raw = json.dumps({"status": "error", "data": {"result": []}})
    assert not env.parse(raw).ok


def test_parse_skips_a_bad_value_or_a_missing_instance() -> None:
    raw = json.dumps(
        {
            "status": "success",
            "data": {
                "result": [
                    {"metric": {"instance": "a:1"}, "value": [0, "x"]},
                    {"metric": {}, "value": [0, "1"]},
                ]
            },
        }
    )
    got = env.parse(raw)
    assert got.series == 2
    assert got.values == {}


def test_parse_keeps_the_larger_of_two_series_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = _vector(("head-host:9400", "38.1"), ("head-host:9400", "41.5"))
    with caplog.at_level(logging.WARNING):
        got = env.parse(raw)
    assert got.values == {"head-host:9400": 41.5}
    assert "two series for one instance" in caplog.text


# --------------------------------------------------------------- mapping ----


def test_for_role_keeps_only_the_two_nodes() -> None:
    got = env.for_role(env.parse(_fixture("power_max")).values, HOSTS, 9400)
    assert got == {"head": 38.758, "worker": 39.919}


def test_for_role_drops_an_unknown_host() -> None:
    # other-1 draws 590 W: a different machine. It must not reach either node.
    got = env.for_role({"other-1:9400": 590.006}, HOSTS, 9400)
    assert got == {"head": None, "worker": None}


def test_for_role_matches_the_port() -> None:
    # The node-exporter series of the head must not answer a DCGM question.
    got = env.for_role({"head-host:9100": 1.0}, HOSTS, 9400)
    assert got == {"head": None, "worker": None}


# -------------------------------------------------------------- envelope ----


def test_envelope_from_the_fixtures() -> None:
    got = env.envelope(SINCE, UNTIL, prom=_prom(), hosts=HOSTS)
    assert got.errors == []
    assert got.warnings == []
    head, worker = got.nodes
    assert head.role == "head"
    assert head.gpu_temp_max_c == 68.0
    assert head.power_avg_w == pytest.approx(3533.287 / 137)
    assert head.power_max_w == 38.758
    assert head.mem_avail_min_gib == pytest.approx(6312071168 / 2**30)
    assert head.dcgm_coverage == pytest.approx(1.0)  # 137 samples x 5 s / 685 s
    assert head.node_exporter_coverage == pytest.approx(690 / 685)  # 69 x 10 s
    assert worker.role == "worker"
    assert worker.gpu_temp_max_c == 66.0
    assert worker.power_avg_w == pytest.approx(3669.366 / 137)
    assert worker.power_max_w == 39.919
    assert worker.mem_avail_min_gib == pytest.approx(6250881024 / 2**30)


def test_lines_match_the_hand_run_readout() -> None:
    got = env.envelope(SINCE, UNTIL, prom=_prom(), hosts=HOSTS)
    assert env.lines(got.nodes) == HAND_RUN


def test_as_json_rounds_like_the_lines() -> None:
    got = env.envelope(SINCE, UNTIL, prom=_prom(), hosts=HOSTS)
    assert json.loads(env.as_json(got, SINCE, UNTIL)) == {
        "since": SINCE,
        "until": UNTIL,
        "window_s": 685,
        "nodes": {
            "head": {
                "gpu_temp_max_c": 68.0,
                "power_avg_w": 25.8,
                "power_max_w": 38.8,
                "mem_avail_min_gib": 5.9,
                "dcgm_coverage": 1.0,
                "node_exporter_coverage": 1.01,
            },
            "worker": {
                "gpu_temp_max_c": 66.0,
                "power_avg_w": 26.8,
                "power_max_w": 39.9,
                "mem_avail_min_gib": 5.8,
                "dcgm_coverage": 1.0,
                "node_exporter_coverage": 1.01,
            },
        },
        "errors": [],
        "warnings": [],
    }


def test_every_failed_call_reads_n_a_and_is_an_error() -> None:
    got = env.envelope(SINCE, UNTIL, prom=lambda _q: "", hosts=HOSTS)
    assert env.lines(got.nodes) == [
        "head: GPU temp max n/a, power avg n/a, peak n/a, MemAvailable min n/a",
        "worker: GPU temp max n/a, power avg n/a, peak n/a, MemAvailable min n/a",
    ]
    assert len(got.errors) == 8
    assert got.errors[0] == (
        "head: gpu_temp_max_c missing: gcx returned nothing or an error status"
    )


def test_one_failed_call_is_an_error_for_that_field_only() -> None:
    got = env.envelope(SINCE, UNTIL, prom=_prom(mem_min=""), hosts=HOSTS)
    assert got.errors == [
        "head: mem_avail_min_gib missing: gcx returned nothing or an error status",
        "worker: mem_avail_min_gib missing: gcx returned nothing or an error status",
    ]
    assert env.lines(got.nodes)[0].endswith("MemAvailable min n/a")


def test_one_node_down_is_an_error_for_that_node() -> None:
    # The worker's DCGM exporter is gone: only other hosts answer.
    gone = _vector(("head-host:9400", "38.758"), ("other-1:9400", "590.006"))
    got = env.envelope(SINCE, UNTIL, prom=_prom(power_max=gone), hosts=HOSTS)
    assert got.errors == [
        (
            "worker: power_max_w missing: 2 series came back, none at the "
            "configured worker host on port 9400 (check the host name in dgx_hosts)"
        )
    ]


def test_a_host_name_typo_names_no_host() -> None:
    hosts = {"head": "head-hots", "worker": "worker-host"}
    got = env.envelope(SINCE, UNTIL, prom=_prom(), hosts=hosts)
    assert len(got.errors) == 4
    assert all(e.startswith("head: ") for e in got.errors)
    assert "3 series came back, none at the configured head host" in got.errors[0]
    # The errors go to public issues: no instance name, real or configured.
    assert not any("other-" in e or "head-hots" in e for e in got.errors)


def test_an_empty_answer_says_no_series() -> None:
    got = env.envelope(SINCE, UNTIL, prom=_prom(power_max=_vector()), hosts=HOSTS)
    assert got.errors[0] == (
        "head: power_max_w missing: no series in the window "
        "(out of retention, or no exporter?)"
    )


def test_low_coverage_warns() -> None:
    # 100 samples x 5 s / 685 s = 73%: under 80%, not under 50%.
    low = _vector(("head-host:9400", "100"), ("worker-host:9400", "137"))
    got = env.envelope(SINCE, UNTIL, prom=_prom(gpu_temp_count=low), hosts=HOSTS)
    assert got.errors == []
    assert got.warnings == ["head: dcgm_coverage 73% is under 80%"]


def test_very_low_coverage_is_an_error() -> None:
    # 30 samples x 10 s / 685 s = 44%: under 50%.
    low = _vector(("head-host:9100", "69"), ("worker-host:9100", "30"))
    got = env.envelope(SINCE, UNTIL, prom=_prom(mem_count=low), hosts=HOSTS)
    assert got.warnings == []
    assert got.errors == ["worker: node_exporter_coverage 44% is under 50%"]


# ------------------------------------------------------------------ main ----


def test_main_logs_one_line_per_node(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(dgx_metrics, "query", _prom())
    _hosts_from_env(monkeypatch)
    with caplog.at_level(logging.INFO):
        assert env.main(["--since", SINCE, "--until", UNTIL]) == 0
    assert [r.getMessage() for r in caplog.records] == HAND_RUN


def test_main_prints_json(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(dgx_metrics, "query", _prom())
    _hosts_from_env(monkeypatch)
    with caplog.at_level(logging.INFO):
        assert env.main(["--since", SINCE, "--until", UNTIL, "--json"]) == 0
    (record,) = caplog.records
    got = json.loads(record.getMessage())
    assert got["nodes"]["worker"]["power_max_w"] == 39.9
    assert got["errors"] == []


def test_main_fails_on_a_partial_envelope(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(dgx_metrics, "query", _prom(power_sum=""))
    _hosts_from_env(monkeypatch)
    with caplog.at_level(logging.INFO):
        assert env.main(["--since", SINCE, "--until", UNTIL]) == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == [
        "error: head: power_avg_w missing: gcx returned nothing or an error status",
        "error: worker: power_avg_w missing: gcx returned nothing or an error status",
    ]


def test_main_fails_when_no_data_comes_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dgx_metrics, "query", lambda _q: "")
    _hosts_from_env(monkeypatch)
    assert env.main(["--since", SINCE, "--until", UNTIL]) == 1


def test_main_exits_2_when_a_host_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("LOCAL_LLM_DGX_HEAD", raising=False)
    monkeypatch.delenv("LOCAL_LLM_DGX_WORKER", raising=False)
    monkeypatch.setattr(dgx_hosts, "HOSTS_FILE", tmp_path / "absent.toml")
    monkeypatch.setattr(dgx_metrics, "query", _prom())
    with caplog.at_level(logging.INFO):
        assert env.main(["--since", SINCE, "--until", UNTIL]) == 2
    assert "LOCAL_LLM_DGX_HEAD" in caplog.text


def test_main_refuses_a_bad_window() -> None:
    with pytest.raises(SystemExit):
        env.main(["--since", UNTIL, "--until", SINCE])


def test_main_refuses_an_end_in_the_future() -> None:
    with pytest.raises(SystemExit):
        env.main(["--since", SINCE, "--until", UNTIL], now=END - 3600)


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
def test_a_recent_live_window_is_whole() -> None:
    import datetime as dt

    end = dt.datetime.fromtimestamp(time.time() - 120, dt.UTC)
    since = (end - dt.timedelta(minutes=10)).isoformat()
    got = env.envelope(since, end.isoformat())
    if all(n.power_max_w is None for n in got.nodes):
        pytest.skip("gcx returned no power data (not authenticated?)")
    assert [n.role for n in got.nodes] == ["head", "worker"]
    assert got.errors == []
