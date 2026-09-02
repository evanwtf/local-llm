# Internal NVMe baseline

**Machine:** MacBook Pro, Apple M5 Max, 128 GiB unified memory, macOS 26.6.2.
**Measured:** 2026-08-28, quiet machine, no model server running.
**Tool:** [`scripts/disk_baseline.py`](../../scripts/disk_baseline.py). Re-runnable.

Issue #34 asks what SSD offload costs. Every answer rests on this table, and
nothing in this repo recorded it before today.

## The numbers

| pattern | median latency | p99 | IOPS | throughput |
|---|---|---|---|---|
| sequential, 4 MiB blocks | — | — | — | **9.45 GiB/s** |
| random 1 MiB | 198 µs | 241 µs | 6,476 | 6.32 GiB/s |
| random 64 KiB | 90 µs | 117 µs | 16,439 | 1.00 GiB/s |
| random 4 KiB | 61 µs | 71 µs | 25,559 | **0.10 GiB/s** |

Write, for reference: 160 GiB in 12.5 s, **12.80 GiB/s**.

Single-threaded, one read outstanding at a time. Real engines queue several, so
treat the IOPS column as a floor rather than the device's limit.

## What it means for offload

**Block size is what costs, not randomness.** Random 1 MiB reads reach 67% of
sequential bandwidth. Random 4 KiB reads reach **1.1%**. The device is not
penalising scattered access much; it is penalising *small* access. Any offload
design is really a question of how large the unit of transfer is.

That splits the three implementations in #34 cleanly:

**Streaming MoE experts (ds4 `--ssd-streaming-*`) is well matched.** An expert
block is hundreds of KiB to a few MiB, which lands in the 1 MiB row at 6.32 GiB/s
and 198 µs. With 10 of 512 experts used per token, a fully-cold token costs
~2 ms of I/O — a 500 tok/s ceiling before any compute, comfortably above the
28–40 tok/s these models decode at. Cache hits remove most of that. **The
arithmetic does not rule it out**, which is the first real evidence for it here.

**The n-gram PLE table is the hard case.** ~100 bytes per token at a random
offset cannot cost less than one block: **61 µs, not 100 bytes' worth of
bandwidth.** One lookup per token is fine (16,400/s ceiling). The question that
decides it is how many lookups a token actually needs — at 8 per token the
ceiling falls to ~2,000/s, and at prefill rates of 600 tok/s that is 30% of the
budget spent waiting on the disk. **Unmeasured, and it is the number that
matters.** Measure lookups per token before trusting the "lossless" claim in #33:
lossless in *output* is not free in *time*.

**Latency, not bandwidth, is the tail risk.** p99 sits within 20% of the median
at every block size, so this device does not have a nasty tail. A stall during
agent decode would come from queue depth or thermal state, neither of which this
single-threaded test exercises.

## How the first two attempts were wrong

Recorded because the failure is easy to repeat and it is invisible.

The first run reported a **1.0 µs median for a random 128-byte read** and random
1 MiB reads *faster than the sequential pass*. Both are impossible for a real
device; they describe the buffer cache.

Two separate causes, and fixing the first alone did not help:

1. **`F_NOCACHE` only bypasses the cache for block-aligned I/O.** Unaligned reads
   fall back silently. Offsets are now aligned to 4096.
2. **`F_NOCACHE` does not evict what the write just cached.** An 8 GiB file on a
   128 GiB machine stays resident, so reads hit RAM. `purge` fixes it in one line
   and needs sudo, which an unattended run does not have. The test file is now
   **larger than physical RAM** (160 GiB default), which needs no privileges.

The script now refuses to report a run whose median is under 10 µs or whose
sequential rate is over 20 GiB/s, because a wrong number here is worse than no
number: it would have been quoted at 50x the truth in every later argument about
offload.
