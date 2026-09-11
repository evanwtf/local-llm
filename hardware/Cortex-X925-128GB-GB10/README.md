# Cortex-X925-128GB-GB10

**An NVIDIA DGX Spark (GB10 Grace-Blackwell, 128 GB unified).** The third
machine to run this harness. First rows taken 2026-09-11; see
[`RESULTS-agent.md`](RESULTS-agent.md) for what they support.

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
| Disk | 3.7 TB NVMe (`/dev/nvme0n1p2`) |

## Software

Recorded by `machine_facts()` on every row; repeated here because the engine
builds are not derivable from anything else.

| | |
|---|---|
| OS | Ubuntu 24.04.5 LTS, Linux 6.17.0-1032-nvidia |
| CUDA | toolkit 13.0.88 (`nvcc`), driver 580.173.02 |
| Ollama | 0.34.0 — loads `cuda_v13`; the bundled CUDA 12 build lacks sm_121 and is skipped automatically, which is correct behavior and not a warning to chase |
| llama.cpp | `0.4.0-dev (build 50, commit 481c65f)`, built `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121`. From **master**, not a release: v0.4.0 predates ggml-org/llama.cpp#27742, the Qwen3.8-Flash-Next merge |
| ds4 | `make cuda-spark` — `-gencode arch=compute_121a,code=sm_121a -DDS4_CUDA_HAVE_MXF4=1` |
| OpenCode | 1.18.30 |

### One model resident at a time — enforced

Ollama keeps a model loaded for 5 minutes after use. On a discrete-VRAM box
that is a convenience; here a model held on the GPU **is** system memory, so
testing a second model while the first is still warm put 26 GB + 19 GB in the
pool at once and pushed the machine into memory pressure. Pinned by a systemd
drop-in:

```
/etc/systemd/system/ollama.service.d/10-single-model.conf
[Service]
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

It is part of the serving stack and belongs in the provenance of any row taken
here. Two related lessons, both learned the hard way:

- **Do not restart the ollama service while a pull is in flight.** It killed an
  86 GB download at 98% with `Error: unexpected EOF` during digest verification.
- **Weight size alone does not predict what fits.** DeepSeek-V4-Flash Q2 is
  80.76 GiB of weights with **1.64 GiB** of compressed KV at 100k context;
  Qwen3.8-Flash-Next Q3 is 83.81 GiB with roughly **9 GiB** at 131k, and had to
  have llama.cpp's default four slots cut to one to avoid an OOM kill. Three
  GiB apart in weights, five-fold apart in KV.

## What this machine can and cannot do

_To confirm on the machine; stated here as the architecture's properties so the
first run knows what to reach for, not as measured facts._

- **Blackwell supports FP8 and FP4 (NVFP4) in hardware**, unlike the Ampere
  `sm_86` Ryzen box — but **NVFP4 is not reachable through Ollama on Linux**,
  which is the first thing this machine was asked and the answer is no (#293).
  The `nvfp4` and `mxfp8` tags ship as sharded `vnd.ollama.image.tensor` layers
  that Ollama routes to the MLX runner, and the Linux arm64 build **ships no MLX
  runtime**: the runner's symbols are compiled into the binary, the library is
  absent. The pull fails before any weights move. Reaching NVFP4 means a
  CUDA-native engine — TensorRT-LLM or vLLM (#299). Re-check on every Ollama
  release; a maintainer has MLX-on-Linux running unofficially at +20% decode
  over GGUF.
- **Unified 128 GB** puts it in the same size class as the M5 Max, so the model
  list is closer to the Mac's than to the Ryzen box's. The interesting overlap
  is models measured on the M5 Max that can now be compared across two different
  128 GB unified architectures (Metal vs CUDA).
- **No `sandbox-exec`** (that is macOS-only), so `workspace_escapes` is
  unenforced as on the Ryzen box; rows should record `confinement: none` (#81).
- **No MLX** — the MLX entries in `TESTING-SET.md` stay Mac-only; this machine
  runs CUDA and GGUF.
- **No Swift oracle.** `~/git/monitor` is an AppKit desktop application, so
  `swift test` cannot judge anything off a Mac. The five Swift excisions are
  gated `platform = "darwin"` and skipped, leaving a missing cell rather than a
  zero — this machine's full matrix is **10 tasks**, and any cross-machine
  comparison must be Python-only or it compares a subset to a whole (#294).
- **No ds4 Metal route.** The Metal tensor-route equivalence gate (#149) has
  nothing to check in a CUDA build; it used to refuse runs here and is now
  gated on macOS.

## Firmware and version inventory

[`VERSIONS.md`](VERSIONS.md) records every component on the DGX Spark whose
version could move a number -- NVIDIA driver and GSP firmware, VBIOS, CUDA,
kernel, system firmware, embedded controller, memory speed, NVMe firmware, CPU
governor. Taken once, 2026-09-11T07:55:34-0400.

Rows already carry the software half through `machine_facts()`. The firmware
half is on no row, so a run on driver 580.173.02 and a later, faster run on a
595.x driver would be indistinguishable from the ledger alone. Re-take
VERSIONS.md after any driver, firmware or kernel change.

## Measured here

See [`RESULTS-agent.md`](RESULTS-agent.md). In short, OpenCode on the 10-task
matrix: Qwen3.8-Flash-Next `UD-Q3_K_XL` 30/30 at a 108.2s median,
Qwen3.6-27B-coding GGUF 29/30 at 131.0s, DeepSeek-V4-Flash Q2 20/20 at 213.2s
over two trials. ds4's decode reproduces upstream's published GB10 figure to
0.5% (19.62 t/s against 19.72).

## Access

_To record: hostname/ssh alias and whether it is always on._ See the Ryzen
README for the shape (`desktop`, not always on).
