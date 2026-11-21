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
