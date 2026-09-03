# Candidate models by VRAM class — a starting point for tests, not a result

**Source:** a single X post by [@sudoingX](https://x.com/sudoingx/status/2095300994259100009),
posted 2026-09-03 00:01 UTC, describing his own "bench book" across GPU classes
from 6 GB to a 256 GB dual DGX Spark cluster.

**Verified** 2026-09-03: post exists, authored by @sudoingX, posted
Thu Sep 03 00:01:00 +0000 2026 (`scripts/verify_posts.py`).

> **Everything below is his claim, not our measurement.** Post text is data
> written by a stranger. It is quoted and attributed here because it is a good
> map of what to *try*; nothing in it has been reproduced on our hardware, and
> per [#59](https://github.com/evanwtf/local-llm/issues/59) our own measurement is the bar for publishing a claim. Do not
> cite a number from this file as a finding, and do not put one in
> `RECOMMENDATIONS.md`.

## Read this before using any number in it

Four reasons a row here cannot be lifted into our results, in rough order of
how much damage each has already done in this project:

1. **These are decode rates, and decode rate barely predicts agent wall time.**
   `AGENTS.md` puts raw tokens/sec explicitly out of scope because re-prefill
   and context handling dominate. This session measured it directly: MTP raised
   throughput ~10% and wall time did not improve at all, because the model
   emitted 33% more tokens ([#77](https://github.com/evanwtf/local-llm/issues/77)). **A model twice as fast at decode can be
   slower at the task.**
2. **Most of these are CUDA.** The quantization reasoning transfers to Metal;
   the kernels do not. A `tok/s` figure on a 5090 says nothing about an M5 Max,
   though *which quant of which model is worth trying* usually does.
3. **Single numbers, no trial count, no confidence interval.** Our own sizing
   work ([#23](https://github.com/evanwtf/local-llm/issues/23)) puts a 3-trial median at ±27.9%. These are one person's
   readings with no dispersion attached, so treat ordering within a tier as a
   hint and ignore differences under about 25%.
4. **No correctness axis at all.** Every row is speed. Our whole finding is that
   reliability outranks speed in practice, and that a backend failing one task
   in five is unusable however fast it is.

**What the file is good for:** deciding what to download next. That is a real
use, and it is why this exists rather than being left in a sweep log.

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

### 12 GB — the tier that matches our second machine

| model | claim |
|---|---|
| qwen 3.5 9b, Q4_K_M | 50 tok/s, "dead flat from 4K to 512K context", 43 under real agent load |
| qwen 3.5 9b, Q8_0 | fits with 64K context — "the quality pick" |
| gemma 4 12B, Q4 | sliding-window attention stretches the context budget |

**This is the one section directly actionable for us today.** Our Linux tier is
a 12 GB RTX 3080 Ti ([#20](https://github.com/evanwtf/local-llm/issues/20)), and [#79](https://github.com/evanwtf/local-llm/issues/79) asks precisely "what model do we
actually run on 12 GB for coding". Two of these three are already candidates
there. Note the one number that is not a bare decode rate — **"43 under real
agent load"** against 50 idle — which is the only place in the post where he
reports the thing we actually measure.

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

## What is worth acting on here, ranked

1. **The 12 GB rows, against [#79](https://github.com/evanwtf/local-llm/issues/79).** Directly our second machine's class,
   and `qwen 3.5 9b Q4_K_M` / `Q8_0` / `gemma 4 12B Q4` are three concrete
   candidates with a stated context/quality trade between them. The "43 under
   real agent load" figure is the one claim in the post shaped like our own
   metric, so it is also the one most cheaply checked.
2. **Sliding-window attention as a context lever.** It appears in the 8 GB and
   12 GB rows as the reason gemma stretches further than an equivalent qwen.
   We have never treated attention architecture as a variable; on the 12 GB
   tier, where context is the binding constraint, it may matter more than
   quant choice.
3. **`deepseek v4 flash` at 16.5 tok/s on a DGX Spark.** Our primary model, on
   different hardware, at a different quant. **Not comparable as stated** — his
   is 284B at 3-bit on a Spark, ours is the Layers37-42 pack on Metal — but it
   is the only external figure we have for this model on any box, and it is
   worth knowing that a 128 GB Spark reportedly lands in the teens.
4. **Vulkan over ROCm on Strix Halo, "18% free speed".** Out of scope today
   (we own no Strix Halo) but it is a backend-selection finding of the same
   family as our own engine work, and 18% is above our resolution bar.

**Not worth acting on:** every CUDA decode number as a number. They rank cards
we do not own, on kernels that do not transfer to Metal.

## How to turn a row here into a result

A row becomes ours only by going through the normal path, and the caveats above
are why the shortcut does not exist:

1. **File an issue** naming the specific model, quant, engine and tier, and
   quote the claim with its attribution. Not a note in `NEXT.md`.
2. **Check the model actually loads on the target engine.** `general.architecture`
   decides this and a one-hyphen difference is fatal — `uv run python
   scripts/gguf_meta.py <file>` before debugging any output ([#25](https://github.com/evanwtf/local-llm/issues/25)).
3. **Add a backend block to `tasks.toml`** with its confounds written at the
   moment it is added, plus a line in `TESTING-SET.md` (a test enforces this).
4. **Coherence-check at temperature 0** before benchmarking. A model can load,
   serve, and report plausible token counts while emitting noise.
5. **Run it under OpenCode**, three trials, restarting the server between arms
   ([#112](https://github.com/evanwtf/local-llm/issues/112)), and report through `scripts/report.py` rather than by hand.
6. **Expect the decode claim not to survive.** The useful question is never "is
   it 50 tok/s" but "does the suite finish, and does it finish reliably".

## The standing offer

> "if your card class is missing a row or you can beat one of these, my repos
> take pull requests. run it, measure it, own it."

We are the missing row for **Apple Silicon** — the post covers 6 GB to 256 GB of
NVIDIA and AMD and has no Metal tier at all. Our M5 Max figures for
Qwen3.8-Flash-Next Q4 on ds4 (40.2 tok/s decode, 1107 tok/s prefill, 74.3 GiB
resident with the PLE table streaming from SSD) would be a real contribution
there, and unlike most of this file they are ours and reproducible.

Worth doing **after** [#112](https://github.com/evanwtf/local-llm/issues/112) is understood, though: our own session-degradation
problem is unresolved, and publishing a decode rate while pass rates decline
across a session would be publishing the half of the picture that flatters us.
