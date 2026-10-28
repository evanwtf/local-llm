# What to actually run

**Pick your machine.** The best local coding stack is not the same on each
machine: engine, quantization and memory budget all differ, and the fastest
stack on one may not load on another. Each machine keeps its own picks and the
evidence behind them. This page is the map.

| machine | one quickstart tip | full picks |
|---|---|---|
| **M5 Max** MacBook Pro · 128 GB · macOS | `brew install ollama && ollama pull qwen3.6:27b-coding-mxfp8`, then point OpenCode at it: a complete agent, **24/24**, 31 GB | [`hardware/MacBook-Pro-M5-Max-…/RECOMMENDATIONS.md`](hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/RECOMMENDATIONS.md) |
| **Two DGX Sparks, clustered** · 2 × GB10 · 200 Gb/s RoCE · Linux | DeepSeek-V4.1-Flash-1017 EXL3 2.7 bpw on **vLLM + ExLlamaV3** across both nodes (#1160): 41/41 replay and hard-set trials, 369 of 380 hidden tests. GLM-5.3-Flash on TensorFold v0.6.0 is the fast option: 52.9% of the pick's time on the standard set, but 72.8% of hidden tests. Served to a remote OpenCode client | [`hardware/Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md`](hardware/Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md) |
| **One DGX Spark** · GB10 · 128 GB · Linux | **Testing stopped 2026-10-23; rows are not refreshed.** Fastest: Qwen3.6-35B-A3B NVFP4 on SGLang, thinking off: 21/21 replay, 21/21 hard, 58.4% of hidden tests | [`hardware/Cortex-X925-128GB-GB10/RECOMMENDATIONS.md`](hardware/Cortex-X925-128GB-GB10/RECOMMENDATIONS.md) |
| **Ryzen + RTX 3080 Ti** · 12 GB VRAM · Linux | secondary, not always on: the 12 GB-VRAM / 30 GB-RAM MoE-offload experiment (#20); no picks file | [`hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/`](hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/RESULTS.md) |

**Numbers never cross machines.** A row from one machine is not comparable to
a row from another. Read the machine's own file, and never quote a median from
one machine on another.

## Where the rest of it went

| | |
|---|---|
| every pick stood up step by step, per machine | [`docs/howto.md`](docs/howto.md) |
| every backend's numbers, split per machine | [`docs/results.md`](docs/results.md) |
| running each M5 Max stack by hand, and the reasoning | [`docs/stacks.md`](docs/stacks.md) |
| what the benchmark does, task by task | [`benchmarks/agent/METHODOLOGY.md`](benchmarks/agent/METHODOLOGY.md) |
| the machines we manage | [`hardware/MACHINES.md`](hardware/MACHINES.md) |
| what to do next | `uv run python scripts/make_next.py --platform {macos,nvidia}` |
