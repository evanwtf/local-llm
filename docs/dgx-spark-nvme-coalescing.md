# DGX Spark: NVMe interrupt coalescing is off (#884)

**Decision (operator, 2026-09-30):** NVMe interrupt coalescing stays **off** on
both DGX Spark nodes. NVIDIA's `nvidia-nvme-interrupt-coalescing.service` is
**masked** on each node, so it cannot turn coalescing back on at boot.

This page applies to the DGX Spark nodes only. The M5 Max and the Ryzen desktop
do not have this service.

## What the setting is

DGX OS installs the package `nvidia-nvme-options` (26.02-1 on both nodes). Its
boot service runs `/usr/bin/nvidia-nvme-interrupt-coalescing.sh enable`. The
script writes `0x00000107` to NVMe feature `0x08` (Interrupt Coalescing) on
every controller from Samsung (`0x144d`), Kioxia (`0x1e0f`) or Micron
(`0x1344`).

- `0x107` means: interrupt after **8** completions (THR = 7, 0-based) or after
  **100 µs** (TIME = 1, in units of 100 µs), whichever comes first.
- At queue depth 1 there is never more than one completion pending. So the
  threshold is never met, and **every read waits for the timer**.
- The NVMe reset value is `0` (off). The Linux `nvme` driver never sets this
  feature. The service is the only thing that turns it on.
- NVIDIA's only stated reason is that it "reduces the interrupt overhead"
  ([Grace performance tuning guide](https://docs.nvidia.com/dccpu/grace-perf-tuning-guide/optimizing-io.html)).

Both nodes have the same drive: SAMSUNG MZALC4T0HBL1-00B07, firmware NXHB202Q.
So the service applied `0x107` to both.

The lead came from a post by @wrldsuksgo2mars on 2026-09-30
([post](https://x.com/wrldsuksgo2mars/status/2105216239056589131)). It reported
~200 µs against ~56 µs for QD1 4K reads on Samsung drives.

## Who it affects

Only an engine that reads from the SSD **while it generates** can feel it.

| stack | reads the SSD while serving? |
|---|---|
| DeepSeek V4.1 Flash EXL3 (`dsv41fexl3dual2xrc`, #685) | **Yes.** Its 189 GiB Engram table is file-backed. Unpacked, every Engram miss on either node reads the head's NVMe (the worker reads it over NFS). |
| ds4 with SSD streaming on CUDA | Possibly, if we use it. Not checked. |
| GLM-5.3-Flash EXL3, TensorFold, hibrid48, Qwen3.8-FN NVFP4 | No. Weights and n-gram tables stay in memory. |
| TensorFold's session store (patch 0250) | Reads and writes the NVMe, but in large transfers, which coalescing barely affects. |

Model load time does not change: sequential reads run at the same speed either
way (see below).

## The test

`scripts/nvme_coalescing_ab.py` runs the A/B. It runs fio against existing
files, read-only, with O_DIRECT. It toggles the setting with
`systemctl stop|start nvidia-nvme-interrupt-coalescing`, reads the value back
after every toggle, and always ends with the setting back where it started. It
refuses to run while a GPU process is active.

```sh
FILES="--file $HOME/dsv41-exl3/engram-src/model-00047-of-00048.safetensors --file $HOME/dsv41-exl3/engram-src/model-00048-of-00048.safetensors"
uv run python scripts/nvme_coalescing_ab.py $FILES            # dry run: prints the plan
uv run python scripts/nvme_coalescing_ab.py $FILES --apply    # 18 min, cluster idle
```

The script toggles the service. With the service masked, `systemctl start`
fails. To repeat the test, unmask first and mask again after (see
[Reverse the decision](#reverse-the-decision)).

- **Run:** 2026-09-30T19:25 to 19:44 EDT, on the head, with the cluster idle.
- **fio:** 3.36, libaio, 60 s per job, six arms (on/off × 3).
- **Files:** the two DeepSeek V4.1 Engram shards, 2 × 101.5 GB, more than memory.
- **Results:** the raw output is in `~/bench-logs/nvme-coalescing-20260930T232547Z/` on the head.

## The result

| job | coalescing ON (`0x107`), arms 1/3/5 | coalescing OFF (`0`), arms 2/4/6 |
|---|---|---|
| QD1 4K random, p50 | 197.6 / 197.6 / 197.6 µs | 53.5 / 53.5 / 53.5 µs |
| QD1 4K random, mean | 224.2 / 218.6 / 222.6 µs | 58.6 / 59.0 / 57.0 µs |
| QD1 4K random, p99 | 757.8 / 659.5 / 716.8 µs | 148.5 / 152.6 / 138.2 µs |
| QD1 4K random, IOPS | 4,351 / 4,479 / 4,401 | 16,404 / 16,287 / 16,935 |
| QD32 4K random, IOPS | 410,070 / 409,327 / 410,029 | 434,149 / 433,796 / 436,581 |
| 1 MiB sequential, bandwidth | 10.07 / 10.07 / 10.09 GB/s | 10.16 / 10.16 / 10.15 GB/s |
| NVMe interrupts/s (QD1 · QD32 · seq) | ~4,400 · ~50,900 · ~9,600 | ~16,400 · ~350,000 · ~75,000 |

- **QD1:** with coalescing on, a median read takes 369% of the time it takes
  with coalescing off (197.6 µs against 53.5 µs). The p99 takes about 480%.
  The three arms of each state agree to within 3%.
- **QD32:** coalescing on gives 94% of the IOPS of off (410k against 434k). It
  does not buy throughput on this drive.
- **Sequential:** no difference. Model load time does not change.
- **Cost of off:** more interrupts, up to ~350k/s at QD32, over 15 I/O queues
  and 20 cores. The test did not record CPU use.

Not measured: the effect on agent wall time. For DeepSeek V4.1 EXL3 it depends
on how many Engram misses fall on the critical path. Only an end-to-end series
can measure that.

## Why off is low-risk

- **Drive wear does not change.** Wear comes from writes. Coalescing changes
  only when the drive signals a completion, not what it reads or writes. On
  2026-09-30 both drives reported `percentage_used` 0%, 100% spare and 0 media
  errors. The head had written 7.04 TB and the worker 2.96 TB.
- **The value is the default.** `0` is the NVMe reset value, and the Linux
  default on every other machine.
- **It reverses in one command,** with no reboot (see below).

## How it is applied

On each node:

```sh
sudo systemctl stop nvidia-nvme-interrupt-coalescing     # ExecStop writes 0 now
sudo systemctl disable nvidia-nvme-interrupt-coalescing  # remove the boot link
sudo systemctl mask nvidia-nvme-interrupt-coalescing     # nothing can start it
sudo systemctl daemon-reload
```

**Mask, not only disable.** A package upgrade can re-enable a disabled unit.
It cannot start a masked one. A controller reset returns the feature to its
reset value, `0`, so a reset cannot turn coalescing back on either.

## Verify

```sh
systemctl is-enabled nvidia-nvme-interrupt-coalescing    # masked
sudo nvme get-feature /dev/nvme0 -f 8                    # Current value:00000000
```

`scripts/cluster_heartbeat.py` reads feature `0x08` on both nodes every tick.
If either node reads non-zero, the heartbeat shows **NVMe coalescing ON**,
with a link to this page.

**A new or rebuilt node must get the same four commands.**
[`docs/dgx-cluster-howto.md`](dgx-cluster-howto.md) lists them, and the
heartbeat flags a node that missed them.

## Reverse the decision

```sh
sudo systemctl unmask nvidia-nvme-interrupt-coalescing
sudo systemctl enable --now nvidia-nvme-interrupt-coalescing   # writes 0x107
sudo nvme get-feature /dev/nvme0 -f 8                          # Current value:0x00000107
```
