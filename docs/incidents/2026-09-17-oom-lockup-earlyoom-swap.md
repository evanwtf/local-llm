# Incident: the box hard-locked again because earlyoom's swap condition was never met (2026-09-17)

**Severity:** high — the machine became unreachable (ssh and the Claude session
both dead) and recovered only by **remote-power-cycling the smart plug**. One
occurrence, 06:48 EDT.

**One-line cause:** FlashInfer JIT-compiled its NVFP4 CUTLASS kernel during
vLLM's warmup with `MAX_JOBS` unset — about 22 concurrent `nvcc` processes on
top of 69.6 GiB of already-loaded weights — and earlyoom, the one net that could
have stopped it, never fired because **its memory and swap thresholds are ANDed
and swap was 91% free**.

Two separate defects, both now fixed: the trigger (#406, uncapped JIT build) and
the net that should have caught it (#458, earlyoom config). The layer everyone
assumed was protecting the server — `MemoryMax=108G` on its systemd scope — never
applied, because CUDA allocations are not charged to a cgroup here (#456).

## Timeline (America/New_York)

From `~/bench-logs/serve-nemotron-super-20260917-063843.log` and the previous
boot's journal.

| time | event |
|---|---|
| 06:38:43 | vLLM starts under `dgx_server.py`, scope `local-llm-vllm.scope`, `MemoryMax=108G`, serving Nemotron-3-Super-120B-A12B-NVFP4 (#406) |
| 06:46:43 | `Loading weights took 462.81 seconds` |
| 06:46:53 | `Model loading took 69.62 GiB memory and 478.074599 seconds` |
| 06:47:13 | `torch.compile took 17.86 s`, then `Warming up Mamba2 SSD Triton kernels...` — **the last line the server ever wrote** |
| 06:47:19 | earlyoom: `mem avail: 43968 of 124610 MiB (35.29%), swap free: 16234 of 16383 MiB (99.09%)` |
| 06:47:47 | FlashInfer writes `build.ninja` for `fp4_gemm_cutlass_sm120` (18 CUDA translation units) |
| 06:48:02 | its first object file completes (`.ninja_log`); the remaining units build in parallel |
| 06:48:20 | earlyoom: `mem avail: 0 of 124610 MiB (0.00%), swap free: 14983 of 16383 MiB (91.45%)` — **journal ends here** |
| ~06:51 | operator power-cycles the DGX with the smart plug |
| 06:52:12 | the box boots |

**44 GiB went to zero in 61 seconds**, about 0.7 GiB/s.

## Why earlyoom did nothing

The config installed in #362 was:

```
EARLYOOM_ARGS="-m 10,5 -s 20,10 -r 60 --avoid '…' --prefer '…'"
```

`earlyoom --help`, v1.7, says plainly: *"Note: both memory and swap must be
below minimum for earlyoom to act."* At 06:48:20 memory was at **0.00%** and
swap at **91.45% free**, far above the 20% threshold, so no SIGTERM was ever
sent. With `vm.swappiness=10` and a fast GPU-side ramp, swap barely drains, so
**the swap condition essentially never trips on this box**.

The #362 validation passed because it ran `swapoff -a` first, which makes the
swap condition trivially true. That test therefore never exercised the
production configuration.

## Why `MemoryMax` did not help either (#456)

Measured live during the same load, with the server still up:

| reading | value |
|---|---|
| `nvidia-smi --query-compute-apps` — vLLM EngineCore | **71,776 MiB** |
| the scope's cgroup `memory.current` | **3,762 MiB** |
| process `VmRSS` (vllm + python + EngineCore) | ~3.1 GB |
| system `MemAvailable` | 46.6 GB of 127.6 GB |

On GB10 a CUDA allocation is not charged to the process's cgroup v2 memory
controller, so `MemoryMax=108G` bounded only the ~3.7 GB of CPU-side memory.
Docker's `--memory` uses the same controller and has the same hole, which also
answers the memory-safety claim in #450.

## Fixes landed the same day

| fix | what it does | ref |
|---|---|---|
| earlyoom is **memory-only** (`-s 100,100`) | free memory alone decides; it fired at 9.97% in a swap-on balloon test and SIGTERM'd a 108 GiB `python3` while ssh stayed up | #458 |
| earlyoom runs at `Nice=-20`, `OOMScoreAdjust=-1000` | a systemd drop-in, because earlyoom's own `-p` cannot set them under the unit's `DynamicUser` sandbox | #458 |
| `dgx_server.py` spawns a **MemAvailable watcher** | stops the server's scope below 14 GiB, above earlyoom's ~12 GiB line; it caught the second attempt at 16 GiB and the box stayed up | #456 |
| `dgx_server.py` sets **`MAX_JOBS=3`** | caps the JIT build that caused this; prebuilding the kernel in a memory-capped scope took 90 s | #456 |

**The second attempt is the evidence the watcher works.** With the same flags
plus the guard, free memory fell to 16 GiB at 07:16:48, the watcher stopped the
scope, and memory recovered to 109 GiB within three seconds. The box never went
unreachable.

## What this changes about the layers

- **`MemoryMax` is not an OOM net for a model server on this box.** It is worth
  setting — it still bounds CPU-side runaways — but the GPU side is invisible to
  it. Treat the MemAvailable watcher and earlyoom as the real nets.
- **A validation that changes the environment has not validated the
  environment.** `swapoff -a` made the #362 test pass and the production config
  fail. Re-run guards in the configuration they will actually run in.
- **A build is a memory consumer.** `nvcc`, `ninja` and `torch.compile` run
  *inside* the launch, after the weights are resident, and nothing about the
  model's own footprint predicts them.

Previous incident of the same class, different cause:
[`2026-09-13-oom-lockup.md`](2026-09-13-oom-lockup.md).
