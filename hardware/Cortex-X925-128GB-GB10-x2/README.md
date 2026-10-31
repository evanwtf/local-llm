# The dual DGX Spark cluster (2x GB10, 2x 128 GiB)

Two DGX Sparks cabled directly to each other over ConnectX-7, with no switch.
They serve models tensor-parallel across both nodes to an agent client on
another machine. This directory holds the cluster's results, logs and notes.
It is a **different machine** from the single DGX Spark in
`hardware/Cortex-X925-128GB-GB10/`, and rows from the two are never pooled
(#647 says why that needs machinery, not a convention). Node A was the single
Spark; single-Spark testing stopped on 2026-10-23, and the cluster is the
active DGX machine.

| document | holds |
|---|---|
| [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) | what to run, and the tests behind it |
| [`howto.md`](howto.md) | each recommended stack, step by step from two clean Sparks |
| [`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md) | the fabric layer by layer, and [the numbered gotchas](../../docs/dgx-cluster-setup.md#gotchas--the-full-list) |
| [`docs/dgx-cluster-howto.md`](../../docs/dgx-cluster-howto.md) | building the pair from scratch, step by step |
| [`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md) | operating a Spark: memory nets, ports, Prometheus |
| [`../agent-opener-prompt.md`](../agent-opener-prompt.md), §10 | the operating routine for an agent session on the head |
| [`results.jsonl`](results.jsonl) | the two-node ledger (tier `gb10-spark-x2`) |

## The two nodes

| | **node A: the head** | **node B: the worker** |
|---|---|---|
| role | serves the API, holds the run lock, runs the agent session | rank 1 only; never its own queue |
| in service | before the pair existed; it also carries the single-Spark results | added 2026-09-21 |
| SoC | NVIDIA GB10 Grace Blackwell | NVIDIA GB10 Grace Blackwell |
| CPU | 20-core Arm (Cortex-X925 + Cortex-A725) | 20-core Arm (Cortex-X925 + Cortex-A725) |
| memory | 128 GiB unified (`MemTotal` 127,598,768 kB; about 121.7 GiB usable) | 128 GiB unified (`MemTotal` 127,598,832 kB) |
| root filesystem | 3.7 TiB NVMe, 65% used (2026-10-28) | 3.7 TiB NVMe, 55% used (2026-10-28) |
| fabric NIC | ConnectX-7, two QSFP cages | ConnectX-7, two QSFP cages |

Aggregate: **2x GB10, 256 GiB installed unified memory** (about 243 GiB
usable). That number decides what fits. It is not one 256 GiB pool: every
byte sits on one side of the link, and a model that spans the pair pays for
each crossing. MiMo-V2.6-Flash (161 GiB of weights) was the first model here
that cannot run on one node at all (#672).

## The fabric

**Both cages are cabled** (two NJAAKK-N911 QSFP cables), cage `p0` to `p0` and
`p1` to `p1`. Each cage is reached over two PCIe Gen5 x4 paths, so each node
has **four** fabric interfaces for two cables. Count cages, not interfaces
(`cat /sys/class/net/<iface>/phys_port_name`).

| check | reading | threshold |
|---|---|---|
| interfaces (2026-10-28) | 4 per node, each 200000 Mb/s, MTU 9000, link up | — |
| RoCE, `rdma link show` (2026-10-28) | 4 ACTIVE per node | — |
| ping RTT, average per subnet (2026-10-28) | 0.59–1.12 ms | < 2 ms |
| NCCL all-reduce, 1 GiB (2026-09-23) | 185.60 Gb/s median (runs 192.07, 185.60, 179.23) | > 180 Gb/s |

The all-reduce read 196.09 Gb/s on 2026-09-22, before node A moved to kernel
7.0. The drop to 185.60 is one set of three runs and has no explanation on
record. The second cable is worth +4.8% on the collective, not a doubling,
because the PCIe budget binds before the wire does
([`docs/second-cable-dgx-spark-cluster.md`](../../docs/second-cable-dgx-spark-cluster.md)).
Addressing is four point-to-point subnets, one per interface pair
([`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md), Layer 2).

## Software, as observed 2026-10-28

The two nodes match on everything that affects a run. The only difference is
the factory image each was installed from, and no package upgrade changes it.

| | node A (head) | node B (worker) |
|---|---|---|
| kernel | 7.0.0-1019-nvidia (`linux-nvidia-hwe-24.04`, 7.0.0-1019.19~24.04.2) | the same |
| kernel command line | `kho=off` | `kho=off` |
| `CmaTotal` | 0 kB | 0 kB |
| NVIDIA driver | 580.178.04 | 580.178.04 |
| CUDA (driver API) | 13.0 | 13.0 |
| GPU VBIOS | 9A.0B.2D.00.00 | 9A.0B.2D.00.00 |
| ConnectX-7 firmware | 28.50.1002 (NVD0000000087), `mlx5_core` | 28.50.1002 (NVD0000000087), `mlx5_core` |
| DGX OTA | 7.6.0 | 7.6.0 |
| `DGX_SWBUILD_VERSION` (factory image) | 7.2.3 | 7.5.0 |
| distro | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS |
| Docker | 29.6.2 | 29.6.2 |
| nvidia-container-toolkit | 1.20.1 | 1.20.1 |
| earlyoom | 1.7, `-M 1048576,524288` (SIGTERM 1.0 GiB, SIGKILL 0.5 GiB, #700) | the same |
| `vm.swappiness` | 10 (`/etc/sysctl.d/99-dgx-oom.conf`, #362) | the same (set 2026-10-29) |
| GPU clock cap | 1,900 MHz from boot (`gpu-clock-cap.service`, [`systemd/`](systemd/gpu-clock-cap.service); #1319, set 2026-10-31) | the same |
| fan RPM in node_exporter (`nvfanread`) | loaded | **not loaded**: needs a MOK-signed build (`evanwtf/dgx-spark-fan-override#14`, open) |
| last boot | 2026-10-22 | 2026-10-22 |

NCCL is not a host package: each serving image brings its own. The
2026-09-23 all-reduce ran on NCCL 2.30.7+cuda13.3, in
`vllm/vllm-openai:qwen38-flash-next`.

**Why `kho=off`.** Kernel 7.0.0-1019 builds with `CONFIG_CMA_SIZE_MBYTES=0`
and Kexec HandOver on. Without `kho=off` on the command line (package
`nvidia-spark-grub-kho`), a multi-node NCCL job fails at init with
`ibv_reg_mr_iova2` ENOMEM (NVIDIA advisory, 2026-09-14). `CmaTotal: 0 kB`
confirms it took effect. Node A keeps 6.17.0-1032-nvidia installed as the
GRUB fallback.

Node A came up on kernel 6.17.0-1032 and driver 580.173.02. It moved to the
node B versions on 2026-09-23 (#646); the route is
[gotcha 12](../../docs/dgx-cluster-setup.md#gotchas--the-full-list).

Versions live in this file and in the ledger rows, never in the directory
name: a directory that renames itself on a driver update breaks every link
to it. **When either node's kernel, driver or NIC firmware changes, update
the software table and its date.**

## What does not vary between the nodes

Both run the operator account at **uid 1000, gid 1000**, in `sudo`. The NVIDIA
Cluster Assistant requires the account to match on both nodes, and a UID
change after the fact means re-owning every file in the home directory. A
fresh DGX OS install gives its first account uid 1000.

Both derive the **same** canonical hardware name when probed one at a time.
That is the root of #647: without a guard, a two-node run launched from either
node would write into the single-Spark ledger, with nothing to show a reader.
`scripts/server_facts.py --cluster-peer` checks the peer and emits the
cluster's name, or refuses; it never falls back to the single-node name.
