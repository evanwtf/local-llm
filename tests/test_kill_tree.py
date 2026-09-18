"""The memory cap must kill exactly what it counted. #485

2026-09-17: the client cap measured a 24.8 GiB process tree and killed the
process group. The model's `mbox-scan` code had left the group -- as a
detached tool call does -- so it survived, and grew to 108,524 MiB before
earlyoom SIGTERMed it. The sampler walks parent links and saw the runaway; the
kill walked the process group and did not. These tests pin that the kill now
walks the same tree the sampler measures, and that the Linux sandbox gives
each trial a PID namespace so killing it kills everything inside.
"""

from __future__ import annotations

import logging
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import memcap
import run
import timeout_policy as tp


def test_tree_pids_follows_parent_links_not_the_process_group():
    """The detached grandchild (40) is reached by its parent link even though
    it would be in a different process group."""
    table = {10: (1, 100), 20: (10, 100), 30: (20, 100), 40: (20, 900), 99: (1, 5)}
    assert sorted(memcap.tree_pids(10, table)) == [10, 20, 30, 40]
    assert 99 not in memcap.tree_pids(10, table)


def test_tree_pids_and_tree_rss_count_the_same_processes():
    """The cap must kill exactly what it counted."""
    table = {10: (1, 1024), 20: (10, 2048), 40: (20, 4096)}
    counted_kib = sum(table[p][1] for p in memcap.tree_pids(10, table))
    assert round(counted_kib / 1024**2, 2) == memcap.tree_rss_gib(10, table)


def _sleepers(tag: str) -> list[int]:
    out = subprocess.run(
        ["ps", "-eo", "pid,args"], capture_output=True, text=True, check=False
    ).stdout
    return [
        int(line.split()[0])
        for line in out.splitlines()
        if line.strip().endswith(f"sleep {tag}")
    ]


def _reap(tag: str) -> None:
    for pid in _sleepers(tag):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.skipif(sys.platform == "darwin", reason="setsid is a Linux util here")
def test_the_agent_phase_kill_reaches_a_runaway_that_left_the_group():
    """The #485 escape, reproduced without any sandbox: `setsid` puts the
    grandchild in its own session, which is what let it survive a killpg."""
    tag = "437"
    proc = subprocess.Popen(
        ["bash", "-c", f"setsid sleep {tag} & wait"], start_new_session=True, text=True
    )
    try:
        deadline = time.monotonic() + 5
        while not _sleepers(tag) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert _sleepers(tag), "the detached grandchild never started"
        tp._terminate_group(proc, 1.0, logging.getLogger("test"))
        time.sleep(0.5)
        assert _sleepers(tag) == [], "a detached runaway survived the cap's kill"
    finally:
        _reap(tag)


@pytest.mark.skipif(sys.platform == "darwin", reason="setsid is a Linux util here")
def test_kill_tree_reaches_a_runaway_that_left_the_group():
    """The oracle phase uses kill_tree; same escape, same requirement."""
    tag = "438"
    proc = subprocess.Popen(
        ["bash", "-c", f"setsid sleep {tag} & wait"], start_new_session=True, text=True
    )
    try:
        deadline = time.monotonic() + 5
        while not _sleepers(tag) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert memcap.kill_tree(proc.pid) >= 1
        time.sleep(0.5)
        assert _sleepers(tag) == []
    finally:
        _reap(tag)


def test_the_linux_sandbox_gives_every_trial_its_own_pid_namespace():
    """With a PID namespace, the sandbox's child is PID 1 inside, and when it
    dies the kernel kills the rest -- detached or not. Measured 2026-09-17: a
    setsid'd grandchild survived a group kill without `--unshare-pid` and died
    with it."""
    argv = run.bwrap_argv(["true"], "/tmp/wt", "/home/x/git/gmail-archive", [])
    assert "--unshare-pid" in argv
    assert "--die-with-parent" in argv
