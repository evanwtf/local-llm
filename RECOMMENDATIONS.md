# Recommendations by Hardware Platform

Recommendations in this repository are strictly empirical: measured by `benchmarks/agent/` on real machines rather than taken from model cards. Because inference characteristics, memory bandwidth, and engine choices differ significantly between hardware targets, recommendations live in each hardware platform's respective directory.

Select your target hardware below:

## Supported Hardware Platforms

| Platform | Target Architecture | Memory & Specs | Recommendations Guide |
|---|---|---|---|
| **Apple Silicon Mac** | Apple Silicon (M5 Max) | 128 GB Unified Memory, macOS 26 | [`hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/RECOMMENDATIONS.md`](hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/RECOMMENDATIONS.md) |
| **NVIDIA DGX Spark** | ARM Cortex-X925 (GB10) | 128 GB Unified Memory, Linux/aarch64 (sm_121) | [`hardware/Cortex-X925-128GB-GB10/RECOMMENDATIONS.md`](hardware/Cortex-X925-128GB-GB10/RECOMMENDATIONS.md) |

---

## Machine Inventory & Methodology

- **Full Machine Registry:** See [`hardware/MACHINES.md`](hardware/MACHINES.md) for specs, topology, and testing environments of all benchmarked machines.
- **Measurement Discipline:** See [`docs/measurement-discipline.md`](docs/measurement-discipline.md) for harness parameters, warmup cycles, and metric definitions.
- **Workflow & Scenarios:** See [`docs/agent-workflow.md`](docs/agent-workflow.md) for how agent tasks and models are evaluated end-to-end.
