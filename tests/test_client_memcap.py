"""The agent-client memory cap in run_client_with_watchdog (#379).

On 2026-09-14 a model-written solution the agent executed grew to ~51 GiB and
drove a GLOBAL OOM that evicted the model server and killed the run. The oracle
had a memory ceiling since #82; the client phase did not. These tests drive the
watchdog with an injected RSS sampler (the module is built to be tested offline)
so we never allocate real gigabytes: a tree that crosses the cap is killed
locally as one trial, and a well-behaved run is untouched.
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import timeout_policy


def _busy_watchdog():
    """A watchdog whose GPU always reads busy and whose stall window never
    elapses in a test, so only the memory path can end the run."""
    return timeout_policy.IdleStallWatchdog(
        lambda: 999.0, idle_floor_watts=13.0, idle_stall_secs=600.0
    )


def test_client_killed_when_tree_crosses_cap():
    # A real child that would otherwise outlive the test; the injected sampler
    # ramps its reported tree-RSS past the 24 GiB cap on the third sample.
    samples = iter([1.0, 5.0, 30.0])

    def fake_rss(_pid):
        try:
            return next(samples)
        except StopIteration:
            return 30.0

    start = time.monotonic()
    res = timeout_policy.run_client_with_watchdog(
        ["sleep", "30"],
        cwd=None,
        env=None,
        timeout=None,
        watchdog=_busy_watchdog(),
        poll_secs=0.05,
        grace_secs=0.5,
        memory_cap_gib=24.0,
        rss_sampler=fake_rss,
    )
    elapsed = time.monotonic() - start

    assert res.memory_killed is True
    assert res.idle_stalled is False
    assert res.timed_out is False
    assert res.peak_rss_gib >= 24.0
    # It was killed, not left to run its 30s: the process was terminated and the
    # call returned promptly.
    assert res.returncode is not None and res.returncode != 0
    assert elapsed < 10.0


def test_well_behaved_client_is_not_killed():
    # Tree stays far under the cap; a quick clean exit must pass through
    # untouched, with no memory kill and returncode 0.
    res = timeout_policy.run_client_with_watchdog(
        ["true"],
        cwd=None,
        env=None,
        timeout=None,
        watchdog=_busy_watchdog(),
        poll_secs=0.05,
        grace_secs=0.5,
        memory_cap_gib=24.0,
        rss_sampler=lambda _pid: 0.5,
    )
    assert res.memory_killed is False
    assert res.returncode == 0


def test_no_cap_never_samples_memory():
    # memory_cap_gib=None disables the check entirely; the sampler must not even
    # be consulted, and a clean run exits normally.
    def boom(_pid):  # pragma: no cover - must never be called
        raise AssertionError("rss_sampler consulted when cap is disabled")

    res = timeout_policy.run_client_with_watchdog(
        ["true"],
        cwd=None,
        env=None,
        timeout=None,
        watchdog=_busy_watchdog(),
        poll_secs=0.05,
        grace_secs=0.5,
        memory_cap_gib=None,
        rss_sampler=boom,
    )
    assert res.memory_killed is False
    assert res.returncode == 0
