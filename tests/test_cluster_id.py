"""The cluster identity must refuse rather than fall back.

A failed cluster run recorded as a single-Spark row is the conflation #647
exists to prevent, and it is unrecoverable once written -- nothing in the row
says which topology produced it. So the refusal paths matter more than the
happy path, and each has its own test.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import cluster_id


def test_directory_name_appends_the_node_count() -> None:
    assert (
        cluster_id.cluster_directory("Cortex-X925-128GB-GB10", 2)
        == "Cortex-X925-128GB-GB10-x2"
    )


def test_one_node_is_not_a_cluster() -> None:
    with pytest.raises(ValueError):
        cluster_id.cluster_directory("Cortex-X925-128GB-GB10", 1)


def test_active_links_are_read_from_the_state_field() -> None:
    """A device can report `state DOWN physical_state LINK_UP`. Matching the
    word ACTIVE anywhere in the line would count a down link as up, and the
    real output puts LINK_UP on the DOWN lines -- which is why this is parsed
    by field rather than grepped.
    """
    out = (
        "link rocep1s0f0/1 state DOWN physical_state DISABLED netdev enp1s0f0np0\n"
        "link rocep1s0f1/1 state ACTIVE physical_state LINK_UP netdev enp1s0f1np1\n"
        "link roceP2p1s0f0/1 state DOWN physical_state LINK_UP netdev enP2p1s0f0np0\n"
        "link roceP2p1s0f1/1 state ACTIVE physical_state LINK_UP netdev enP2p1s0f1np1\n"
    )
    assert cluster_id.active_rdma_links(out) == ["enp1s0f1np1", "enP2p1s0f1np1"]


def test_no_active_link_yields_empty_not_error() -> None:
    out = "link rocep1s0f0/1 state DOWN physical_state DISABLED netdev enp1s0f0np0\n"
    assert cluster_id.active_rdma_links(out) == []


def test_empty_output_is_not_an_active_link() -> None:
    """An unplugged Spark shows no RDMA device at all, and `rdma link show`
    prints nothing. That must not read as "no failures, therefore fine".
    """
    assert cluster_id.active_rdma_links("") == []


def test_a_mismatched_peer_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two different machines are not a cluster, and the error must say so
    rather than returning the local name.
    """
    monkeypatch.setattr(
        cluster_id.hardware_id,
        "facts_for_this_machine",
        lambda: ({}, "linux"),
    )
    monkeypatch.setattr(
        cluster_id.hardware_id,
        "directory_name",
        lambda facts, platform: "Cortex-X925-128GB-GB10",
    )
    monkeypatch.setattr(
        cluster_id, "local_fingerprint", lambda: "GB10\n120\nNVIDIA GB10"
    )
    monkeypatch.setattr(
        cluster_id, "_ssh", lambda peer, cmd: "Ryzen 9 7900X\n31\nRTX 3080 Ti"
    )

    with pytest.raises(cluster_id.ClusterVerificationError, match="different hardware"):
        cluster_id.verify_and_name("peer")


def test_more_than_two_nodes_is_refused_not_guessed() -> None:
    with pytest.raises(cluster_id.ClusterVerificationError, match="2-node"):
        cluster_id.verify_and_name("peer", nodes=3)
