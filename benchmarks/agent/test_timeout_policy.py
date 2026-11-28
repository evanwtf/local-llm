"""Tests for the timeout policies (#366).

Fast and offline: the watchdog takes an injectable clock and a scripted watts
sampler, the circuit-breaker is a pure function, and the subprocess tests spawn
short-lived Python children rather than any real client or nvidia-smi. Nothing
here reads a GPU.
"""

from __future__ import annotations

import logging
import pathlib
import sys
import tomllib

import pytest
import results
import timeout_policy as tp
from timeout_policy import (
    IDLE_FLOOR_WATTS,
    TIER_IDLE_FLOOR_WATTS,
    ClientResult,
    IdleStallWatchdog,
    cell_should_abort,
    resolve_idle_floor,
    run_client_with_watchdog,
)


class _Clock:
    """A monotonic stand-in that yields scripted times and holds at the last."""

    def __init__(self, times: list[float]) -> None:
        self._times = times
        self._i = 0

    def __call__(self) -> float:
        t = self._times[min(self._i, len(self._times) - 1)]
        self._i += 1
        return t


class _Now:
    """A clock the test sets by hand, however many times the code reads it."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _Sampler:
    """A watts sampler that yields a scripted sequence and holds at the last."""

    def __init__(self, values: list[float | None]) -> None:
        self._values = values
        self._i = 0

    def __call__(self) -> float | None:
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


# --- (B) cell_should_abort --------------------------------------------------


def test_below_min_fraction_never_aborts_even_at_100pct_timeouts():
    # 2/10 done is under ceil(0.25*10)=3, so even all-timeouts holds.
    abort, reason = cell_should_abort(2, 10, 2)
    assert abort is False
    assert "not enough data" in reason


def test_judged_cell_with_majority_timeouts_aborts():
    # 6/10 done, 4 timeouts -> 66% > 50% -> abort.
    abort, reason = cell_should_abort(6, 10, 4)
    assert abort is True
    assert "abort" in reason


def test_exactly_the_timeout_fraction_does_not_abort():
    # 8 done, 4 timeouts -> exactly 50%, which is not strictly greater.
    abort, _ = cell_should_abort(8, 10, 4)
    assert abort is False


def test_total_not_positive_is_safe():
    for total in (0, -1):
        abort, reason = cell_should_abort(5, total, 5)
        assert abort is False
        assert "not enough data" in reason


def test_zero_trials_done_is_safe():
    abort, reason = cell_should_abort(0, 10, 0)
    assert abort is False
    assert "not enough data" in reason


def test_ceil_boundary_is_explicit():
    # ceil(0.25 * 10) == 3: 2 done never judges, 3 done does.
    below, _ = cell_should_abort(2, 10, 2)  # 100% timeouts, but under threshold
    assert below is False
    at, reason = cell_should_abort(3, 10, 2)  # 2/3 = 66% > 50%, just judged
    assert at is True
    assert "abort" in reason


# --- (A) IdleStallWatchdog --------------------------------------------------


def test_sustained_idle_reaches_the_window_and_stalls():
    clock = _Now()
    wd = IdleStallWatchdog(
        _Sampler([5.0]),  # always idle
        idle_floor_watts=20.0,
        idle_stall_secs=600.0,
        monotonic=clock,
    )
    wd.sample()  # t=0
    assert wd.stalled() is False
    clock.t = 300.0
    wd.sample()
    assert wd.stalled() is False
    clock.t = 600.0
    wd.sample()  # 600s continuous idle
    assert wd.stalled() is True


def test_a_busy_spike_mid_window_resets_and_prevents_stall():
    clock = _Clock([0.0, 0.0, 300.0, 600.0, 900.0, 1200.0])
    wd = IdleStallWatchdog(
        _Sampler([5.0, 5.0, 50.0, 5.0, 5.0]),  # idle, idle, BUSY, idle, idle
        idle_floor_watts=20.0,
        idle_stall_secs=600.0,
        monotonic=clock,
    )
    for _ in range(5):
        wd.sample()
        # The busy sample at t=600 resets the stretch, so the two later idle
        # samples only accumulate 300s -- never the full window.
        assert wd.stalled() is False


def test_all_busy_never_stalls():
    clock = _Clock([0.0, 100.0, 5000.0, 10000.0])
    wd = IdleStallWatchdog(
        _Sampler([80.0]),  # always working
        idle_floor_watts=20.0,
        idle_stall_secs=600.0,
        monotonic=clock,
    )
    for _ in range(4):
        wd.sample()
        assert wd.stalled() is False


def test_none_samples_do_not_count_as_idle_and_do_not_stall():
    # All-None: the idle stretch never even begins.
    wd = IdleStallWatchdog(
        _Sampler([None]),
        idle_floor_watts=20.0,
        idle_stall_secs=600.0,
        monotonic=_Clock([0.0]),
    )
    for _ in range(5):
        wd.sample()
        assert wd.stalled() is False


def test_a_none_after_idle_holds_and_does_not_fabricate_a_stall():
    # One idle reading starts the stretch; a long run of unreadable (None)
    # samples must not let it grow -- an unreadable GPU is held, not idle.
    wd = IdleStallWatchdog(
        _Sampler([5.0, None, None, None, None]),
        idle_floor_watts=20.0,
        idle_stall_secs=600.0,
        # Only the first (idle) sample reads the clock; the Nones never do, so
        # the idle duration is frozen at 0s no matter how much real time passes.
        monotonic=_Clock([0.0, 0.0]),
    )
    for _ in range(5):
        wd.sample()
        assert wd.stalled() is False
    assert wd.idle_seconds() == 0.0


def test_an_unreadable_gap_between_idle_readings_is_not_counted_as_idle():
    """10 W, then unreadable through a stretch of real GPU work, then 10 W
    601 s later. The gap is unknown, not idle: counting it fabricated a
    601 s stall and killed a run that may have been working."""
    clock = _Now()
    wd = IdleStallWatchdog(
        _Sampler([10.0, None, 10.0]),
        idle_floor_watts=20.0,
        idle_stall_secs=600.0,
        monotonic=clock,
    )
    wd.sample()  # t=0, idle
    clock.t = 300.0
    wd.sample()  # unreadable, while the GPU worked
    clock.t = 601.0
    wd.sample()  # idle again
    assert wd.idle_seconds() == 0.0
    assert wd.stalled() is False


def test_idle_seen_on_both_sides_of_a_gap_still_adds_up():
    """Held, not erased: the readable idle time before and after a gap counts,
    the gap itself does not. 300 s + (gap) + 300 s is a 600 s stall."""
    clock = _Now()
    wd = IdleStallWatchdog(
        _Sampler([5.0, 5.0, None, 5.0, 5.0]),
        idle_floor_watts=20.0,
        idle_stall_secs=600.0,
        monotonic=clock,
    )
    for t in (0.0, 300.0, 600.0, 1000.0):  # idle, idle, unreadable, idle
        clock.t = t
        wd.sample()
    assert wd.idle_seconds() == 300.0
    assert wd.stalled() is False
    clock.t = 1300.0
    wd.sample()
    assert wd.idle_seconds() == 600.0
    assert wd.stalled() is True


# --- (A) run_client_with_watchdog -------------------------------------------


def _live_children(pid: int) -> list[int]:
    """Pids whose parent is `pid`, read from /proc. Empty means none leaked."""
    import pathlib

    kids: list[int] = []
    proc = pathlib.Path("/proc")
    if not proc.exists():  # not Linux; the returncode assertion covers reaping
        return kids
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text()
        except OSError:
            continue
        for line in status.splitlines():
            if line.startswith("PPid:") and int(line.split()[1]) == pid:
                kids.append(int(entry.name))
    return kids


def test_an_idle_child_is_group_killed_and_flagged_stalled():
    wd = IdleStallWatchdog(
        _Sampler([5.0]),  # always below the floor -> idle
        idle_floor_watts=20.0,
        idle_stall_secs=0.0,  # any idle sample stalls immediately
        poll_secs=0.05,
    )
    result = run_client_with_watchdog(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=None,
        env=None,
        timeout=30.0,
        watchdog=wd,
        poll_secs=0.05,
        grace_secs=2.0,
    )
    assert isinstance(result, ClientResult)
    assert result.idle_stalled is True
    assert result.timed_out is False
    # Reaped, so not leaked: a killed-but-unwaited child would leave this None.
    assert result.returncode is not None
    # The 30s sleeper cannot have exited on its own in a sub-second test.
    assert result.returncode != 0


def test_a_fast_child_returns_cleanly_and_is_not_flagged():
    wd = IdleStallWatchdog(
        _Sampler([5.0]),  # idle, but the child exits before any stall
        idle_floor_watts=20.0,
        idle_stall_secs=600.0,
        poll_secs=5.0,
    )
    result = run_client_with_watchdog(
        [sys.executable, "-c", "print('ok')"],
        cwd=None,
        env=None,
        timeout=30.0,
        watchdog=wd,
        poll_secs=5.0,
    )
    assert result.returncode == 0
    assert result.idle_stalled is False
    assert result.timed_out is False
    assert "ok" in result.stdout


def test_no_child_is_left_running_after_a_stall_kill():
    import os

    wd = IdleStallWatchdog(
        _Sampler([5.0]),
        idle_floor_watts=20.0,
        idle_stall_secs=0.0,
        poll_secs=0.05,
    )
    run_client_with_watchdog(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=None,
        env=None,
        timeout=30.0,
        watchdog=wd,
        poll_secs=0.05,
        grace_secs=2.0,
    )
    assert _live_children(os.getpid()) == []


# --- schema: timeout_reason is additive and optional ------------------------


def _verdict_row(**over):
    row = results.new_row(
        task="mbox-scan",
        backend="ds4anthropic",
        client="opencode",
        trial=1,
        model="deepseek-v4-flash",
        context_tokens=100000,
        effort=None,
        env={"machine": "GB10"},
    )
    row.update(
        finished="2026-09-13T12:00:00",
        passed=False,
        wall_seconds=1800.0,
        pytest="1 failed",
        touched_tests=False,
        source_repo_intact=True,
        control_fails_as_expected=True,
    )
    row.update(over)
    return row


def test_new_row_carries_a_null_timeout_reason():
    assert _verdict_row()["timeout_reason"] is None


def test_a_row_with_a_timeout_reason_validates():
    row = _verdict_row(error="timeout", timeout_reason="gpu-idle-stall")
    # `error` present means no verdict is required; the field itself must pass.
    assert "timeout_reason" not in "; ".join(results.validate(row))


def test_a_row_without_a_timeout_reason_still_validates():
    row = _verdict_row()
    del row["timeout_reason"]
    assert results.validate(row) == []


def test_a_wrongly_typed_timeout_reason_is_a_violation():
    row = _verdict_row(error="timeout", timeout_reason=123)
    assert any("timeout_reason" in e for e in results.validate(row))


def test_a_child_that_outlives_its_deadline_is_timed_out():
    """The deadline was checked only after a poll timed out, so a child that
    exited inside the poll window after its deadline returned as a clean run.
    The watched path must time out where the plain path would."""
    wd = IdleStallWatchdog(
        _Sampler([80.0]),  # busy: only the wall clock can end this run
        idle_floor_watts=20.0,
        idle_stall_secs=600.0,
    )
    result = run_client_with_watchdog(
        [sys.executable, "-c", "import time; time.sleep(1.0)"],
        cwd=None,
        env=None,
        timeout=0.05,
        watchdog=wd,
        poll_secs=5.0,
        grace_secs=2.0,
    )
    assert result.timed_out is True
    assert result.idle_stalled is False


# --- the idle floor per tier (#1450) ------------------------------------------
#
# On GB10 a light model decodes at 15.8-17.3 W per GPU, under the 20 W global
# floor, so the watchdog killed a live trial (#1450). The floor now resolves
# flag > backend key > tier default > global default.


def test_an_explicit_flag_wins_over_backend_and_tier() -> None:
    backend = {"tier": "gb10-spark-x2", "idle_floor_watts": 14.0}
    assert resolve_idle_floor(25.0, backend) == (25.0, "flag")


def test_a_backend_key_wins_over_the_tier_default() -> None:
    backend = {"tier": "gb10-spark-x2", "idle_floor_watts": 14.0}
    assert resolve_idle_floor(None, backend) == (14.0, "backend")


def test_an_integer_backend_key_is_accepted() -> None:
    # TOML writes `idle_floor_watts = 14` as an int.
    assert resolve_idle_floor(None, {"idle_floor_watts": 14}) == (14.0, "backend")


@pytest.mark.parametrize("tier", ["gb10-spark", "gb10-spark-x2"])
def test_a_gb10_tier_gets_13_watts(tier: str) -> None:
    assert resolve_idle_floor(None, {"tier": tier}) == (13.0, "tier")


def test_an_unmapped_tier_keeps_the_global_default() -> None:
    assert resolve_idle_floor(None, {"tier": "desktop-3080ti"}) == (
        IDLE_FLOOR_WATTS,
        "default",
    )


def test_a_backend_without_a_tier_keeps_the_global_default() -> None:
    assert resolve_idle_floor(None, {}) == (IDLE_FLOOR_WATTS, "default")


def test_the_global_default_is_still_20_watts() -> None:
    # Non-GB10 tiers must not change behavior.
    assert IDLE_FLOOR_WATTS == 20.0


@pytest.mark.parametrize("bad", ["12", True, 0, -3.0, float("nan"), float("inf")])
def test_a_bad_backend_key_is_refused(bad: object) -> None:
    with pytest.raises(ValueError, match="idle_floor_watts"):
        resolve_idle_floor(None, {"idle_floor_watts": bad})


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_a_bad_flag_is_refused(bad: float) -> None:
    # NaN reads every sample as idle and kills each live trial at 600 s; a
    # floor <= 0 never sees idle and switches the watchdog off.
    with pytest.raises(ValueError, match="--idle-floor-watts"):
        resolve_idle_floor(bad, {"tier": "gb10-spark-x2"})


def test_the_gb10_floor_sits_between_loaded_idle_and_light_decode() -> None:
    """#1450. The cluster watchdog reads the busier node, and the worker idles
    with a TP2 model resident at a 10-min peak median of up to 12.2 W
    (60 days of DCGM). Inkling 2 Mini decodes at 15.8-17.3 W per GPU
    (2026-11-21). The floor must sit strictly between, or the watchdog either
    misses real stalls (at or under idle) or kills a live trial (above
    decode)."""
    for tier in ("gb10-spark", "gb10-spark-x2"):
        assert 12.2 < TIER_IDLE_FLOOR_WATTS[tier] < 15.8, tier
        assert TIER_IDLE_FLOOR_WATTS[tier] == 13.0, tier


def test_a_floor_given_without_a_source_does_not_claim_the_default() -> None:
    assert IdleStallWatchdog(lambda: 1.0).idle_floor_source == "default"
    given = IdleStallWatchdog(lambda: 1.0, idle_floor_watts=13.0)
    assert given.idle_floor_source == "caller"


def test_every_gb10_tier_in_tasks_toml_has_a_floor() -> None:
    """A new GB10 tier name must not fall back silently to the 20 W floor."""
    cfg = tomllib.loads((pathlib.Path(__file__).parent / "tasks.toml").read_text())
    tiers = {b.get("tier") for b in cfg["backend"].values()}
    gb10 = {t for t in tiers if isinstance(t, str) and t.startswith("gb10")}
    assert gb10, "no GB10 tier in tasks.toml: the test reads the wrong file"
    assert gb10 <= set(TIER_IDLE_FLOOR_WATTS), gb10 - set(TIER_IDLE_FLOOR_WATTS)


def test_every_backend_idle_floor_key_in_tasks_toml_resolves() -> None:
    cfg = tomllib.loads((pathlib.Path(__file__).parent / "tasks.toml").read_text())
    for backend in cfg["backend"].values():
        resolve_idle_floor(None, backend)


def test_a_row_with_an_idle_floor_validates() -> None:
    row = _verdict_row(idle_floor_watts=12.0)
    assert results.validate(row) == []


def test_a_wrongly_typed_idle_floor_is_a_violation() -> None:
    row = _verdict_row(idle_floor_watts="12")
    assert any("idle_floor_watts" in e for e in results.validate(row))


def test_the_stall_log_names_the_floor_and_its_source(caplog) -> None:
    clock = _Now()
    wd = IdleStallWatchdog(
        _Sampler([5.0]),
        idle_floor_watts=13.0,
        idle_floor_source="tier",
        idle_stall_secs=0.0,
        monotonic=clock,
    )
    log = logging.getLogger("test_idle_floor_log")
    with caplog.at_level(logging.ERROR, logger="test_idle_floor_log"):
        result = run_client_with_watchdog(
            [sys.executable, "-c", "import time; time.sleep(5.0)"],
            cwd=None,
            env=None,
            timeout=30.0,
            watchdog=wd,
            poll_secs=0.01,
            grace_secs=2.0,
            log=log,
        )
    assert result.idle_stalled is True
    assert "13 W (tier)" in caplog.text


def test_sample_watts_shows_the_tier_floor(monkeypatch, caplog) -> None:
    import timeout_policy

    monkeypatch.setattr(timeout_policy, "gpu_watts", lambda: 16.4)
    with caplog.at_level(logging.INFO):
        assert timeout_policy._sample_watts("gb10-spark-x2") == 0
    assert "idle floor 13 W, from tier" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO):
        timeout_policy._sample_watts(None)
    assert "idle floor 20 W, from default" in caplog.text


# --- #1453: the engine's running-request signal ----------------------------
#
# The power floor alone cannot tell a stuck client from a light decode on
# GB10: the worker idles with a two-node model resident at 11.5-12.0 W, and
# Inkling 2 Mini decodes at 15.8-17.3 W (#1450). Where the engine says whether
# it serves a request, a sample is idle only when the engine is idle AND the
# GPU reads under the engine-rule floor (20 W). Elsewhere the power rule stays.

# Lines in the engines' own /metrics format. The vLLM names and labels match
# what Prometheus stored from a vLLM server (engine="0", model_name=...); the
# llama.cpp and SGLang lines are from live /metrics captures (llama.cpp b10809,
# SGLang nightly 2026-09-21).
VLLM_BUSY = """\
# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{engine="0",model_name="inkling-2-mini"} 1.0
# TYPE vllm:generation_tokens_total counter
vllm:generation_tokens_total{engine="0",model_name="inkling-2-mini"} 111129.0
vllm:generation_tokens_created{engine="0",model_name="inkling-2-mini"} 1.7e9
"""
VLLM_IDLE = VLLM_BUSY.replace("} 1.0\n", "} 0.0\n", 1)
LLAMACPP = """\
# HELP llamacpp:tokens_predicted_total Number of generation tokens processed
# TYPE llamacpp:tokens_predicted_total counter
llamacpp:tokens_predicted_total 224
# HELP llamacpp:requests_processing Number of requests processing
# TYPE llamacpp:requests_processing gauge
llamacpp:requests_processing 0
"""
SGLANG = """\
# TYPE sglang:generation_tokens_total counter
sglang:generation_tokens_total{engine_type="unified",is_streaming="false",model_name="MiMo-v2.6-Flash"} 792.0
sglang:generation_tokens_total{engine_type="unified",is_streaming="true",model_name="MiMo-v2.6-Flash"} 146347.0
# TYPE sglang:num_running_reqs gauge
sglang:num_running_reqs{engine_type="unified",model_name="MiMo-v2.6-Flash",moe_ep_rank="0",pp_rank="0",tp_rank="0"} 1.0
"""


def test_parse_reads_the_vllm_gauge_and_counter() -> None:
    sig = tp.ENGINE_SIGNALS["vllm"]
    assert tp.parse_engine_metrics(VLLM_BUSY, sig) == tp.EngineReading(1.0, 111129.0)
    assert tp.parse_engine_metrics(VLLM_IDLE, sig) == tp.EngineReading(0.0, 111129.0)


def test_parse_reads_llamacpp_and_sglang() -> None:
    got = tp.parse_engine_metrics(LLAMACPP, tp.ENGINE_SIGNALS["llama.cpp"])
    assert got == tp.EngineReading(0.0, 224.0)
    got = tp.parse_engine_metrics(SGLANG, tp.ENGINE_SIGNALS["sglang"])
    assert got == tp.EngineReading(1.0, 792.0 + 146347.0)


def test_a_body_without_the_gauge_is_unreadable() -> None:
    # A llama-server without --metrics, a shim, or another engine's body: no
    # running gauge means no engine verdict, never "0 running".
    assert tp.parse_engine_metrics(LLAMACPP, tp.ENGINE_SIGNALS["vllm"]) is None
    assert tp.parse_engine_metrics("", tp.ENGINE_SIGNALS["vllm"]) is None


def test_a_body_without_the_counter_is_unreadable() -> None:
    """The gauge alone cannot show a short request between two reads, so a
    body without the counter falls back to the power rule (#1545 review)."""
    body = 'vllm:num_requests_running{engine="0"} 0.0\n'
    assert tp.parse_engine_metrics(body, tp.ENGINE_SIGNALS["vllm"]) is None
    body = "tensorfold:requests_running 0\n"
    assert tp.parse_engine_metrics(body, tp.ENGINE_SIGNALS["tensorfold"]) is None


def test_a_gauge_only_body_never_kills_what_power_keeps() -> None:
    """At 14 W, over the 13 W power floor, a gauge-only body reading 0 must
    not stop the trial: each sample is judged by power, and 14 W is work."""
    reading = tp.parse_engine_metrics(
        "tensorfold:requests_running 0\n", tp.ENGINE_SIGNALS["tensorfold"]
    )
    clock = _Now()
    wd = IdleStallWatchdog(
        _Sampler([14.0]),
        idle_floor_watts=13.0,
        engine=lambda: reading,
        engine_floor_watts=20.0,
        idle_stall_secs=600.0,
        monotonic=clock,
    )
    _run(wd, clock, 100)
    assert wd.stalled() is False


def test_the_metrics_url_is_the_engine_root() -> None:
    assert (
        tp.engine_metrics_url({"base_url": "http://srv:8888/v1"})
        == "http://srv:8888/metrics"
    )
    # A shim-fronted backend: the engine's own port, not the shim's.
    backend = {"base_url": "http://srv:8100", "engine_url": "http://srv:8000"}
    assert tp.engine_metrics_url(backend) == "http://srv:8000/metrics"
    assert tp.engine_metrics_url({}) is None


@pytest.mark.parametrize("engine", ["vllm", "sglang", "llama.cpp"])
def test_an_engine_with_a_running_gauge_gets_the_engine_rule(engine: str) -> None:
    backend = {"engine": engine, "base_url": "http://srv:8888"}
    signal, why = tp.engine_signal_for(backend, name="b")
    assert signal is tp.ENGINE_SIGNALS[engine]
    assert engine in why


@pytest.mark.parametrize("engine", ["ds4", "ollama", "mlx-serve", ""])
def test_other_engines_keep_the_power_rule(engine: str) -> None:
    signal, why = tp.engine_signal_for(
        {"engine": engine, "base_url": "http://s:1"}, name="b"
    )
    assert signal is None
    assert "power" in why


# --- #1545: native TensorFold by its running-request gauge -----------------
#
# Real-format bodies. TF_V06 is an excerpt of the live capture
# llm-metrics-exporter/testdata/tensorfold/v0.6.0-miaai-glm-v1.8.metrics.txt
# (TensorFold v0.6.0, MiaAI-Lab GLM-5.3 recipe v1.8, 2026-10-06), lines
# unchanged. TF_NATIVE_DECODE is the native 1.0.x server's own series as
# Prometheus stored them at 2026-10-14T07:01:15Z and 07:01:30Z: one request
# running, its in-flight tokens 843 -> 3253, and generation_tokens_total
# flat at 457988, because that counter counts finished requests only.
# TF_PATCH0150 is an excerpt of the live capture
# testdata/tensorfold/v0.3.4-glm53-e9c8cbb.metrics.txt (2026-09-29).
TF_V06 = """\
# HELP tensorfold:requests_running Requests in prefill or decode.
# TYPE tensorfold:requests_running gauge
tensorfold:requests_running 1
# HELP tensorfold:requests_waiting Requests queued or held until a lane is free.
# TYPE tensorfold:requests_waiting gauge
tensorfold:requests_waiting 0
# HELP tensorfold:prompt_tokens_total Prompt tokens of finished requests.
# TYPE tensorfold:prompt_tokens_total counter
tensorfold:prompt_tokens_total 40840385
# HELP tensorfold:generation_tokens_total Generated tokens of finished requests.
# TYPE tensorfold:generation_tokens_total counter
tensorfold:generation_tokens_total 504646
# HELP tensorfold_health:completion_tokens_total Reply tokens, the running replies' tokens so far included.
# TYPE tensorfold_health:completion_tokens_total counter
tensorfold_health:completion_tokens_total 504733
"""
TF_NATIVE_DECODE = (
    """\
tensorfold:requests_running 1
tensorfold:num_requests_running 1
tensorfold:generation_tokens_running 843
tensorfold:generation_tokens_total 457988
""",
    """\
tensorfold:requests_running 1
tensorfold:num_requests_running 1
tensorfold:generation_tokens_running 3253
tensorfold:generation_tokens_total 457988
""",
)
TF_PATCH0150 = """\
# HELP tensorfold_completion_tokens_total generated tokens
# TYPE tensorfold_completion_tokens_total counter
tensorfold_completion_tokens_total{model="GLM-5.3-Flash-EXL3"} 2557
# HELP tensorfold_requests_inflight completions running now
# TYPE tensorfold_requests_inflight gauge
tensorfold_requests_inflight{model="GLM-5.3-Flash-EXL3"} 0
"""


def test_parse_reads_the_native_tensorfold_gauge_and_counter() -> None:
    sig = tp.ENGINE_SIGNALS["tensorfold"]
    assert sig.running == "tensorfold:requests_running"
    assert sig.progress == "tensorfold:generation_tokens_total"
    assert tp.parse_engine_metrics(TF_V06, sig) == tp.EngineReading(1.0, 504646.0)


def test_the_mirror_gauge_is_not_counted_twice() -> None:
    # Native 1.0.x and the Ling-3.1 fork print num_requests_running beside
    # requests_running, and generation_tokens_running beside the total.
    sig = tp.ENGINE_SIGNALS["tensorfold"]
    got = tp.parse_engine_metrics(TF_NATIVE_DECODE[0], sig)
    assert got == tp.EngineReading(1.0, 457988.0)


def test_a_patch0150_body_is_unreadable_as_native_tensorfold() -> None:
    """v0.3.4 + patch 0150 has its own gauge (tensorfold_requests_inflight),
    not tensorfold:requests_running. Its body must never read as 0 running."""
    assert (
        tp.parse_engine_metrics(TF_PATCH0150, tp.ENGINE_SIGNALS["tensorfold"]) is None
    )


def test_native_tensorfold_gets_the_engine_rule() -> None:
    signal, why = tp.engine_signal_for(
        {"engine": "tensorfold", "base_url": "http://srv:8888"}, name="tf"
    )
    assert signal is tp.ENGINE_SIGNALS["tensorfold"]
    assert "tensorfold:requests_running" in why


@pytest.mark.parametrize("name", ["glm53ftfjaydual2xrc", "glm53ftfjaydual2xrchigh"])
def test_tensorfold_v034_patch0150_keeps_the_power_rule_and_says_why(
    name: str,
) -> None:
    cfg = tomllib.loads((pathlib.Path(__file__).parent / "tasks.toml").read_text())
    signal, why = tp.engine_signal_for(cfg["backend"][name], name=name)
    assert signal is None
    assert "power rule" in why
    assert "patch 0150" in why
    assert "tensorfold:requests_running" in why


@pytest.mark.parametrize(
    "name", ["dsv41ftfzigjaydual2xrc", "dsv41f1017tfzigjaydual2xrc"]
)
def test_october_zigjay_deepseek_keeps_the_power_rule(name: str) -> None:
    cfg = tomllib.loads((pathlib.Path(__file__).parent / "tasks.toml").read_text())
    signal, why = tp.engine_signal_for(cfg["backend"][name], name=name)
    assert signal is None
    assert "no tensorfold:requests_running" in why


def test_every_patch0150_clone_is_on_the_power_rule() -> None:
    """A new backend on the patch 0150 tree must not slip off the list."""
    cfg = tomllib.loads((pathlib.Path(__file__).parent / "tasks.toml").read_text())
    clones = {
        name
        for name, b in cfg["backend"].items()
        if b.get("recipe_dir") == "~/jay-tensorfold"
    }
    assert clones, "the patch 0150 backends are in tasks.toml"
    assert clones <= set(tp.POWER_RULE_BACKENDS)


def test_every_power_rule_tensorfold_backend_is_a_tensorfold_backend() -> None:
    """A stale or misspelled name would silently put nothing on the power rule."""
    cfg = tomllib.loads((pathlib.Path(__file__).parent / "tasks.toml").read_text())
    for name in tp.POWER_RULE_BACKENDS:
        assert cfg["backend"][name]["engine"] == "tensorfold", name


def test_every_other_tensorfold_backend_gets_the_engine_rule() -> None:
    cfg = tomllib.loads((pathlib.Path(__file__).parent / "tasks.toml").read_text())
    for name, b in cfg["backend"].items():
        if b.get("engine") != "tensorfold" or name in tp.POWER_RULE_BACKENDS:
            continue
        signal, why = tp.engine_signal_for(b, name=name)
        assert signal is tp.ENGINE_SIGNALS["tensorfold"], (name, why)


def _tf_watchdog(
    watts: list[float | None], engine: list[tp.EngineReading | None], clock: _Now
) -> IdleStallWatchdog:
    return IdleStallWatchdog(
        _Sampler(watts),
        idle_floor_watts=13.0,
        idle_floor_source="tier",
        engine=_Engine(engine),
        engine_floor_watts=20.0,
        engine_floor_source="engine",
        engine_name="tensorfold (tensorfold:requests_running)",
        idle_stall_secs=600.0,
        monotonic=clock,
    )


#: The 2026-10-21 Qwen3.8 TensorFold kill, as Prometheus stored it at the
#: watchdog's 15 s poll from 2026-10-20T22:54:00-0400 to 23:04:00-0400
#: (02:54:00Z-03:04:00Z), 41 samples. The head's DCGM power (scraped every
#: 30 s, so each value shows twice), and the decode counter: flat at 49337,
#: then 49451 from 23:03:30-0400, when a 114-token request finished. The
#: gauge read 0 at every sample. The server log shows that request arrived at
#: 23:03:23.9-0400, 600.2 s after the reply before it finished.
_STALL_PAIRS = (
    12.121, 11.609, 11.094, 10.956, 10.844, 10.759, 10.692, 10.737, 10.765,
    10.789, 10.793, 10.789, 10.771, 10.765, 10.776, 10.759, 10.758, 10.748,
    10.746, 10.752,
)  # fmt: skip
STALL_WATTS: list[float | None] = [w for pair in _STALL_PAIRS for w in (pair, pair)]
STALL_WATTS.append(10.806)
STALL_TOKENS = [49337.0] * 38 + [49451.0] * 3


def test_the_stored_stall_is_stopped_by_power_and_kept_by_the_engine() -> None:
    """The 13 W power rule reads all 41 samples as idle and stops the trial at
    23:04:00-0400, 600 s in. The engine rule resets at 23:03:30-0400, when the
    counter moved: a request had just run, so the agent was working again."""
    assert len(STALL_WATTS) == len(STALL_TOKENS) == 41
    sig = tp.ENGINE_SIGNALS["tensorfold"]
    readings: list[tp.EngineReading | None] = [
        tp.parse_engine_metrics(
            f"tensorfold:requests_running 0\ntensorfold:generation_tokens_total {n:g}\n",
            sig,
        )
        for n in STALL_TOKENS
    ]
    power = IdleStallWatchdog(
        _Sampler(STALL_WATTS), idle_floor_watts=13.0, monotonic=(c := _Now())
    )
    _run(power, c, 41)
    assert power.stalled() is True

    clock = _Now()
    wd = _tf_watchdog(STALL_WATTS, readings, clock)
    _run(wd, clock, 41)
    assert wd.stalled() is False
    # Work at 23:03:30; the stretch opens at 23:03:45 and is 15 s old at 23:04:00.
    assert wd.idle_seconds() == 15.0


def test_a_flat_tensorfold_stretch_is_stopped_by_the_engine_rule() -> None:
    """A synthetic flat stretch (not stored data): 0 running, the counter
    still, under 13 W. The engine rule stops it at 600 s."""
    sig = tp.ENGINE_SIGNALS["tensorfold"]
    body = "tensorfold:requests_running 0\ntensorfold:generation_tokens_total 49337\n"
    clock = _Now()
    wd = _tf_watchdog([10.8], [tp.parse_engine_metrics(body, sig)], clock)
    _run(wd, clock, 41)  # 0..600 s
    assert wd.stalled() is True
    assert wd.rule_summary().startswith("engine rule: tensorfold")


def test_tensorfold_decode_with_a_flat_counter_is_never_killed() -> None:
    """generation_tokens_total moves only when a request finishes. The stored
    native 1.0.x bodies show it flat across a live decode, and the Ling-3.1
    decode of 2026-11-28 held 1 running for 12 min with the counter flat at
    173085, then it rose by 32768 at once. The gauge alone keeps the trial,
    even at 5 W, under every floor."""
    sig = tp.ENGINE_SIGNALS["tensorfold"]
    first, second = (tp.parse_engine_metrics(b, sig) for b in TF_NATIVE_DECODE)
    assert first is not None and second is not None
    assert first.progress == second.progress  # flat across a live decode
    clock = _Now()
    wd = _tf_watchdog([5.0], [first, second], clock)
    _run(wd, clock, 200)  # 50 min
    assert wd.stalled() is False
    assert wd.idle_seconds() == 0.0


def test_a_patch0150_server_falls_back_to_the_power_rule_per_sample() -> None:
    """Were a patch 0150 backend put on the engine rule by mistake, each read
    is None, and 14 W stays work under the 13 W power floor."""
    reading = tp.parse_engine_metrics(TF_PATCH0150, tp.ENGINE_SIGNALS["tensorfold"])
    clock = _Now()
    wd = _tf_watchdog([14.0], [reading], clock)
    _run(wd, clock, 100)
    assert wd.stalled() is False


def test_a_backend_with_no_url_keeps_the_power_rule() -> None:
    signal, why = tp.engine_signal_for({"engine": "vllm"}, name="b")
    assert signal is None
    assert "no base_url" in why


def test_every_backend_in_tasks_toml_gets_a_rule() -> None:
    cfg = tomllib.loads((pathlib.Path(__file__).parent / "tasks.toml").read_text())
    rules = {
        tp.engine_signal_for(b, name=n)[0] is not None
        for n, b in cfg["backend"].items()
    }
    assert rules == {True, False}, "both kinds of backend exist in tasks.toml"


def test_the_engine_floor_resolves_flag_then_backend_then_engine_default() -> None:
    b = {"tier": "gb10-spark-x2"}
    assert tp.resolve_engine_idle_floor(None, b) == (20.0, "engine")
    assert tp.resolve_engine_idle_floor(None, {**b, "idle_floor_watts": 14}) == (
        14.0,
        "backend",
    )
    assert tp.resolve_engine_idle_floor(12.0, b) == (12.0, "flag")


def test_the_engine_floor_is_above_light_decode_power() -> None:
    """With the engine signal the floor no longer has to sit under a light
    decode: the engine says when a request runs. 20 W is above the GB10
    loaded-idle readings in #1450, and it catches the 2026-10-17 stall
    (15.6-16.2 W) that 13 W misses. It still misses quiet windows that peak
    at 20 W or more: 9 on vLLM and 5 on llama.cpp in 60 days."""
    assert tp.ENGINE_IDLE_FLOOR_WATTS == IDLE_FLOOR_WATTS == 20.0


class _Engine:
    """An engine sampler that yields a scripted sequence, holding at the last."""

    def __init__(self, values: list[tp.EngineReading | None]) -> None:
        self._values = values
        self._i = 0

    def __call__(self) -> tp.EngineReading | None:
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


def _engine_watchdog(
    watts: list[float | None], engine: list[tp.EngineReading | None], clock: _Now
) -> IdleStallWatchdog:
    return IdleStallWatchdog(
        _Sampler(watts),
        idle_floor_watts=13.0,
        idle_floor_source="tier",
        engine=_Engine(engine),
        engine_floor_watts=20.0,
        engine_floor_source="engine",
        engine_name="vllm",
        idle_stall_secs=600.0,
        monotonic=clock,
    )


def _run(wd: IdleStallWatchdog, clock: _Now, steps: int, every: float = 15.0) -> None:
    for i in range(steps):
        clock.t = i * every
        wd.sample()


IDLE = tp.EngineReading(0.0, 500.0)


def test_engine_rule_catches_a_stuck_client_the_power_floor_misses() -> None:
    """The #1453 case: no request runs and the counters stand still, but the
    worker reads 14 W, above the 13 W tier floor. The power rule never sees
    idle; the engine rule stops the trial at 600 s."""
    clock = _Now()
    wd = _engine_watchdog([14.0], [IDLE], clock)
    _run(wd, clock, 41)  # 0..600 s
    assert wd.stalled() is True

    power_only = IdleStallWatchdog(
        _Sampler([14.0]), idle_floor_watts=13.0, monotonic=(c := _Now())
    )
    _run(power_only, c, 41)
    assert power_only.stalled() is False


def test_engine_rule_never_kills_a_running_request_at_low_power() -> None:
    """A light decode at 5-16 W with one request running is work (#1450)."""
    clock = _Now()
    wd = _engine_watchdog([5.0], [tp.EngineReading(1.0, 500.0)], clock)
    _run(wd, clock, 200)
    assert wd.stalled() is False
    assert wd.idle_seconds() == 0.0


def test_moving_tokens_with_no_running_request_is_work() -> None:
    """The gauge is a snapshot: vLLM read 0 running at 07:27 on 2026-10-14
    while its token counter rose 11960 -> 12571 in the next minute. A counter
    that moved between two samples means the engine worked."""
    clock = _Now()
    readings = [tp.EngineReading(0.0, float(n)) for n in range(100)]
    wd = _engine_watchdog([5.0], readings, clock)  # type: ignore[arg-type]
    _run(wd, clock, 100)
    assert wd.stalled() is False


def test_a_counter_reset_is_work() -> None:
    """llama.cpp's token counter fell 36158 -> 359 between two minutes on
    2026-10-15 (a server restart). A counter that went down moved."""
    clock = _Now()
    wd = _engine_watchdog(
        [5.0], [tp.EngineReading(0.0, 36158.0), tp.EngineReading(0.0, 359.0)], clock
    )
    clock.t = 0.0
    wd.sample()
    clock.t = 15.0
    wd.sample()
    assert wd.idle_seconds() == 0.0


def test_engine_rule_at_or_above_its_floor_is_work() -> None:
    clock = _Now()
    wd = _engine_watchdog([20.0], [IDLE], clock)
    _run(wd, clock, 100)
    assert wd.stalled() is False


def test_an_unreadable_engine_falls_back_to_the_power_rule() -> None:
    """Endpoint down or a scrape gap: the sample is judged as before #1453,
    against the power-rule floor (13 W). 14 W is then work, so a light decode
    is never killed on a blind engine signal."""
    clock = _Now()
    wd = _engine_watchdog([14.0], [None], clock)
    _run(wd, clock, 100)
    assert wd.stalled() is False

    clock = _Now()
    wd = _engine_watchdog([10.0], [None], clock)
    _run(wd, clock, 41)
    assert wd.stalled() is True  # under 13 W: the #366 watchdog still works


def test_an_unreadable_engine_does_not_reset_the_counter_baseline() -> None:
    """Tokens that moved across a gap still count as work at the next read."""
    clock = _Now()
    wd = _engine_watchdog(
        [5.0],
        [tp.EngineReading(0.0, 100.0), None, tp.EngineReading(0.0, 900.0)],
        clock,
    )
    for t in (0.0, 15.0, 30.0):
        clock.t = t
        wd.sample()
    assert wd.idle_seconds() == 0.0


def test_unreadable_power_still_holds_with_an_engine() -> None:
    clock = _Now()
    wd = _engine_watchdog([None], [IDLE], clock)
    _run(wd, clock, 100)
    assert wd.stalled() is False


def test_the_stall_log_names_the_engine_rule(caplog) -> None:
    clock = _Now()
    wd = IdleStallWatchdog(
        _Sampler([14.0]),
        idle_floor_watts=13.0,
        idle_floor_source="tier",
        engine=_Engine([IDLE]),
        engine_floor_watts=20.0,
        engine_floor_source="engine",
        engine_name="vllm",
        idle_stall_secs=0.0,
        monotonic=clock,
    )
    log = logging.getLogger("test_engine_rule_log")
    with caplog.at_level(logging.ERROR, logger="test_engine_rule_log"):
        result = run_client_with_watchdog(
            [sys.executable, "-c", "import time; time.sleep(5.0)"],
            cwd=None,
            env=None,
            timeout=30.0,
            watchdog=wd,
            poll_secs=0.01,
            grace_secs=2.0,
            log=log,
        )
    assert result.idle_stalled is True
    assert "engine rule" in caplog.text
    assert "vllm" in caplog.text
    assert "20 W (engine)" in caplog.text


def test_the_stall_log_says_power_rule_and_why(caplog) -> None:
    clock = _Now()
    # A patch 0150 backend: the reason is a power-rule reason (#1545 review;
    # a native TensorFold backend now gets an engine-rule string here).
    _, why = tp.engine_signal_for(
        {"engine": "tensorfold", "base_url": "http://s:1"},
        name="glm53ftfjaydual2xrc",
    )
    assert why.startswith("power rule")
    wd = IdleStallWatchdog(
        _Sampler([5.0]),
        idle_floor_watts=13.0,
        idle_floor_source="tier",
        power_only_reason=why,
        idle_stall_secs=0.0,
        monotonic=clock,
    )
    log = logging.getLogger("test_power_rule_log")
    with caplog.at_level(logging.ERROR, logger="test_power_rule_log"):
        run_client_with_watchdog(
            [sys.executable, "-c", "import time; time.sleep(5.0)"],
            cwd=None,
            env=None,
            timeout=30.0,
            watchdog=wd,
            poll_secs=0.01,
            grace_secs=2.0,
            log=log,
        )
    assert "power rule" in caplog.text
    assert "13 W (tier)" in caplog.text
    assert "patch 0150" in caplog.text
    assert "engine rule" not in caplog.text


def test_fetch_engine_reading_is_none_when_the_endpoint_is_down() -> None:
    # Port 9 (discard) on loopback: nothing listens, so the GET fails fast.
    got = tp.fetch_engine_reading(
        "http://127.0.0.1:9/metrics", tp.ENGINE_SIGNALS["vllm"], timeout=1.0
    )
    assert got is None


# --- #1453 review M1/L8: every failed engine read is None, never a raise ----

_BODY = b'vllm:num_requests_running{engine="0"} 1.0\nvllm:generation_tokens_total{engine="0"} 7.0\n'
HTTP_OK = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(_BODY), _BODY)
#: Content-Length promises more than arrives: http.client.IncompleteRead.
HTTP_SHORT = b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + _BODY
#: A chunked body cut inside a chunk: IncompleteRead too.
HTTP_CUT_CHUNK = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n400\r\n" + _BODY
#: Not HTTP at all, as from a wrong port: http.client.BadStatusLine.
NOT_HTTP = b"SSH-2.0-OpenSSH_9.6\r\n"


def test_fetch_engine_reading_reads_a_real_server(raw_http) -> None:
    got = tp.fetch_engine_reading(raw_http(HTTP_OK), tp.ENGINE_SIGNALS["vllm"])
    assert got == tp.EngineReading(1.0, 7.0)


@pytest.mark.parametrize("reply", [HTTP_SHORT, HTTP_CUT_CHUNK, NOT_HTTP])
def test_fetch_engine_reading_is_none_on_a_broken_reply(raw_http, reply) -> None:
    url = raw_http(reply)
    assert tp.fetch_engine_reading(url, tp.ENGINE_SIGNALS["vllm"], timeout=2.0) is None


def test_fetch_engine_reading_is_none_on_a_timeout(raw_http) -> None:
    url = raw_http(HTTP_OK, delay=3.0)
    assert tp.fetch_engine_reading(url, tp.ENGINE_SIGNALS["vllm"], timeout=0.3) is None


def test_a_sampler_that_raises_still_kills_the_client_group(tmp_path) -> None:
    """Any exception out of the poll loop must take the client group with it:
    it was started in its own session, so it would outlive run.py's cleanup
    and keep working in a deleted worktree."""
    pidfile = tmp_path / "pid"

    def boom() -> float | None:
        raise RuntimeError("sampler blew up")

    wd = IdleStallWatchdog(boom, idle_stall_secs=600.0)
    child = (
        "import os, time, pathlib; "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    with pytest.raises(RuntimeError, match="sampler blew up"):
        run_client_with_watchdog(
            [sys.executable, "-c", child],
            cwd=None,
            env=None,
            timeout=30.0,
            watchdog=wd,
            poll_secs=0.5,
            grace_secs=2.0,
        )
    import os
    import time

    deadline = time.monotonic() + 5.0
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = int(pidfile.read_text())
    gone = False
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            gone = True
            break
        time.sleep(0.05)
    assert gone, f"client {pid} outlived the watchdog's exception"


def test_the_memory_cap_is_checked_before_the_engine_read() -> None:
    """A slow engine read must not delay the memory kill (#1453 review L3)."""
    order: list[str] = []

    def watts() -> float | None:
        order.append("watts")
        return 5.0

    def rss(_pid: int) -> float:
        order.append("rss")
        return 2.0

    wd = IdleStallWatchdog(watts, idle_stall_secs=600.0)
    res = run_client_with_watchdog(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=None,
        env=None,
        timeout=30.0,
        watchdog=wd,
        poll_secs=0.05,
        grace_secs=2.0,
        memory_cap_gib=1.0,
        rss_sampler=rss,
    )
    assert res.memory_killed is True
    assert order[0] == "rss"
    assert "watts" not in order


# --- #1453 review L3: no engine read while power alone says busy -----------


def test_no_engine_read_at_or_above_the_engine_floor() -> None:
    clock = _Now()
    reads: list[int] = []

    def engine() -> tp.EngineReading | None:
        reads.append(1)
        return IDLE

    wd = IdleStallWatchdog(
        _Sampler([40.0, 40.0, 20.0]),
        idle_floor_watts=13.0,
        engine=engine,
        engine_floor_watts=20.0,
        monotonic=clock,
    )
    _run(wd, clock, 3)
    assert reads == []
    assert wd.idle_seconds() == 0.0


def test_a_skipped_read_leaves_one_sample_of_doubt() -> None:
    """After a busy stretch with no reads, the first read compares against an
    old counter value, counts as work, and the next read starts the stretch."""
    clock = _Now()
    wd = IdleStallWatchdog(
        _Sampler([5.0, 40.0, 5.0, 5.0, 5.0]),
        idle_floor_watts=13.0,
        engine=_Engine([tp.EngineReading(0.0, 10.0), tp.EngineReading(0.0, 90.0)]),
        engine_floor_watts=20.0,
        monotonic=clock,
    )
    _run(wd, clock, 5)  # t = 0, 15 (40 W, no read), 30 (moved), 45, 60
    assert wd.idle_seconds() == 15.0


# --- #1453 review L7/L8: the stall line for a mixed or blind stretch -------


def _mixed(engine: list[tp.EngineReading | None], n: int) -> IdleStallWatchdog:
    clock = _Now()
    wd = IdleStallWatchdog(
        _Sampler([10.0]),
        idle_floor_watts=13.0,
        idle_floor_source="tier",
        engine=_Engine(engine),
        engine_floor_watts=20.0,
        engine_name="vllm (vllm:num_requests_running)",
        monotonic=clock,
    )
    _run(wd, clock, n)
    return wd


def test_a_mixed_stretch_counts_both_rules() -> None:
    wd = _mixed([IDLE, IDLE, None, IDLE, None], 5)
    assert wd.idle_seconds() == 60.0  # every sample idle under its rule
    text = wd.rule_summary()
    assert "engine rule" in text and "for 3 samples" in text
    assert "power rule: below 13 W (tier) for 2 samples" in text


def test_a_blind_stretch_says_the_engine_was_never_read() -> None:
    text = _mixed([None], 5).rule_summary()
    assert "for 0 samples" not in text
    assert text.startswith("power rule: below 13 W (tier)")
    assert "could not be read" in text and "vllm" in text
