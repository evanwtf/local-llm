"""Tests for the timeout policies (#366).

Fast and offline: the watchdog takes an injectable clock and a scripted watts
sampler, the circuit-breaker is a pure function, and the subprocess tests spawn
short-lived Python children rather than any real client or nvidia-smi. Nothing
here reads a GPU.
"""

from __future__ import annotations

import sys

import results
from timeout_policy import (
    ClientResult,
    IdleStallWatchdog,
    cell_should_abort,
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
    clock = _Clock([0.0, 0.0, 300.0, 600.0])
    wd = IdleStallWatchdog(
        _Sampler([5.0]),  # always idle
        idle_floor_watts=20.0,
        idle_stall_secs=600.0,
        monotonic=clock,
    )
    wd.sample()  # t=0
    assert wd.stalled() is False
    wd.sample()  # t=300
    assert wd.stalled() is False
    wd.sample()  # t=600 -> 600s continuous idle
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
