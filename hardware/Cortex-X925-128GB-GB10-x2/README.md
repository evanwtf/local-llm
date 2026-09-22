# The dual DGX Spark cluster (2x GB10, 2x 128 GiB)

Two DGX Sparks cabled directly to each other over ConnectX-7 at 200 Gb/s, with
no switch. This directory holds the cluster's results, logs and notes. It is a
**different machine** from the single DGX Spark in
`hardware/Cortex-X925-128GB-GB10/`, and rows from the two are never pooled —
see #647 for why that separation needs machinery behind it rather than a
convention.

**Setup, verification and failure modes:
[`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md).** That is the operating document; this
one records what the hardware is.

## Status

**Not yet assembled.** One 200 Gb/s cable is connected and its link carries
signal, but no interface holds an address and nothing has been served across
the pair. Bring-up is #646. Nothing in this directory is a measurement yet.

**One cable, not two.** Two NJAAKK-N911 cables were bought; one is plugged in,
between QSFP cage `p1` on each node. The pair's ceiling is therefore a single
200 Gb/s link. See [`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md) for why four
ethernet interfaces exist for two cages, and why two of them being up does not
mean two cables.

## The two nodes

Referred to throughout as **node A** (the first Spark, in service since before
the pair existed, and the box that already carries this project's single-Spark
results) and **node B** (added 2026-09-21). Which one ends up as the serving
head is a bring-up decision, recorded in `CLUSTER-SETUP.md` once made — it is
not implied by A and B.

| | node A | node B |
|---|---|---|
| SoC | NVIDIA GB10 Grace Blackwell | NVIDIA GB10 Grace Blackwell |
| CPU | 20-core Arm (Cortex-X925 + Cortex-A725) | 20-core Arm (Cortex-X925 + Cortex-A725) |
| Memory | 128 GiB unified (121.7 GiB usable) | 128 GiB unified (121.7 GiB usable) |
| Root filesystem | 3.7 TiB NVMe | 3.7 TiB NVMe |
| Fabric NIC | ConnectX-7, two QSFP ports | ConnectX-7, two QSFP ports |

Aggregate: **2x GB10, 256 GiB installed unified memory** (about 243 GiB
usable), which is the number that decides what will fit. It is not one 256 GiB
pool — every byte is on one side of a 200 Gb/s link, and a model that spans the
pair pays for each crossing.

## Software, as observed 2026-09-22T00:06-0400

**The two do not match, and #646 treats that as the first task.**

| | node A | node B |
|---|---|---|
| `DGX_SWBUILD_VERSION` | 7.2.3 | 7.5.0 |
| `DGX_SWBUILD_DATE` | 2025-09-10 | 2026-03-23 |
| kernel | 6.17.0-1032-nvidia | 7.0.0-1019-nvidia |
| distro | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS |

Node A is the one that moves. Its signed out-of-tree `nvfanread` hwmon module
(fan RPM into node_exporter) has to be rebuilt and re-signed against the new
kernel, or the fan series disappears from Prometheus without an error.

Versions live in this file and in the ledger rows, never in the directory
name — a directory that renames itself on a driver update breaks every link to
it.

## What does not vary between the nodes

Both run the operator account at **uid 1000, gid 1000**, both in `sudo`. The
NVIDIA Cluster Assistant requires the account to match on both nodes, and
changing a UID after the fact means re-owning every file in the home
directory. It matched for free because a fresh DGX OS install gives its first
account uid 1000.

Both derive the **same** canonical hardware name when probed individually,
which is the root of #647: a two-node run launched from either node would
otherwise write into the single-Spark ledger with nothing out of place to a
reader.
