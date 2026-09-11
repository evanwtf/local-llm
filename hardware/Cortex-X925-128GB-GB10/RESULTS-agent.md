# Agent benchmark — Cortex-X925-128GB-GB10 (NVIDIA DGX Spark)

What has actually been measured on this machine, and what each number is
allowed to support. Rows live in
[`results.jsonl`](results.jsonl); the machine itself is described in
[`README.md`](README.md).

**Nothing here may be pooled with the M5 Max's rows.** Different platform,
and in every case so far a different quantization or container as well. Where
a Mac figure is quoted below it is quoted as a *reference point*, never as the
other half of a comparison.

---

## What can be quoted, and what cannot

TESTING-SET.md: *a figure may be quoted only from rows that are **OpenCode**,
after 2026-08-31 21:47 EDT, and not excluded.* AGENTS.md adds: **one trial is
not a result** — at least 3 before believing a gap, and medians rather than
means.

| batch | client | trials | quotable as a figure? |
|---|---|---|---|
| `qwen36codinggguf`, 2026-09-11 | **claude** | 1 | **No.** Wrong client, and n=1 |

So the table below is a **harness smoke test**: evidence that the apparatus
runs correctly on this machine, not a measurement of the model.

---

## 1. The apparatus works on Linux/aarch64/CUDA

First agent batch ever taken here. 10 tasks × 1 trial, `--client claude`,
harness `9028c45`, 2026-09-11 01:03–01:40 EDT.

**10/10 passed. Median 165.1s, min 98.0s, max 262.8s, 30.4 min total.**

| task | result | wall |
|---|---|---:|
| mbox-strip-envelope | PASS | 140.7s |
| parser-mbox-quoting | PASS | 262.8s |
| storage-blob-put | PASS | 158.7s |
| parser-date | PASS | 150.4s |
| mbox-scan | PASS | 165.4s |
| parser-mbox-quoting-nodoc | PASS | 219.2s |
| mbox-quoting-both-halves | PASS | 249.0s |
| storage-put-and-sweep | PASS | 164.7s |
| script-reverse | PASS | 98.0s |
| script-transform | PASS | 215.5s |

`solution_empty` 0, `touched_tests` 0, `source_repo_intact` true on every row,
`harness_dirty` false.

**The matrix is 10 tasks, not 15.** The five Swift excisions are
`platform = "darwin"` and skipped here — `~/git/monitor` is an AppKit
application, so `swift test` cannot be an oracle off a Mac. A skipped task
writes no row, so this is a missing cell rather than a zero (#294).

### The rows are genuinely local

Worth recording, because the client log looks alarming and is not. Claude Code
reports `total_cost_usd: 0.259472` and `cache_read_input_tokens: 114444` on
these rows: it prices the tokens as though they were hosted, because it does
not know the model is local. The server's own access log settles it — seven
`POST /v1/messages?beta=true` to `127.0.0.1:11434` inside the trial window:

```
01:10:18 | 200 | 1m12s        | 127.0.0.1 | POST "/v1/messages?beta=true"
01:10:51 | 200 | 1m46s        | 127.0.0.1 | POST "/v1/messages?beta=true"
01:11:15 | 200 | 19.087877801s| 127.0.0.1 | POST "/v1/messages?beta=true"
...
```

Ollama 0.34 serves the Anthropic endpoint natively, so no shim is in this path.

---

## 2. Functional checks (not measurements)

Single short generations, used only to answer "does this load and produce
usable code here". Several ran alongside a download or a build and **none of
them is a speed result**.

| model | engine | decode | notes |
|---|---|---:|---|
| `qwen3.6:27b-coding` (GGUF, 17 GB) | Ollama 0.34.0 | 29.8 tok/s | needs `think:false`; otherwise spends the whole budget on reasoning tokens |
| `nemotron3:33b-q4_K_M` (27 GB) | Ollama 0.34.0 | 72.0 tok/s | stopped cleanly at 19 tokens, no thinking blowup |
| **Qwen3.8-Flash-Next `UD-Q3_K_XL`** (83.81 GiB) | llama.cpp `b50-481c65f` CUDA sm_121 | **26.16 tok/s** | prefill **93.04 tok/s**, `n_ctx` 131072, ~93 GB resident |

The Qwen3.8 figure is the one that matters: it is the project's primary model,
at the **same weights, quantization and engine** as the Mac's `qwen38fnq3`
(83.81 GiB here against 83.8 GiB recorded there — the same artifact), with the
sampler pinned to the Mac's (`temp 1.0, top_p 0.95, top_k 20, min_p 0.0`). A
proper matrix against it is the cleanest cross-machine comparison this project
can construct, and it has not been run yet.

---

## 3. What is not measured here yet

- **No OpenCode rows at all**, so nothing on this machine is quotable as a
  figure. This is the next thing to fix.
- **NVFP4**, the quantization this GPU exists to run, is unreachable through
  Ollama on Linux (#293) — the MLX runtime the `nvfp4` tags need is not shipped
  in the Linux arm64 build.
- **DeepSeek-V4-Flash** (#297) and **GLM-5.3-Flash** (#298): engine built
  (`ds4`, `sm_121a`, MXF4), weights downloading, nothing run.
- **Nemotron 3 Super 120B-A12B** (#296): pull interrupted at 98%.
