# The dual DGX Spark cluster (2x GB10, 2x 128 GiB)

Two DGX Sparks cabled directly to each other over ConnectX-7, with no switch.
They serve models tensor-parallel across both nodes to an agent client on
another machine. This directory holds the cluster's results, logs and notes.
It is a **different machine** from the single DGX Spark in
`hardware/Cortex-X925-128GB-GB10/`, and rows from the two are never pooled.
See #647 for why that separation needs machinery behind it rather than a
convention.

| document | holds |
|---|---|
| [`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md) | setup, verification, and [the numbered gotchas](../../docs/dgx-cluster-setup.md#gotchas--the-full-list) |
| [`docs/dgx-cluster-howto.md`](../../docs/dgx-cluster-howto.md) | building the pair from scratch, step by step |
| [`agent-opener-prompt-dgx-cluster.md`](agent-opener-prompt-dgx-cluster.md) | the operating routine for an agent session on the head |
| [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) | what to run: **provisional**, with the rounds done per stack at the top |
| [`results.jsonl`](results.jsonl) | the two-node ledger (tier `gb10-spark-x2`) |

## Status, 2026-09-23

**In service.** Bring-up (#646) closed on 2026-09-23. Both nodes are on the
same kernel and driver, both QSFP cages are cabled, and the post-reboot checks
passed:

- two-node NCCL all-reduce: **185.60 Gb/s** median bus bandwidth at 1 GiB
  (runs of 192.07, 185.60 and 179.23). That is over the 180 Gb/s threshold,
  but **below the 196.09 Gb/s** the same two-cable fabric measured on
  2026-09-22, before node A's kernel moved to 7.0. It is one set of three
  runs, and the drop is **not explained yet**.
- a two-node vLLM launch registered its NCCL buffers with no
  `ibv_reg_mr_iova2` ENOMEM

Five models have been served across both nodes and measured from a remote
client: GLM-5.3-Flash, DeepSeek V4.1 Flash, DeepSeek-V4-Flash-Vision-Exp,
Qwen3.8-Flash-Next and MiMo-V2.6-Flash. See the ranking.

## The two nodes

| | **node A: the head** | **node B: the worker** |
|---|---|---|
| role | serves the API, holds the run lock, runs the agent session | rank 1 only; never its own queue |
| in service | before the pair existed; it also carries the single-Spark results | added 2026-09-21 |
| SoC | NVIDIA GB10 Grace Blackwell | NVIDIA GB10 Grace Blackwell |
| CPU | 20-core Arm (Cortex-X925 + Cortex-A725) | 20-core Arm (Cortex-X925 + Cortex-A725) |
| memory | 128 GiB unified (`MemTotal` 127,598,772 kB; about 121.7 GiB usable) | 128 GiB unified (`MemTotal` 127,598,832 kB) |
| root filesystem | 3.7 TiB NVMe, **76% used** (2026-09-23; pruning is #697) | 3.7 TiB NVMe, 20% used |
| fabric NIC | ConnectX-7, two QSFP cages | ConnectX-7, two QSFP cages |

Aggregate: **2x GB10, 256 GiB installed unified memory** (about 243 GiB
usable). That is the number that decides what fits. It is not one 256 GiB
pool: every byte sits on one side of the link, and a model that spans the pair
pays for each crossing. MiMo-V2.6-Flash (161 GiB of weights) is the first model
here that cannot run on one node at all.

## The fabric, 2026-09-23

**Both cages are cabled** (two NJAAKK-N911 QSFP cables), cage `p0` to `p0` and
`p1` to `p1`. Each cage is reached over two PCIe Gen5 x4 paths, so each node
has **four** fabric interfaces for two cables. Count cages, not interfaces
(`cat /sys/class/net/<iface>/phys_port_name`).

| check (2026-09-23, both nodes) | reading |
|---|---|
| interfaces | 4 per node, each 200000 Mb/s, MTU 9000 (jumbo), link up |
| RoCE (`rdma link show`) | 4 ACTIVE per node |
| ping RTT, average across the 4 subnets | 0.2–1.1 ms (the operator's threshold is < 2 ms) |
| NCCL all-reduce, 1 GiB | 185.60 Gb/s median (the threshold is > 180 Gb/s) |

The second cable is worth **+4.8%** on the collective, not a doubling, because
the PCIe budget binds before the wire does
([`docs/second-cable-dgx-spark-cluster.md`](../../docs/second-cable-dgx-spark-cluster.md)).
Addressing is four point-to-point subnets, one per interface pair. The
addresses live in each node's NetworkManager profiles and in
`docs/dgx-cluster-setup.md`, not here.

## Software, as observed 2026-09-23T08:05–08:14-0400

**The two nodes now match** on everything that affects a run. The only
difference is the factory image each was installed from, and no package
upgrade changes that.

| | node A (head) | node B (worker) |
|---|---|---|
| kernel | **7.0.0-1019-nvidia** (`linux-nvidia-hwe-24.04` 7.0.0-1019.19~24.04.2) | **7.0.0-1019-nvidia** (same package) |
| kernel command line | `kho=off` | `kho=off` |
| `CmaTotal` | 0 kB | 0 kB |
| NVIDIA driver | **580.178.04** | **580.178.04** |
| CUDA (driver API) | 13.0 | 13.0 |
| GPU VBIOS | 9A.0B.2D.00.00 | 9A.0B.2D.00.00 |
| ConnectX-7 firmware | 28.45.4028 (NVD0000000087), `mlx5_core` | 28.45.4028 (NVD0000000087), `mlx5_core` |
| DGX OTA | 7.6.0 | 7.6.0 |
| `DGX_SWBUILD_VERSION` (factory image) | 7.2.3 | 7.5.0 |
| distro | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS |
| Docker | 29.6.2 | 29.6.2 |
| nvidia-container-toolkit | 1.20.1 | 1.20.1 |
| earlyoom | 1.7, `-M 1048576,524288` (SIGTERM 1.0 GiB / SIGKILL 0.5 GiB, #700) | 1.7, the same; installed 2026-09-23 |
| `vm.swappiness` | 10 | **60**: not yet set to match (#700) |
| fan RPM in node_exporter (`nvfanread`) | loaded; Fan 0 / Fan 1 read | **not loaded**: needs a MOK-signed build (`evanwtf/dgx-spark-fan-override#14`) |
| last boot | 2026-09-23 04:55 | 2026-09-22 05:37 |

NCCL is not a host package: it comes with each serving image. The all-reduce
check ran on **NCCL 2.30.7+cuda13.3**, in `vllm/vllm-openai:qwen38-flash-next`.

**About `kho=off`.** Kernel 7.0.0-1019 builds with `CONFIG_CMA_SIZE_MBYTES=0`
and Kexec HandOver on. Without `kho=off` on the command line (package
`nvidia-spark-grub-kho`), a multi-node NCCL job fails at init with
`ibv_reg_mr_iova2` ENOMEM (NVIDIA advisory, 2026-09-14). Both nodes carry it,
and `CmaTotal: 0 kB` confirms it took effect. **6.17.0-1032-nvidia is still
installed on node A** as the GRUB fallback.

### History

| date | change |
|---|---|
| 2026-09-22T00:06 | first observed, **mismatched**. Node A: kernel 6.17.0-1032, driver 580.173.02. Node B: kernel 7.0.0-1019, driver 580.178.04. Both nodes on OTA 7.6.0. |
| 2026-09-22 | second cable added (cage `p0`). The all-reduce went from 187.10 to **196.09 Gb/s** (+4.8%; `docs/second-cable-dgx-spark-cluster.md`) |
| 2026-09-22T23:38 | node A `apt-get full-upgrade` to kernel 7.0.0-1019 and driver 580.178.04. Its GPU was unusable until reboot (`Driver/library version mismatch`). |
| 2026-09-23T04:55 | node A rebooted onto 7.0.0-1019. The post-reboot checks passed, and #646 closed. |
| 2026-09-23 | earlyoom set to 1.0 / 0.5 GiB on both nodes and installed on node B (#700) |

Versions live in this file and in the ledger rows, never in the directory
name. A directory that renames itself on a driver update breaks every link to
it. **Update the software table when either node's kernel, driver or NIC
firmware changes, and add a history row.**

## What does not vary between the nodes

Both run the operator account at **uid 1000, gid 1000**, both in `sudo`. The
NVIDIA Cluster Assistant requires the account to match on both nodes, and
changing a UID after the fact means re-owning every file in the home
directory. It matched for free because a fresh DGX OS install gives its first
account uid 1000.

Both derive the **same** canonical hardware name when probed individually.
That is the root of #647: a two-node run launched from either node would
otherwise write into the single-Spark ledger, with nothing out of place to a
reader. `scripts/server_facts.py --cluster-peer` checks the peer and emits the
cluster's name, or refuses; it never falls back to the single-node name.
