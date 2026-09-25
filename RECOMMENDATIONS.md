# What to actually run

**Pick your machine.** This project measures three
([`hardware/MACHINES.md`](hardware/MACHINES.md)), and the best local coding
stack is not the same on each — engine, quantization and memory budget all
differ, and the fastest stack on one machine may not even load on another. Each
machine keeps its own picks, with the evidence behind them. This page is the
map; the numbers live in the per-machine files.

| machine | one quickstart tip | full picks |
|---|---|---|
| **M5 Max** MacBook Pro · 128 GB · macOS | `brew install ollama && ollama pull qwen3.6:27b-coding-mxfp8`, then point OpenCode at it — a complete agent, **24/24**, 31 GB | [`hardware/MacBook-Pro-M5-Max-…/RECOMMENDATIONS.md`](hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/RECOMMENDATIONS.md) |
| **DGX Spark** · GB10 · 128 GB · Linux | serve on the box, drive from your laptop: llama.cpp CUDA + Qwen3.8-Flash-Next `UD-Q3_K_XL`, thinking off — **331/331**, 67.3 s median (loopback; the LAN hop is on top). Fastest: the A3B on SGLang, 90/90, 28.5 s | [`hardware/Cortex-X925-128GB-GB10/RECOMMENDATIONS.md`](hardware/Cortex-X925-128GB-GB10/RECOMMENDATIONS.md) |
| **Two DGX Sparks, clustered** · 2 × GB10 · 200 Gb/s RoCE · Linux | **provisional** (GLM: 3 standard-set rounds + 1 replay round; the others 1–3 + 1): GLM-5.3-Flash EXL3 on vLLM across both nodes, **111/111** trials passed and the fastest stack in every round, served to a remote client | [`hardware/Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md`](hardware/Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md) |
| **Ryzen + RTX 3080 Ti** · 12 GB VRAM · Linux | secondary tier, not always on — the 12 GB-VRAM / 30 GB-RAM MoE-offload experiment (#20); no picks file yet | [`hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/`](hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/RESULTS.md) |

**Numbers never cross machines.** The M5 Max runs Metal / MLX, the DGX Spark
CUDA / NVFP4; a row from one is not comparable to a row from another. Read the
machine's own file, and never quote a median from one box on another.

## Where the rest of it went

| | |
|---|---|
| every backend's numbers, split per machine | [`docs/results.md`](docs/results.md) |
| running each stack by hand, and the reasoning | [`docs/stacks.md`](docs/stacks.md) |
| what the benchmark does, task by task | [`benchmarks/agent/METHODOLOGY.md`](benchmarks/agent/METHODOLOGY.md) |
| the machines we manage | [`hardware/MACHINES.md`](hardware/MACHINES.md) |
| traps that have cost a measurement | [`AGENTS.md`](AGENTS.md) · what to do next: `scripts/make_next.py --platform …` |
