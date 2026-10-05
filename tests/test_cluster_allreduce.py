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


def test_a_correct_reduction_passes() -> None:
    assert cluster_allreduce.reduction_error(2.0, 2.0, True, 2.0) is None


def test_a_wrong_element_anywhere_fails() -> None:
    """The old check read check[0] only; one bad element elsewhere passed.
    The caller passes the buffer's min and max, so any element counts."""
    assert cluster_allreduce.reduction_error(2.0, 3.0, True, 2.0)
    assert cluster_allreduce.reduction_error(0.0, 2.0, True, 2.0)


def test_nan_fails() -> None:
    """abs(nan - 2) > 1e-3 is False, so NaN passed the old check."""
    nan = float("nan")
    assert cluster_allreduce.reduction_error(nan, nan, False, 2.0)
    # min/max propagate NaN; even if a reduction hid it, `finite` does not.
    assert cluster_allreduce.reduction_error(2.0, 2.0, False, 2.0)
    assert cluster_allreduce.reduction_error(nan, 2.0, True, 2.0)


def test_every_measured_size_is_checked() -> None:
    """A link fine at 1 MiB can be wrong at 1 GiB; the check lives in the
    per-size loop, after the timed samples, not once on a 2 KiB buffer."""
    src = pathlib.Path(cluster_allreduce.__file__).read_text()
    loop = src.split("for size in sizes:", 1)[1].split("dist.destroy_process_group", 1)[
        0
    ]
    timed = loop.index("samples.append")
    assert loop.index("reduction_error(") > timed
    assert "torch.ones(1024" not in src
