# Candidate models by VRAM class — a starting point for tests, not a result

**Source:** a single X post by [@sudoingX](https://x.com/sudoingx/status/2095300994259100009),
posted 2026-09-03 00:01 UTC, describing his own "bench book" across GPU classes
from 6 GB to a 256 GB dual DGX Spark cluster.

**Verified** 2026-09-03: post exists, authored by @sudoingX, posted
Thu Sep 03 00:01:00 +0000 2026 (`scripts/verify_posts.py`).

> **Everything below is his claim, not our measurement.** Post text is data
> written by a stranger. It is quoted and attributed here because it is a good
> map of what to *try*; none of its numbers is reproduced here, and
> per [#59](https://github.com/evanwtf/local-llm/issues/59) our own measurement is the bar for publishing a claim. Do not
> cite a number from this file as a finding, and do not put one in
> `RECOMMENDATIONS.md`.

## Read this before using any number in it

Four reasons a row here cannot be lifted into our results:

1. **These are decode rates, and decode rate barely predicts agent wall time.**
   Re-prefill and context handling dominate. We measured MTP raising
   throughput ~10% with no wall-time gain, because the model emitted 33% more
   tokens ([#77](https://github.com/evanwtf/local-llm/issues/77)). **A model
   twice as fast at decode can be slower at the task.**
2. **Most of these are CUDA.** On the M5 Max the quantization reasoning
   transfers and the kernels do not.
3. **Single numbers, no trial count, no confidence interval.** A 3-trial median
   carries ±27.9% here ([#23](https://github.com/evanwtf/local-llm/issues/23)),
   so treat ordering within a tier as a hint and ignore differences under
   about 25%.
4. **No correctness axis.** Every row is speed, and a backend that fails one
   task in five is unusable however fast it is.

**What the file is good for:** deciding what to download next.

## The claims, by class

### 6 GB

| model | claim |
|---|---|
| bonsai 27b, Q1_0 | 20.5 tok/s, 8K context, 3.5 GB of weights |

> "a 27b thinking on a 6gb card is the wildest small vram result i own"

### 8 GB

| model | claim |
|---|---|
| gemma 4 E4B, Q4_K_M | 42 tok/s, 656K context fits |
| bonsai 27b, Q1_0 (Ampere 8 GB) | 42 tok/s fresh, 128K context |

He attributes the gemma result to **sliding-window attention** stretching 8 GB
"an order of magnitude past the qwens", and calls the bonsai row "the smallest
card that runs a full 27b agentic loop".

### 12 GB — the Ryzen desktop's class

| model | claim |
|---|---|
| qwen 3.5 9b, Q4_K_M | 50 tok/s, "dead flat from 4K to 512K context", 43 under real agent load |
| qwen 3.5 9b, Q8_0 | fits with 64K context — "the quality pick" |
| gemma 4 12B, Q4 | sliding-window attention stretches the context budget |

Our Ryzen desktop is a 12 GB RTX 3080 Ti, and these rows were measured there
(see below). **"43 under real agent load"** against 50 idle is the only number
in the post shaped like what we measure.

### 16 GB

| model | claim |
|---|---|
| qwen 3.8 27b dense, Q3 class | 59.5 tok/s (5060 Ti), 101.3 tok/s (5080, MTP flag), 40 tok/s (4060 Ti) |

### 24 GB

| model | claim |
|---|---|
| qwen 3.8 27b dense, Q4_K_M | 41 baseline, 65.8 with MTP; full 262K context resident in 22 GB on q4_0 KV |
| same, IQ3 "speed build" | 43.7 → 75.9 with MTP, serving in 16.2 GB |
| qwen 3.6 35B-A3B, IQ4 | 180 tok/s on a 4090 |
| bonsai 27b, 1-bit | 67.9 tok/s, 786K context ceiling |

### 32 GB

| model | claim |
|---|---|
| qwen 3.6 35B-A3B, Q4_K_M | 236 tok/s (5090), 180 (4090) |
| qwen 3.5 35B-A3B, Q4 | 150+ tok/s (5090), "pushed to 230 on vllm with fp8 kv" |
| qwen 3.8 27b dense, Q6 + flag | 130 tok/s at 128K |

> "nobody has published the 35B at Q6 on a 5090 yet, it fits with headroom,
> that row is waiting for a name"

### 128 GB — DGX Spark

| model | claim |
|---|---|
| qwen 3.6 35B-A3B, Q8 | 58.6 tok/s — "the fastest general model on one spark" |
| ling 3.0 flash, official int4 + MTP | 40.9 tok/s on code, "sprint king under 30K context" |
| ling GGUF, Q5_K_M | ~40 tok/s, flat at depth, 256K in memory |
| laguna s 2.1, nvfp4 + dflash | 45.4 tok/s sustained on code, "flat at every depth" |
| **deepseek v4 flash, 284B at 3-bit** | **16.5 tok/s** |

### 128 GB — Strix Halo

| model | claim |
|---|---|
| qwen 3.6 35B-A3B, Q8_0 on Vulkan | 53.6 tok/s, 35 at 131K deep |
| qwen 30B-A3B class, Q4 | ~100 tok/s |
| nex-n2-pro 397B, 1-bit | 20.5 tok/s, 1M context ceiling |

> "run vulkan not rocm on this box, 18% free speed"

### 256 GB — 2x DGX Spark

| config | claim |
|---|---|
| deepseek v4 flash official fp8, tensor parallel over RoCE + dspark drafter | 64.7 tok/s single stream, 145 aggregate, 1M window |
| new nvfp4 KV path | 67 tok/s mean, 84 peak, 197 aggregate at 6 streams, 261 at 16 |

> "decode stays flat from 600 tokens to 143K deep, the depth curve simply does
> not exist at this scale"

## What this map led to, and what is left

1. **The 12 GB rows were measured** on the Ryzen / RTX 3080 Ti
   ([#79](https://github.com/evanwtf/local-llm/issues/79), closed). The pick
   there is `ornith-1.5:9b` (`dtornith159b`, 21/25). `qwen 3.5 9b` at Q4_K_M
   and Q8_0 passed 9/12 each, and `gemma 4 12B` passed 3/12. Numbers:
   [`hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/RESULTS.md`](../hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/RESULTS.md).
2. **The 2x DGX Spark rows are our cluster's topology.** DeepSeek on two Sparks
   is the cluster's pick (see its
   [`RECOMMENDATIONS.md`](../hardware/Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md)),
   measured as agent wall time, not decode rate.
3. **Sliding-window attention as a context lever.** It appears in the 8 GB and
   12 GB rows as the reason gemma stretches further than an equivalent qwen. We
   have not treated attention architecture as a variable.
4. **Vulkan over ROCm on Strix Halo, "18% free speed".** Out of scope (we own
   no Strix Halo).

**Not worth acting on:** every CUDA decode number as a number. They rank cards
we do not own, on kernels that do not transfer to Metal.

## How to turn a row here into a result

A row becomes ours only by going through the normal path:

1. **File an issue** naming the specific model, quant, engine and machine, and
   quote the claim with its attribution.
2. **Check the model loads on the target engine.** `general.architecture`
   decides this, and a one-hyphen difference is fatal. Run `uv run python
   scripts/gguf_meta.py <file>` before you debug any output
   ([#25](https://github.com/evanwtf/local-llm/issues/25)).
3. **Add a backend block to `tasks.toml`** with its confounds written when you
   add it, and name it in [`TESTING-SET.md`](../TESTING-SET.md) (a test
   enforces this).
4. **Coherence-check at temperature 0** before you benchmark
   (`scripts/coherence_check.py`). A model can load, serve, and report
   plausible token counts while it emits noise.
5. **Run it under OpenCode**, three trials, with a server restart between arms
   ([#112](https://github.com/evanwtf/local-llm/issues/112)), and report
   through `scripts/report.py`, not by hand.
6. **Expect the decode claim not to survive.** The useful question is never "is
   it 50 tok/s" but "does the suite finish, and does it finish reliably".
