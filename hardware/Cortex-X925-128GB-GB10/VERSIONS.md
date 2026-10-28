# Cortex-X925-GB10 — firmware and version inventory

Everything on the DGX Spark whose version could move a number, recorded
**2026-09-11T07:55:34-0400**, before the first rows.

This is a **point-in-time snapshot.** Nothing regenerates or verifies it. Rows
carry the software half (OS, kernel, driver, engine builds, client versions)
through `machine_facts()`; **the firmware half is on no row**, which is why
this file exists. The Spark is now node A of the two-Spark cluster, and the
[cluster README](../Cortex-X925-128GB-GB10-x2/README.md) keeps the current
versions of both nodes. Re-take this file after any driver, firmware or kernel
change, and say so in the issue for the run.

**Changed since this snapshot** (read 2026-10-28):

| component | 2026-09-11 | 2026-10-28 |
|---|---|---|
| NVIDIA driver and GSP firmware | 580.173.02 | 580.178.04 |
| kernel | 6.17.0-1032-nvidia | 7.0.0-1019-nvidia |
| DGX OTA | — | 7.6.0 (factory image still 7.2.3) |
| kernel command line | as below | adds `kho=off` |
| ConnectX-7 | not enumerated (no cable) | cabled, firmware 28.50.1002 |

The VBIOS, CUDA toolkit, BIOS, governor, gcc, glibc and Python rows below read
the same on 2026-10-28.

---

## The ones most likely to move a benchmark

| component | version | why it matters |
|---|---|---|
| **NVIDIA driver** | **580.173.02** (`nvidia-driver-580-open`, `580.173.02-0ubuntu0.24.04.1`) | The single most likely thing to change a decode rate between two otherwise identical runs |
| **GSP firmware** | 580.173.02 | Ships with the driver; the GPU's own scheduler runs on it |
| **GPU VBIOS** | 9A.0B.2D.00.00 | Power and clock behavior |
| **CUDA toolkit** | 13.0.88 (`cuda-toolkit-13-0` 13.0.3-1) | What engines are compiled against. llama.cpp and ds4 here are both built locally against it |
| **Kernel** | 6.17.0-1032-nvidia (`#32-Ubuntu SMP PREEMPT_DYNAMIC`, built 2026-08-19) | NVIDIA-flavored kernel; memory management matters more than usual on unified memory |
| **System firmware / BIOS** | AMI `5.36_0ACUM018`, dated 2025-08-06 | Memory training and power limits |
| **Embedded controller** | `0x03000508` | Thermal and power policy |
| **DGX OS** | 7.2.3 (`DGX_SWBUILD_DATE=2025-09-10`, commit `833b4a7`) | The platform image the rest of this sits on |

## Platform

| | |
|---|---|
| Product | `NVIDIA_DGX_Spark`, version `A.7` |
| Board | NVIDIA `P4242`, revision `A04` |
| Chassis / system vendor | NVIDIA |
| OS | Ubuntu 24.04.5 LTS (noble), aarch64 |

## CPU

| | |
|---|---|
| Model | Arm Cortex-X925, 20 cores (10 per socket), 1 thread per core |
| Clocks | 1378–3900 MHz |
| **Governor** | **`performance`**, driver `cppc_cpufreq` |

The governor is worth stating on every comparison: `performance` pins clocks
high, and a machine that came back from an update on `schedutil` would look
slower for reasons that have nothing to do with the model.

## GPU

| | |
|---|---|
| Device | NVIDIA GB10 (Blackwell), compute capability **12.1** (`sm_121`; ds4 builds `sm_121a`) |
| Max SM clock | 3003 MHz |
| Persistence mode | **Enabled** |
| Compute mode | Default |
| MIG | not in use |
| Virtualization | None |

`nvidia-smi` reports `[N/A]` for `memory.total`, `power.max_limit` and
`clocks.max.memory` on this part — memory is unified and not a separate pool it
can report. Engines see it correctly: Ollama reports 121.7 GiB total / 117.8
GiB available as an **iGPU**, and llama.cpp reports `CUDA0: NVIDIA GB10
(124610 MiB, 121166 MiB free)`.

## Memory

| | |
|---|---|
| Installed | 128 GB LPDDR5, **unified** (CPU and GPU share it) |
| Speed | 8533 MT/s (configured 8533 MT/s) |
| Visible to OS | 121.7 GiB |
| ECC | None |
| Transparent hugepages | `madvise` |

Bandwidth is shared between CPU and GPU, so a memory-speed change would move
both prefill and decode. Worth re-reading after any firmware update, since
memory training happens there.

## Storage

| | |
|---|---|
| Device | Samsung `MZALC4T0HBL1-00B07`, 4.10 TB NVMe |
| **Firmware** | **NXHB202Q** |
| Format | 512 B sectors |

Model loading is disk-bound on first touch; an 84 GiB model is a real read.
Warm runs are not, so this matters for the first trial of a batch more than the
rest.

## Networking

| | |
|---|---|
| Ethernet | Realtek 8127 (rev 05) |
| Wireless | MediaTek 7925 |
| ConnectX-7 | **not enumerated** in `lspci` on 2026-09-11: it appears only with a cable attached ([gotcha 2](../../docs/dgx-cluster-setup.md#gotchas--the-full-list)) |

## Toolchain

| | |
|---|---|
| gcc | 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1) |
| glibc | 2.39 (Ubuntu GLIBC 2.39-0ubuntu8.9) |
| Python | 3.12.3 |

Both local engine builds (llama.cpp `481c65f`, ds4 `make cuda-spark`) went
through this gcc. A toolchain change is a rebuild, and a rebuild is a new
engine version for provenance purposes.

**One engine here was not built on this machine.** Installed 2026-09-11 via
`curl -LsSf https://llama.app/install.sh | sh`:

| | |
|---|---|
| binary | `~/.local/bin/llama`, 516 MB, single static file |
| version | `0.4.0-dev` build **10900**, commit `50182a53f` |
| built with | GNU 12.3.0 for Linux aarch64 — **not** the 13.3.0 above |
| CUDA | installer probed the device and selected `121`, matching `sm_121` |

It is a prebuilt upstream artifact, so the gcc row in this table does not
describe it and no local rebuild can reproduce it. That matters for reading a
row: `llama.cpp 481c65f` and `llama.cpp 50182a53f` are different compilers as
well as different commits, and only the latter is upstream's own build. Recorded
before any row cites it, because the two are otherwise indistinguishable in a
log line that says only "llama.cpp".

## Secure boot and signing

From `fwupdmgr`: DGX Spark Platform Key 2025, KEK CA 2023, UEFI CA 2023,
Windows UEFI CA 2023, UEFI dbx 20230501, SBAT 1.5.4, UEFI Device Firmware
`0x02009b0b` and `0x00000001`.

No performance bearing. Recorded because a firmware update that changes these
is the same event that changes the ones above.

## Kernel command line

```
init_on_alloc=0 iommu.passthrough=0 crashkernel=1G-:0M pci=pcie_bus_safe
initcall_blacklist=tegra234_cbb_init earlycon=uart,mmio32,0x16A00000
console=tty0 console=ttyS0,921600 quiet splash vt.handoff=7
```

`init_on_alloc=0` and `iommu.passthrough=0` are the two with plausible
performance bearing, and both are platform defaults rather than anything set
here.

## How to re-take this

No script generates this file (#304). The commands used:

```sh
date +%Y-%m-%dT%H:%M:%S%z
uname -srvmo; grep PRETTY_NAME /etc/os-release
for f in /sys/class/dmi/id/{sys_vendor,product_name,product_version,board_name,board_version,bios_version,bios_date}; do echo "$f: $(cat $f)"; done
nvidia-smi --query-gpu=name,driver_version,vbios_version,compute_cap,clocks.max.sm --format=csv
nvidia-smi -q | grep -E "Persistence Mode|Compute Mode|GSP Firmware"
nvcc --version; lscpu; cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
sudo dmidecode -t memory; sudo nvme list; cat /etc/dgx-release
dpkg -l | grep -E "nvidia-(driver|firmware|kernel|utils)|cuda-toolkit"
sudo fwupdmgr get-devices; cat /proc/cmdline
```
