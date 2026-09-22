"""The all-reduce report is only useful if its bandwidth definition matches
nccl-tests. Quoting algorithm bandwidth as bus bandwidth overstates a two-node
result by 2x, which would turn a saturated link into an apparently impossible
one (#646).
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import cluster_allreduce


def test_bus_bandwidth_matches_nccl_tests_definition() -> None:
    """busbw = algbw * 2(n-1)/n, anchored on a hand-checkable absolute.

    1 GiB in exactly 1 second is 8.589934592 Gbit/s of algorithm bandwidth. At
    two ranks the factor is 2*(2-1)/2 = 1, so bus bandwidth equals it. A
    relationship-only assertion would pass under a uniformly wrong constant, so
    the value is pinned.
    """
    got = cluster_allreduce.bus_bandwidth_gbps(1 << 30, 1.0, 2)
    assert abs(got - 8.589934592) < 1e-9


def test_bus_bandwidth_at_four_ranks_is_one_and_a_half_times_algbw() -> None:
    """2*(4-1)/4 = 1.5. Pins the rank dependence, not just the two-node case."""
    got = cluster_allreduce.bus_bandwidth_gbps(1 << 30, 1.0, 4)
    assert abs(got - 8.589934592 * 1.5) < 1e-9


def test_sizes_span_latency_and_bandwidth_regimes() -> None:
    """A sweep that stops at a few MiB cannot show a link that collapses only
    under large messages, which is the failure this script exists to find.
    """
    assert min(cluster_allreduce.DEFAULT_SIZES) <= 1 << 20
    assert max(cluster_allreduce.DEFAULT_SIZES) >= 1 << 30
