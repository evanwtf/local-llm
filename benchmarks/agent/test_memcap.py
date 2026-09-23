"""The oracle's memory ceiling (#82).

A `scan` implementation that buffered instead of streaming made the oracle
allocate 49 GB and drove the machine into swap. The step already had a timeout
and it did not help: a timeout shortens an outage, a cap prevents one.
"""

from __future__ import annotations

import pathlib
import sys
import textwrap

import memcap
import pytest

HOG = textwrap.dedent("""
    import time
    blocks = []
    for _ in range(80):
        blocks.append(bytearray(256 * 1024 * 1024))
        time.sleep(0.2)
    print("SURVIVED")
""")


def test_a_small_command_is_untouched():
    r, _peak, killed = memcap.run_capped(["echo", "ok"], None, timeout=30, cap_gib=2.0)
    assert killed is False
    assert r.returncode == 0
    assert r.stdout.strip() == "ok"


def test_success_is_not_reported_as_failure():
    """`proc.returncode or 1` turned every passing 0 into a 1.

    Caught by running it, not by reading it: the first version would have
    recorded every passing oracle run as a failure.
    """
    r, _, _ = memcap.run_capped(["echo", "ok"], None, timeout=30, cap_gib=2.0)
    assert r.returncode == 0


@pytest.mark.slow
def test_a_runaway_tree_is_killed(tmp_path):
    """The memory must be found in a GRANDCHILD.

    The oracle is `uv run pytest`, so reading only the direct child would have
    reported near zero for the 49 GB run that motivated this.
    """
    hog = tmp_path / "hog.py"
    hog.write_text(HOG)
    r, peak, killed = memcap.run_capped(
        ["sh", "-c", f"{sys.executable} {hog}"], None, timeout=120, cap_gib=2.0
    )
    assert killed is True
    assert peak > 2.0
    assert "SURVIVED" not in r.stdout


def test_tree_rss_sums_descendants():
    """Pure arithmetic over a fake process table -- no processes spawned."""
    table = {
        100: (1, 1024**2),  # root, 1 GiB in KiB
        200: (100, 1024**2),  # child
        300: (200, 2 * 1024**2),  # grandchild
        400: (1, 9 * 1024**2),  # unrelated: must not be counted
    }
    assert memcap.tree_rss_gib(100, table) == 4.0


def test_tree_rss_survives_a_cycle_and_missing_pids():
    """`ps` output is a snapshot; a pid can vanish between lines."""
    table = {10: (10, 1024**2)}  # self-parented
    assert memcap.tree_rss_gib(10, table) == 1.0
    assert memcap.tree_rss_gib(999, {}) == 0.0


def test_the_oracle_declares_a_cap():
    import run

    assert 0 < run.ORACLE_MEM_CAP_GIB <= 16
    source = (pathlib.Path(run.__file__)).read_text()
    assert "cap_gib=ORACLE_MEM_CAP_GIB" in source


# --- #680: the client cap follows the container's cgroup limit ---------------


def _cgroup(tmp_path, max_value=None, events=None):
    if max_value is not None:
        (tmp_path / "memory.max").write_text(f"{max_value}\n")
    if events is not None:
        (tmp_path / "memory.events").write_text(events)
    return tmp_path


def test_a_cgroup_limit_is_read_in_gib(tmp_path):
    root = _cgroup(tmp_path, 12 * 1024**3)
    assert memcap.cgroup_limit_gib(root) == 12.0


def test_an_unlimited_cgroup_has_no_limit(tmp_path):
    assert memcap.cgroup_limit_gib(_cgroup(tmp_path, "max")) is None


def test_no_cgroup_files_means_no_limit_not_a_guess(tmp_path):
    assert memcap.cgroup_limit_gib(tmp_path) is None


def test_a_v1_unlimited_sentinel_is_no_limit(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "memory.limit_in_bytes").write_text("9223372036854771712\n")
    assert memcap.cgroup_limit_gib(tmp_path) is None


def test_inside_a_limited_container_the_watcher_is_off(tmp_path):
    """The kernel enforces the container limit (#680); the watcher existed only
    to keep a global OOM from taking sshd and the model server (#82, #379)."""
    root = _cgroup(tmp_path, 12 * 1024**3)
    assert memcap.default_client_cap_gib({}, root) == 0.0


def test_an_explicit_cap_still_wins_over_the_cgroup(tmp_path):
    root = _cgroup(tmp_path, 12 * 1024**3)
    env = {"LOCAL_LLM_CLIENT_MEM_CAP_GIB": "10"}
    assert memcap.default_client_cap_gib(env, root) == 10.0


def test_without_a_limit_the_uncontained_default_holds(tmp_path):
    assert memcap.default_client_cap_gib({}, _cgroup(tmp_path, "max")) == 24.0
    assert memcap.default_client_cap_gib({}, tmp_path) == 24.0


def test_the_client_is_made_the_kernels_first_victim():
    wrapped = memcap.oom_first(["opencode", "run", "--dir", "/w"])
    assert wrapped[:2] == ["sh", "-c"]
    assert "1000 > /proc/self/oom_score_adj" in wrapped[2]
    assert 'exec "$@"' in wrapped[2]
    assert wrapped[4:] == ["opencode", "run", "--dir", "/w"]


def test_kernel_oom_kills_are_counted(tmp_path):
    events = "low 0\nhigh 0\nmax 3\noom 1\noom_kill 2\noom_group_kill 0\n"
    assert memcap.cgroup_oom_kills(_cgroup(tmp_path, events=events)) == 2


def test_unreadable_oom_counter_is_none(tmp_path):
    assert memcap.cgroup_oom_kills(tmp_path) is None


def test_a_kernel_kill_is_named_in_the_run_log():
    import run

    line = run.timeout_message(
        "t",
        1800,
        {
            "timeout_reason": "memory-cap",
            "kernel_oom_kills": 1,
            "client_mem_limit_gib": 12.0,
        },
    )
    assert "kernel" in line and "12.0 GiB" in line and "excluded" in line
