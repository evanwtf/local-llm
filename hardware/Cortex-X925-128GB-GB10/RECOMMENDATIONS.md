# What to actually run on a DGX Spark

**A local coding agent *served from* an NVIDIA DGX Spark (GB10, 128 GB unified,
Linux/aarch64, sm_121), driven from a laptop over your LAN.** Every number below
was measured by `benchmarks/agent/` on this machine with **OpenCode** — the only
client this file uses, so there is no client column. Nothing here is from a model
card. Paste section 1, pick a row in section 2, or read what does not help in 3.

**Ledger last read 2026-09-14.** A read date more than a couple of weeks old means
re-check `results.jsonl` before trusting these rows.

> **The one thing nobody has measured: the LAN round trip.** Every median here
> was taken on the box (loopback). This file's deployment shape is a laptop
> driving the Spark over WiFi, and a fixed per-turn network cost hurts the fast
> rows most — their turns are ~3–6 s each. Treat every median as a *lower bound*
> on what a laptop actually sees, and see [#342] before quoting one as latency.

---

## 1. Paste this

Serve on the Spark; point the laptop at it. This is the fastest **agent** row
(section 2): **Qwen3.6-35B-A3B NVFP4** — the small-active-param MoE the NVIDIA
CLI-agent playbook defaults to, and the fastest agent backend measured here.

**On the Spark** — vLLM from the venv, thinking off, bound to all interfaces.
`nvcc` **and** `ninja` must be on `PATH` at launch, or it loads fully and then
dies at the FlashInfer JIT (see [`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md)):

```sh
# vLLM 0.29.0 (CUDA sm_121, MARLIN NVFP4 MoE) serving Qwen3.6-35B-A3B-NVFP4 (~24 GiB)
PATH="$VLLM_VENV/bin:/usr/local/cuda/bin:$PATH" CUDACXX=/usr/local/cuda/bin/nvcc \
vllm serve nvidia/Qwen3.6-35B-A3B-NVFP4 \
  --served-model-name qwen3.6-35b-a3b-nvfp4 --host 0.0.0.0 --port 8030 \
  --gpu-memory-utilization 0.6 --max-model-len 262144 --max-num-seqs 8 \
  --kv-cache-dtype fp8 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking": false}'
```

**On the laptop** — OpenCode points at the Spark's LAN address (`--dir` is not
optional; see below):

```sh
curl -fsSL https://opencode.ai/install | bash
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "model": "spark/qwen3.6-35b-a3b-nvfp4",
  "provider": {
    "spark": {
      "name": "DGX Spark (LAN)",
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://SPARK-IP:8030/v1", "apiKey": "local" },
      "models": { "qwen3.6-35b-a3b-nvfp4": { "tool_call": true, "reasoning": false } }
    }
  }
}
JSON
cd ~/some/project
opencode run --dir "$PWD" "add a --verbose flag to the CLI and a test for it"
```

**56/57 on our benchmark (98%, n=60), 35.0 s median task, on the box** ([#335]) —
about **63% of the wall time** of the next option (llama.cpp Q3, 55.9 s). Replace
`SPARK-IP` with the Spark's LAN address; the WiFi round trip is on top and
unmeasured.

**Prefer a plain binary over vLLM?** The **llama.cpp Q3** row in section 2 is
~1.8× the wall but a single self-contained `llama-server` — no venv, no
`nvcc`/`ninja`, no MoE quirks. If standing up vLLM is friction, start there.

**`--dir` is not optional.** `opencode run` talks to a background server with its
own working directory, so it ignores where you launched it and writes files
elsewhere. That cost the sibling Mac project two weeks and 130 wasted trials.

---

## 2. Or pick a row

**Not a ranking.** Each row is here for the reason in its first column. Two axes
only — engine and model — plus how the laptop reaches it (all `:PORT/v1`).
"pass" is usable trials on the ledger; "median" is OpenCode wall seconds on the
box; "turns" is the median agent turn count.

| pick this if | model | engine | pass | median | turns | note |
|---|---|---|---|---|---|---|
| **you want the agent done fastest** (and the best server) | Qwen3.6-**35B-A3B** `NVFP4`, thinking off | vLLM 0.29.0, MARLIN | **56/57 (98%)** | **35.0 s** | 11 | fastest here (3.2 s/turn, a 3B-active MoE) *and* the best multi-client server — 321 tok/s at 8 concurrent seqs ([#335], [#347]) |
| **you want the simplest server to stand up** | Flash-Next `UD-Q3_K_XL`, thinking off | llama.cpp CUDA sm_121 | **151/151** | 55.9 s | 9 | one `llama-server` binary, no venv; 6.2 s/turn |
| **…and the KV cache halved** | same, `q8_0` KV | llama.cpp, port 8022 | **90/90** | 52.4 s | 9 | quality-neutral memory win ([#344]) |
| **you want a larger model / 262K context** | Flash-Next `NVFP4` (125B-A6B) | vLLM (styles01 fork), MTP-3 | **60/60** | 85.2 s | 13 | the big-model lane, 262K ctx, packed-PLE ([#331]). Slower single-agent wall *and* lower concurrency (160 tok/s @ 8 seqs) than the A3B row above ([#308], [#347]) — §3. |
| **you want a second, non-Qwen lineage** | DeepSeek-V4-Flash Q2 | ds4 CUDA sm_121a | **60/60** | 213.2 s | 9 | the only non-Qwen 100%-pass option ([#369] opens SSD streaming) |
| **you want it to show its work** | Flash-Next Q3, thinking on | llama.cpp CUDA sm_121 | 92/104 (88%) | 106.1 s | 9 | reasoning in the transcript, but ~2× the wall *and* below the 90% bar ([#333]) |

The first five rows clear [#23]'s bar for a **>90%** claim. **The thinking-on row
does not (88%)**, and neither does the one-command Ollama option below (84%) —
both are here for completeness, not as recommendations.

**The one-command option, and why it is not a row above.** `ollama pull
qwen3.6:27b-coding` (~20 GiB, smallest download, no flags) gets you an agent —
but it scored **59/70 (84%)** at a 139 s median on this set, under the bar we'd
stand behind. Reach for it to kick the tyres, not to do the work.

---

## 3. What does not help — and the trap in "fastest"

**What actually made the fastest lane fastest: cheaper turns, not fewer.** The
A3B row takes **more** turns than llama.cpp Q3 (11 vs 9) yet finishes in **half**
the wall time, because each turn costs **2.8 s vs 6.2 s**. A 35B model with only
~3B active parameters per token reads far fewer bytes per turn — for both decode
and re-prefill — so the per-turn cost, which [#317] identifies as the thing that
dominates agent wall time, collapses. Active-parameter count is the model lever
that paid off here (§4).

**Fastest tokens/sec is still not fastest agent.** The Flash-Next NVFP4/vLLM row
has the highest *raw* single-stream decode on the box (~43 tok/s, MTP
speculation) — yet its **median agent wall (85.2 s) is ~2.4× the A3B row**.
Two reasons, neither of them decode rate: it runs more turns (13 vs 11), and its
agent-observed cost per turn (6.6 s) is *higher* once re-prefill is counted — the
125B-A6B Flash-Next has twice A3B's active params. The lane also runs MTP
speculation, and [#354] found speculation can *itself* multiply agent turn count
here, so the extra turns are not cleanly attributable to one cause. If a benchmark
tweet quotes tok/s, it is not quoting what finishes your task.

**Prefill levers that measured null on this machine** ([#344]): the larger
physical batch (`-ub 2048`) moved nothing and OOM-stalled the box at this model
size; `q8_0` KV is quality-neutral and a real *memory* win (its own row above)
but not a speed one. llama.cpp on this build has **no FP8 KV type**, so any recipe
pairing "chunked prefill + FP8 KV" is describing a vLLM stack, not this one.

**Thinking-off quality caveat.** Turning thinking off is a large speed lever, but
it costs character-level exactness. Two probes that flip:

| probe | thinking off | thinking on |
|---|---|---|
| reverse "benchmark" | `kramhceb` | `kramhcneb` ✓ |
| reverse "mailbox" | `xioblam` | `xobliam` ✓ |

If your work is character-level string manipulation, take the thinking-on row
and accept the wall and the lower pass rate.

---

## 4. Why decode speed is not the thing to optimise

Agent wall time is dominated by re-prefill and turn count, not raw decode
([#317]: decode is pinned near the ~231 GB/s unified-memory bandwidth ceiling
regardless of engine). The lever that pays is **how many bytes the model touches
per turn** — and the cleanest way to cut that is **fewer active parameters**, not
a faster engine. The A3B row is the proof: same NVFP4 quantization and the same
vLLM as the 27B and Flash-Next lanes, but ~3B active params instead of 6B+ gives
it 3.2 s/turn and the fastest agent wall on the box, despite taking *more* turns.
Speculation (MTP) helps single-stream decode and multi-client throughput but does
not shorten the agent. Optimise for **cheaper turns first** (small-active-param
model, thinking off), then fewer turns.

---

## 5. Where the rest of it went

| for | see |
|---|---|
| operating the box: ports, Prometheus, the vLLM venv + `nvcc`/`ninja` launch | [`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md) |
| the A3B NVFP4 recipe and full run | [#335], `benchmarks/agent/tasks.toml` → `qwen36a3bnvfp4dgx` |
| the served-endpoint deployment and the unmeasured WiFi cost | [#342], [#308] |
| the Flash-Next NVFP4 / vLLM recipe (packed PLE, MTP) and concurrency curve | [#331], [#308] |
| thinking-off as a measured variable | [#333] |
| the KV / prefill levers | [#344] |
| how any single number was measured | the issue it cites, and `results.jsonl` |

[#23]: https://github.com/evanwtf/local-llm/issues/23
[#308]: https://github.com/evanwtf/local-llm/issues/308
[#317]: https://github.com/evanwtf/local-llm/issues/317
[#331]: https://github.com/evanwtf/local-llm/issues/331
[#333]: https://github.com/evanwtf/local-llm/issues/333
[#335]: https://github.com/evanwtf/local-llm/issues/335
[#342]: https://github.com/evanwtf/local-llm/issues/342
[#344]: https://github.com/evanwtf/local-llm/issues/344
[#354]: https://github.com/evanwtf/local-llm/issues/354
[#369]: https://github.com/evanwtf/local-llm/issues/369
