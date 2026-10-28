# Ryzen9-7900X-32GB-RTX3080Ti-12GB

**Status: registered, idle since 2026-09-19.** The operator powers the box on
for a window; it is not always on. Its last rows are the first confined
(`bwrap`) baseline of the 9B tier on the 10-task suite
([#561](https://github.com/evanwtf/local-llm/issues/561)). Its open queue is
the issues labeled `hardware:Ryzen9-7900X-RTX3080Ti` (all P2 or P3). How an agent operates
it: §11 of [`hardware/agent-opener-prompt.md`](../agent-opener-prompt.md).

Results: [`docs/results.md`](../../docs/results.md) holds the current tables;
[`RESULTS.md`](RESULTS.md) holds the 2026-09-02 to 09-12 write-ups.

## Hardware

| | |
|---|---|
| CPU | AMD Ryzen 9 7900X, 12 cores / 24 threads, boost 5737 MHz |
| Memory | **32 GB installed** (2 × 16 GB DDR5-4800; 2 of 4 slots populated), 30.5 GiB usable |
| GPU | NVIDIA GeForce RTX 3080 Ti, **12,288 MiB**, compute capability **8.6** (Ampere `sm_86`) |
| Disk | 1.8 TB NVMe |
| Arch | x86_64 |

## Software

Snapshot of 2026-09-01, with later changes noted. Every row carries the
versions it ran on (`machine_facts()`), so the directory name stays stable.

| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS, kernel 7.0.0-30-generic |
| NVIDIA driver | 595.71.05 (CUDA 13.2 runtime) |
| CUDA toolkit | `cuda-toolkit-13-3`, added 2026-09-10 for #278. `nvcc` is at `/usr/local/cuda-13.3/bin/nvcc`, not on the default `PATH` |
| Python | 3.12.3 |

**Two llama.cpp builds serve beside Ollama, and they are not
interchangeable.** PrismML `d8f26ee` in `~/git/llama.cpp` carries the 2-bit
kernels the Bonsai arms need. Upstream `91f6a6c` in `~/git/llama.cpp-upstream`
is the only one that knows the `spark2_5` architecture. Each backend in
`tasks.toml` names its own `engine_tree`, so a row says which build answered.

## What this machine can and cannot do

**No FP8 and no NVFP4.** Ampere `sm_86` lacks the hardware, so every
`nvidia/*-FP8` and `*-NVFP4` release is unusable. GGUF/CUDA or EXL2 only.

**No MLX**, so most of the model list in `TESTING-SET.md` is Mac-only.

**Confinement is `bwrap`, once the AppArmor profile is loaded** (#476, #477).
Ubuntu 24.04 restricts the user namespaces `bwrap` needs; without the profile
every trial silently scored as a model failure (#516). Load the profile in
[`setup/`](setup/README.md). Rows then record `confinement: bwrap`; if `bwrap`
cannot start, `run.py` falls back to `confinement: none` with a warning.
Rows before 2026-09-18 predate `bwrap` and record `confinement: none` (#81).

**12 GiB of VRAM against 32 GB of system RAM** is the shape that makes MoE
CPU offload worth measuring (#20). The candidate named for it is
`ornith-1.5:35b` (22 GB, 3B active) streamed with `--n-cpu-moe`: the model is
proven on the Mac (21/21), so only the hardware is in question. No row has
measured it on this box yet.
