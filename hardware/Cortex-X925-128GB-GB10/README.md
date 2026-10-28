# Cortex-X925-128GB-GB10

**An NVIDIA DGX Spark (GB10 Grace-Blackwell, 128 GB unified).** It took its
first rows on 2026-09-11. It is now **node A, the head, of the two-Spark
cluster** ([`../Cortex-X925-128GB-GB10-x2/`](../Cortex-X925-128GB-GB10-x2/README.md)).
Single-Spark testing stopped on 2026-10-23. This directory keeps the
single-Spark ledger and its results; the cluster docs are the current record
for the box's versions, network and operation.

| for | see |
|---|---|
| what ran here, and how to stand it up | [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md), [`howto.md`](howto.md) |
| the first rows (2026-09-11) and what they support | [`RESULTS-agent.md`](RESULTS-agent.md) |
| firmware and versions on 2026-09-11 | [`VERSIONS.md`](VERSIONS.md) |
| MiniMax-H3 video generation | [`H3-VIDEO-GEN.md`](H3-VIDEO-GEN.md) |
| current kernel, driver and firmware | [the cluster README](../Cortex-X925-128GB-GB10-x2/README.md) |
| operating the box | [`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md) |

Name derived by `scripts/hardware_id.py`, not typed:

```
Cortex-X925-128GB-GB10
  cpu: Cortex-X925   memory_gb: 128   gpu: NVIDIA GB10   (20 cores, 121.7 GiB visible)
```

`128GB` is the installed size per the naming convention; the OS sees
121.7 GiB.

## Mission: multipurpose, unlike the Mac

The M5 Max exists for one thing: a local coding agent. The Spark's job is
broader (set 2026-09-11): a multipurpose LLM server, a coding backend served
over the network to other machines (#308), an agent runtime (#310), and
vision for security-camera footage (#309). So the models under test reach
beyond coding, and **aggregate multi-stream throughput matters more than
single-stream tok/s** here, the opposite emphasis from the Mac.

## The two things the name cannot say

**This is a DGX Spark.** A GB10 Grace-Blackwell superchip: 20 Arm
Cortex-X925/A725 cores beside a Blackwell GPU on one package.

**The 128 GB is unified, not discrete VRAM.** CPU and GPU share one LPDDR5X
pool. **Every candidates-by-VRAM judgement in this repo assumes a discrete
VRAM ceiling and is wrong here**, as it is on the M5 Max. Size a model against
the whole 128 GB less the working set. Do not carry over the Ryzen box's
"12 GiB VRAM vs 32 GB RAM" framing.

## Hardware

| | |
|---|---|
| CPU | Arm Cortex-X925 (Grace), 20 cores |
| Memory | **128 GB installed, unified** (LPDDR5X, shared CPU/GPU), 121.7 GiB visible |
| GPU | NVIDIA GB10 (Blackwell), compute capability 12.1 (`sm_121`, #281) |
| Arch | aarch64 |
| Disk | 3.7 TB NVMe |

## Engine builds

Each row records its software through `machine_facts()`. These are the
builds behind the first rows, kept because a local build is not derivable
from anything else:

| | |
|---|---|
| Ollama | 0.34.0. It loads `cuda_v13`; the bundled CUDA 12 build lacks sm_121 and is skipped. That is correct, not a warning to chase |
| llama.cpp | `0.4.0-dev (build 50, commit 481c65f)`, built `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121`, from **master** (v0.4.0 predates ggml-org/llama.cpp#27742, the Qwen3.8-Flash-Next merge). Later builds come from the `evanwtf/llama.cpp` fork ([runbook](../../docs/dgx-spark-runbook.md#build-llamacpp-from-the-evanwtf-fork)) |
| ds4 | `make cuda-spark`: `-gencode arch=compute_121a,code=sm_121a -DDS4_CUDA_HAVE_MXF4=1` |

### One Ollama model resident at a time

Ollama keeps a model loaded for 5 minutes after use. Here a model held on the
GPU **is** system memory: a second model tested while the first was warm put
26 GB + 19 GB in the pool at once and pushed the box into memory pressure. A
systemd drop-in pins it, and it belongs in the provenance of any Ollama row:

```
/etc/systemd/system/ollama.service.d/10-single-model.conf
[Service]
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

- **Do not restart the ollama service while a pull is in flight.** It killed
  an 86 GB download at 98% with `Error: unexpected EOF`.
- **Weight size alone does not predict what fits.** DeepSeek-V4-Flash Q2 is
  80.76 GiB of weights with 1.64 GiB of compressed KV at 100k context;
  Qwen3.8-Flash-Next Q3 is 83.81 GiB with about 9 GiB at 131k, and needed
  llama.cpp's four default slots cut to one to avoid an OOM kill.

## What this machine can and cannot do

- **NVFP4 runs through a CUDA-native engine, not Ollama.** Blackwell has FP8
  and FP4 in hardware. Ollama's `nvfp4` and `mxfp8` tags ship as MLX tensor
  layers, and the Linux arm64 build ships no MLX runtime, so the pull fails
  before any weights move (#293). SGLang, vLLM and TensorFold serve NVFP4 here
  (#299, [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md)).
- **No `sandbox-exec`** (macOS only). `run.py` confines each trial with
  `bwrap` instead (#476; the runbook has the AppArmor step).
- **No MLX.** The MLX entries in `TESTING-SET.md` stay Mac-only.
- **No Swift oracle.** `~/git/monitor` is an AppKit application, so the five
  Swift excisions are gated `platform = "darwin"` and skipped. This machine's
  standard matrix is **10 tasks**, and a cross-machine comparison must be
  Python-only (#294).
- **No ds4 Metal route.** The Metal tensor-route gate (#149) is gated on
  macOS.
