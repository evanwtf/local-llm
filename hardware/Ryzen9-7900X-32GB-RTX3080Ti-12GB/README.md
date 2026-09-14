# Ryzen9-7900X-32GB-RTX3080Ti-12GB

**First measured run: 2026-09-02.** See [`RESULTS.md`](RESULTS.md) —
`gemma4:12b-it-q4_K_M` scored **0/12** under OpenCode.

The 2026-09-01 preliminary run was discarded: it was taken while the
environment was still being stood up, on Ollama 0.32.15 and with an incomplete
client config.

Reached over ssh as `desktop`. **Not always on.**

## Hardware

| | |
|---|---|
| CPU | AMD Ryzen 9 7900X, 12 cores / 24 threads, boost 5737 MHz |
| Memory | **32 GB installed** (2 × 16 GB DDR5-4800; 2 of 4 slots populated), 30.5 GiB usable |
| GPU | NVIDIA GeForce RTX 3080 Ti, **12,288 MiB**, compute capability **8.6** (Ampere `sm_86`) |
| Disk | 1.8 TB NVMe |
| Arch | x86_64 |

## Software, as of 2026-09-01

| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS, kernel 7.0.0-30-generic |
| NVIDIA driver | 595.71.05 (CUDA 13.2 runtime) |
| CUDA toolkit | **not installed** — no `nvcc` |
| Ollama | 0.32.15 |
| Python | 3.12.3 |
| OpenCode | 1.18.26 (the Mac runs 1.18.25) |

These are recorded here rather than in the directory name so the name stays
stable across updates. Every row also carries them via `machine_facts()`.

**Changed since, 2026-09-10: the CUDA toolkit is installed.** `cuda-toolkit-13-3`
was added so #278 could build llama.cpp for `sm_86`; `nvcc` is at
`/usr/local/cuda-13.3/bin/nvcc` and is not on the default `PATH`. The line above
is the 2026-09-01 snapshot and stays as it was. Two blockers written down while
it was absent are therefore spent -- #269's "this box has the driver but no
`nvcc`" is the one that mattered.

The tier now serves **two llama.cpp builds beside Ollama**, and they are not
interchangeable: PrismML `d8f26ee` in `~/git/llama.cpp` carries the 2-bit
kernels the bonsai arms need, and upstream `91f6a6c` in
`~/git/llama.cpp-upstream` is the only one that knows the `spark2_5`
architecture. Each backend in `tasks.toml` names its own `engine_tree`, so a
row can say which build answered.

## What this machine can and cannot do

**No FP8 and no NVFP4.** Ampere `sm_86` lacks the hardware, so every
`nvidia/*-FP8` and `*-NVFP4` release is unusable. GGUF/CUDA or EXL2 only.

**No MLX**, so most of the model list in `TESTING-SET.md` is Mac-only.

**No sandbox.** `sandbox-exec` is macOS-only, so `workspace_escapes` is
unenforced here and rows carry weaker guarantees than the Mac's. Every row
records `confinement: none` (#81).

**12 GiB of VRAM against 32 GB of system RAM** is the shape that makes MoE
CPU-offload interesting: the live experiment is `ornith-1.5:35b` — 22 GB with
3B active — streamed with `--n-cpu-moe`. It is the only candidate where the
model is already proven (21/21 on the Mac) and only the hardware is in
question. See #20 and #79.
