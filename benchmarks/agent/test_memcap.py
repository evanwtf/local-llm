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
    r, peak, killed = memcap.run_capped(["echo", "ok"], None, timeout=30, cap_gib=2.0)
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
