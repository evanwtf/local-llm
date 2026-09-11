# Agent benchmark — Cortex-X925-128GB-GB10 (NVIDIA DGX Spark)

What has been measured on this machine, and what each number is allowed to
support. Rows are in [`results.jsonl`](results.jsonl); the machine is described
in [`README.md`](README.md).

**Nothing here may be pooled with the M5 Max's rows.** Different platform, and
in every case a different engine build, quantization or container as well.
Mac figures below are quoted as reference points, never as the other half of a
comparison.

---

## 1. The quotable rows

OpenCode, post-fix, not excluded — the only rows TESTING-SET.md permits as
figures. All on the **10-task** matrix: the five Swift excisions are
`platform = "darwin"` and skipped here, which is a missing cell and never a
zero (#294).

| backend | model | engine | passed | median | trials |
|---|---|---|---|---:|---:|
| `qwen38fnq3dgx` | Qwen3.8-Flash-Next `UD-Q3_K_XL` | llama.cpp CUDA sm_121 | **30/30** | **108.2s** | 3 |
| `qwen36codinggguf` | Qwen3.6-27B-coding GGUF | Ollama 0.34.0 | **29/30** | 131.0s | 3 |
| `ds4dgx` | DeepSeek-V4-Flash **Q2** 0731 | ds4 CUDA sm_121a | **20/20** | 213.2s | 2 |

`ds4dgx` has **two** trials, not three. The run was stopped deliberately:
ds4-server was holding 112 GB with 9 GB free, and an OOM mid-trial would have
cost the batch and produced rows needing exclusion. Two trials support a pass
rate; **they do not support a timing claim** — AGENTS.md wants three before
believing a gap.

### Against the M5 Max, on the same ten tasks

Recomputed from the committed Mac ledger with `valid_opencode()`, **not** taken
from RECOMMENDATIONS.md. That document's medians (106s for `qwen38fnq3`, 115s
for `ds4`) are over the full 15-task suite, and the Swift tasks have a 122.1s
median of their own — so the published numbers flatter a non-Mac machine by
about 21% if compared directly.

| backend | DGX | M5 Max | note |
|---|---:|---:|---|
| Qwen3.8-Flash-Next UD-Q3_K_XL | 108.2s | **83.7s** | same weights, quant, sampler, context |
| DeepSeek-V4-Flash | 213.2s | **45.8s** | **different quantization** — Q2 here, mixed q2/q4 there |
| Qwen3.6-27B-coding | 131.0s | **94.7s** | **different quantization** — GGUF here, mxfp8 there (#293) |

**Only the first row is close to a like-for-like comparison, and even it is
not one.** The engine build and client differ:

| | llama.cpp | opencode |
|---|---|---|
| M5 Max | `b10832-2092353c8` | 1.18.29 |
| DGX | `481c65f` | 1.18.30 |

Held constant there: model, quantization (83.81 GiB here against 83.8 GiB —
the same artifact), sampler (`temp 1.0, top_p 0.95, top_k 20, min_p 0.0`,
pinned explicitly because llama.cpp defaults `min_p` to 0.05), context 131072,
and the ten tasks. So it is a **stack comparison with three known
differences**, not a platform comparison.

The per-task spread is bidirectional, so the 1.29× summary is not a verdict:

| task | DGX | Mac | ratio |
|---|---:|---:|---:|
| script-reverse | 18.4s | 41.3s | **0.45×** |
| mbox-scan | 101.6s | 105.6s | 0.96× |
| storage-put-and-sweep | 119.0s | 119.5s | 1.00× |
| parser-date | 210.6s | 188.1s | 1.12× |
| storage-blob-put | 129.0s | 94.7s | 1.36× |
| parser-mbox-quoting-nodoc | 135.2s | 86.6s | 1.56× |
| mbox-quoting-both-halves | 160.6s | 94.9s | 1.69× |

The Spark is **faster on the script tasks** and slower on the multi-file
excisions. Given #14 — prompt re-prefill dominates agent wall time — the
hypothesis is that the machines differ more in prefill than decode. Untested.

---

## 2. Session state: llama.cpp flat, ds4 not

No server restart between trials, against AGENTS.md's preference while #112 is
open. That makes these medians evidence about #112 rather than a defect in the
measurement.

| backend | trial 1 | trial 2 | trial 3 |
|---|---:|---:|---:|
| `qwen38fnq3dgx` (llama.cpp) | 114.8s | 108.2s | 110.3s |
| `qwen36codinggguf` (Ollama) | 141.6s | 127.5s | 153.0s |
| `ds4dgx` (ds4) | 213.2s | 238.3s | — |

**llama.cpp is flat across a 60-minute session.** ds4 is 12% slower in its
second trial, and its reported decode rate drifted from 17–19 t/s early to
14.9–16.1 t/s later. That is suggestive and no more: two trials, and decode
rate falls with context length anyway, so this is not yet separable from the
tasks simply having longer prompts.

Thermals are not the explanation. The GPU held **59°C at 2541/3003 MHz** with
44 W draw throughout, so #116's hypothesis is not what is operating here.

---

## 3. #112 reproduces, on new hardware and a new engine

`qwen36codinggguf`'s single failure is not wrong code. `mbox-strip-envelope`
trial 1: `solution_empty=true`, `agent_error=none`, 4 turns, 55.2s. The model
described the work and never called a tool —

> The function needs to strip the first line (the `From_` envelope) from the
> raw bytes. If there's no newline, it returns empty bytes.

— then `reason: stop`. That is #112's turn-1 death, now seen on **CUDA via
Ollama with Qwen3.6**, where the issue found it on Metal via ds4 with Qwen3.8.
Intermittent, not task-bound: the same task passed in trials 2 and 3.

**It is only visible because of #294.** `solution_empty` could never be true on
this machine before the `UV_FROZEN` fix, because the oracle dirtied `uv.lock`
in every worktree and so every diff was non-empty. #112's own detection would
have been blind here.

---

## 4. Engine validation

**DeepSeek decode reproduces upstream's published GB10 figure.** `ds4-server`,
C hash table prompt, temperature 0, 256 tokens, three runs:

```
chat gen=256 THINKING decoding chunk=19.82 t/s avg=19.62 t/s 13.047s
```

**19.62 t/s against upstream's 19.72 t/s — 0.5%.** So the build is not merely
compiling and answering; it performs as the people who wrote it say it should
on this hardware. That is the check #297 required before trusting any agent
row taken on it.

`finish=length` with THINKING on all three: the whole 256-token budget went to
reasoning. Those runs measure decode rate and nothing about output quality.

### Memory shapes what is runnable here

| model | weights | KV | note |
|---|---:|---:|---|
| DeepSeek-V4-Flash Q2 | 80.76 GiB | **1.64 GiB** @ 100k | compressed KV |
| Qwen3.8-Flash-Next Q3 | 83.81 GiB | ~9 GiB @ 131k | f16, 1 slot |

A 3 GiB difference in weights, a 5× difference in KV. Qwen3.8 had to have its
slot count cut from llama.cpp's default 4 to avoid an OOM kill; DeepSeek sits
comfortably. **Weight size alone does not predict whether a model fits on this
machine.**

---

## 5. Functional checks, not measurements

Single short generations, several taken alongside a download or a build.

| model | engine | decode |
|---|---|---:|
| `nemotron3:33b-q4_K_M` | Ollama | 72.0 tok/s |
| `qwen3.6:27b-coding` | Ollama | 29.8 tok/s |
| Qwen3.8-Flash-Next Q3 | llama.cpp | 26.16 tok/s (prefill 93.04) |
| DeepSeek-V4-Flash Q2 | ds4 | 19.62 tok/s |

Nemotron is the fastest decoder measured here and the only one that did not
blow its token budget on reasoning. It has **no agent rows**; that number is a
functional check and must not be quoted as a speed result.

---

## 6. Not measured

- **NVFP4**, the quantization this GPU exists to run: unreachable via Ollama on
  Linux. The MLX runtime its tags need is not shipped in the Linux arm64
  build (#293). A CUDA-native engine is the route (#299).
- **Nemotron 3 Super 120B-A12B** (#296): pull died at 98%, resumable.
- **GLM-5.3-Flash** (#298): needs the official HF CLI for its split GGUF, now
  installed. Backend committed with upstream's Q2 / `--ctx 16384` constraints.
- **A third ds4 trial**, and any prefill-vs-decode breakdown.
