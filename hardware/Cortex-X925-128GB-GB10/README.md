# Cortex-X925-128GB-GB10

**An NVIDIA DGX Spark (GB10 Grace-Blackwell, 128 GB unified).** The third
machine to run this harness. No rows yet — this directory is created ahead of
its first run (#292) so the Spark records straight onto `main`.

Name derived by `scripts/hardware_id.py`, not typed:

```
Cortex-X925-128GB-GB10
  cpu: Cortex-X925   memory_gb: 128   gpu: NVIDIA GB10   (20 cores, 121.7 GiB visible)
```

`128GB` is the installed size per the naming convention; the OS sees 121.7 GiB.

## The two things the name cannot say

**This is a DGX Spark.** A GB10 Grace-Blackwell superchip: 20 Arm Cortex-X925/A725
cores beside a Blackwell GPU on one package.

**The 128 GB is unified, not discrete VRAM.** CPU and GPU share one LPDDR5X
pool. **Every candidates-by-VRAM judgement in this repo assumes a discrete VRAM
ceiling and is wrong here** — in the same way it is wrong on the M5 Max. A model
is sized against the whole 128 GB (less the working set), not against a separate
card. Do not carry the Ryzen box's "12 GiB VRAM vs 32 GB RAM" framing over:
there is no such split here.

## Hardware

| | |
|---|---|
| CPU | Arm Cortex-X925 (Grace), 20 cores |
| Memory | **128 GB installed, unified** (LPDDR5X, shared CPU/GPU), 121.7 GiB visible |
| GPU | NVIDIA GB10 (Blackwell). Our issues treat it as `sm_121` (#281) |
| Arch | aarch64 |
| Disk | _to record on first run_ |

## Software

_Recorded on the machine's first run via `machine_facts()`, not guessed here:_
OS and kernel, NVIDIA driver and CUDA runtime, CUDA toolkit (`nvcc`) presence,
the engines built and their versions, Python, and the client version. Versions
live in the files and on every row, never in the directory name, so the name
stays stable across updates.

## What this machine can and cannot do

_To confirm on the machine; stated here as the architecture's properties so the
first run knows what to reach for, not as measured facts._

- **Blackwell supports FP8 and FP4 (NVFP4) in hardware**, unlike the Ampere
  `sm_86` Ryzen box. The `nvidia/*-FP8` and `*-NVFP4` releases that are unusable
  on both the Mac (Metal) and the Ryzen box should run here — worth confirming
  first, as it is the one capability this machine adds to the fleet.
- **Unified 128 GB** puts it in the same size class as the M5 Max, so the model
  list is closer to the Mac's than to the Ryzen box's. The interesting overlap
  is models measured on the M5 Max that can now be compared across two different
  128 GB unified architectures (Metal vs CUDA).
- **No `sandbox-exec`** (that is macOS-only), so `workspace_escapes` is
  unenforced as on the Ryzen box; rows should record `confinement: none` (#81).
- **No MLX** — the MLX entries in `TESTING-SET.md` stay Mac-only; this machine
  runs CUDA and GGUF.

## Access

_To record: hostname/ssh alias and whether it is always on._ See the Ryzen
README for the shape (`desktop`, not always on).
