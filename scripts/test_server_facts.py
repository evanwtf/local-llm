"""The server facts decide which ledger a remote row joins. #562, #647

Nothing in a row can be repaired once it lands in the wrong ledger: the
hardware facts describe the head node whether one Spark served or two. So the
facts refuse a backend whose tier does not belong to the machine they name.
"""

from __future__ import annotations

import pathlib

import pytest
import server_facts

SINGLE = "Cortex-X925-128GB-GB10"
CLUSTER = "Cortex-X925-128GB-GB10-x2"

TASKS = """\
base_commit = "abc1234"

[backend.pair]
tier = "gb10-spark-x2"
topology = "remote"
model = "m"
base_url = "http://127.0.0.1:8888"

[backend.single]
tier = "gb10-spark"
topology = "remote"
model = "m"
base_url = "http://127.0.0.1:8888"
"""


@pytest.fixture
def tasks(tmp_path, monkeypatch) -> pathlib.Path:
    """A tasks file, and a head node that probes as one Spark."""
    import run

    monkeypatch.setattr(
        server_facts.hardware_id, "facts_for_this_machine", lambda: ({}, "linux")
    )
    monkeypatch.setattr(server_facts.hardware_id, "directory_name", lambda f, p: SINGLE)
    monkeypatch.setattr(server_facts.cluster_id, "verify_and_name", lambda p: CLUSTER)
    monkeypatch.setattr(server_facts.preflight, "machine_facts", dict)
    monkeypatch.setattr(run, "capture_versions", lambda *a, **k: {})
    path = tmp_path / "tasks.toml"
    path.write_text(TASKS)
    return path


def test_a_cluster_backend_without_a_peer_refuses(tasks):
    """The trigger: a two-node backend, facts taken without --cluster-peer.
    The rows would land in the single-Spark ledger and read as one node."""
    with pytest.raises(SystemExit) as exc:
        server_facts.collect("pair", tasks)
    assert "--cluster-peer" in str(exc.value)
    assert CLUSTER in str(exc.value)


def test_a_single_node_backend_with_a_peer_refuses(tasks):
    """The other direction: a one-node run must not join the cluster ledger."""
    with pytest.raises(SystemExit) as exc:
        server_facts.collect("single", tasks, cluster_peer="peer")
    assert SINGLE in str(exc.value)


def test_each_backend_on_its_own_machine_passes(tasks):
    assert server_facts.collect("pair", tasks, cluster_peer="peer")["directory"] == (
        CLUSTER
    )
    assert server_facts.collect("single", tasks)["directory"] == SINGLE
