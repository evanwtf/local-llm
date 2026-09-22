"""Derive the dual-Spark cluster's machine identity, and refuse to guess it.

A cluster cannot be read off one node: both DGX Sparks probe identically, so
`hardware_id` returns the same name on each and a two-node run would append to
the single-Spark ledger with nothing out of place to a reader (#647).

The identity therefore needs a signal from outside the box. To keep the
"derived, never typed" guarantee that makes the per-machine tables
trustworthy, the signal is only a *request* -- which topology to consider --
and this module has to *confirm* it against the hardware before the name is
allowed:

1. the peer answers over SSH,
2. the peer derives the same base hardware name as this node,
3. the fabric between them carries an ACTIVE RDMA link on both ends.

If any check fails this **refuses**. It never falls back to the single-node
name, because a failed cluster run quietly recorded as single-node is exactly
the conflation #647 exists to prevent -- asking for a cluster that is not
there is an error, not a mislabelled row.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hardware_id

#: How long a probe of the peer may take. Long enough for a loaded box to
#: answer, short enough that a dead peer fails the run rather than hanging it.
PROBE_TIMEOUT_SECONDS = 30


class ClusterVerificationError(RuntimeError):
    """A cluster identity was requested and the hardware does not support it."""


def cluster_directory(base_directory: str, nodes: int) -> str:
    """`Cortex-X925-128GB-GB10` + 2 nodes -> `Cortex-X925-128GB-GB10-x2`.

    The node count is not a version: it changes only when hardware is
    physically added, so it may appear in a directory name where a driver
    version may not (`hardware/README.md`).
    """
    if nodes < 2:
        raise ValueError(f"a cluster needs at least 2 nodes, got {nodes}")
    return f"{base_directory}-x{nodes}"


def _ssh(peer: str, command: str) -> str:
    """Run one command on the peer, or raise with the reason."""
    try:
        r = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                f"-o ConnectTimeout={PROBE_TIMEOUT_SECONDS}",
                peer,
                command,
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClusterVerificationError(f"peer {peer!r} unreachable: {exc}") from exc
    if r.returncode != 0:
        raise ClusterVerificationError(
            f"peer {peer!r} failed `{command}`: {r.stderr.strip() or r.returncode}"
        )
    return r.stdout.strip()


def active_rdma_links(rdma_output: str) -> list[str]:
    """The netdev names whose RDMA link is ACTIVE, from `rdma link show`.

    Parsed rather than grepped for "ACTIVE" anywhere in the line: a device can
    report `state DOWN physical_state LINK_UP`, and a substring match on a
    whole line is how a down link gets counted as up.
    """
    active = []
    for line in rdma_output.splitlines():
        fields = line.split()
        if "state" not in fields:
            continue
        state = fields[fields.index("state") + 1]
        if state != "ACTIVE":
            continue
        if "netdev" in fields:
            active.append(fields[fields.index("netdev") + 1])
    return active


#: A hardware fingerprint built only from commands a bare DGX OS install has.
#: Deliberately not `hardware_id.py`: the peer is a machine we cluster with,
#: not necessarily a checkout of this repo, and requiring the repo and `uv` on
#: it made verification fail for a reason that had nothing to do with the
#: hardware.
FINGERPRINT_COMMAND = (
    "lscpu | sed -n 's/^Model name: *//p' | head -1; "
    "awk '/MemTotal/{printf \"%d\\n\", $2/1048576}' /proc/meminfo; "
    "nvidia-smi --query-gpu=name --format=csv,noheader | head -1"
)


def local_fingerprint() -> str:
    """This node's fingerprint, by the same command used on the peer."""
    r = subprocess.run(
        ["bash", "-c", FINGERPRINT_COMMAND],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip()


def verify_and_name(peer: str, nodes: int = 2) -> str:
    """Confirm the cluster is really there, then return its directory name.

    Raises `ClusterVerificationError` rather than returning the single-node
    name, so a broken cluster cannot be silently recorded as one Spark.
    """
    if nodes != 2:
        raise ClusterVerificationError(
            f"only a 2-node cluster is supported here, got {nodes}"
        )

    local_facts, local_platform = hardware_id.facts_for_this_machine()
    local_base = hardware_id.directory_name(local_facts, local_platform)

    mine = local_fingerprint()
    theirs = _ssh(peer, FINGERPRINT_COMMAND)
    if not mine:
        raise ClusterVerificationError("could not fingerprint this node")
    if mine != theirs:
        raise ClusterVerificationError(
            f"peer {peer!r} is different hardware.\n"
            f"  this node: {mine!r}\n"
            f"  peer:      {theirs!r}"
        )

    local_active = active_rdma_links(
        subprocess.run(
            ["rdma", "link", "show"], capture_output=True, text=True, check=False
        ).stdout
    )
    if not local_active:
        raise ClusterVerificationError(
            "no ACTIVE RDMA link on this node: the fabric is not up"
        )
    peer_active = active_rdma_links(_ssh(peer, "rdma link show"))
    if not peer_active:
        raise ClusterVerificationError(
            f"no ACTIVE RDMA link on peer {peer!r}: the fabric is not up"
        )

    return cluster_directory(local_base, nodes)
