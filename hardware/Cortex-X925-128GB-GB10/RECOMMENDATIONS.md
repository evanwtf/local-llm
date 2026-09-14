# What to actually run on a DGX Spark

**A local coding agent *served from* an NVIDIA DGX Spark (GB10, 128 GB unified,
Linux/aarch64, sm_121), driven from a laptop over your LAN.** Every number below
was measured by `benchmarks/agent/` on this machine with **OpenCode** — the only
client this file uses, so there is no client column. Nothing here is from a model
card. Paste section 1, pick a row in section 2, or read what does not help in 3.

**Ledger last read 2026-09-14** (878 rows). A read date more than a couple of
weeks old means re-check `results.jsonl` before trusting these rows.

> **The one thing nobody has measured: the LAN round trip.** Every median here
> was taken on the box (loopback). This file's deployment shape is a laptop
> driving the Spark over WiFi, and a fixed per-turn network cost hurts the fast
> rows most — their turns are ~6 s each. Treat every median as a *lower bound*
> on what a laptop actually sees, and see [#342] before quoting one as latency.

---

## 1. Paste this

Serve on the Spark; point the laptop at it. This is the fastest **agent** row
(section 2), thinking off.

**On the Spark** — bind all interfaces (`0.0.0.0`), not loopback, or the laptop
cannot reach it:

```sh
# llama.cpp (CUDA sm_121) serving Qwen3.8-Flash-Next UD-Q3_K_XL (~84 GiB)
llama-server -m Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf \
  --host 0.0.0.0 --port 8020 -ngl 999 -c 131072 -b 2048 --jinja \
  --chat-template-kwargs '{"enable_thinking":false}'
```

**On the laptop** — OpenCode points at the Spark's LAN address (`--dir` is not
optional; see below):

```sh
curl -fsSL https://opencode.ai/install | bash
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "model": "spark/qwen3.8-flash-next-q3",
  "provider": {
    "spark": {
      "name": "DGX Spark (LAN)",
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://SPARK-IP:8020/v1", "apiKey": "local" },
      "models": { "qwen3.8-flash-next-q3": { "tool_call": true, "reasoning": false } }
    }
  }
}
JSON
cd ~/some/project
opencode run --dir "$PWD" "add a --verbose flag to the CLI and a test for it"
```

**151/151 on our benchmark, 55.9 s median task, on the box.** Replace `SPARK-IP`
with the Spark's LAN address; the WiFi round trip is on top and unmeasured.

**`--dir` is not optional.** `opencode run` talks to a background server with its
own working directory, so it ignores where you launched it and writes files
elsewhere. That cost the sibling Mac project two weeks and 130 wasted trials.

---

## 2. Or pick a row

**Not a ranking.** Each row is here for the reason in its first column. Two axes
only — engine and model — plus how the laptop reaches it (all `:PORT/v1`).
"pass" is over every trial on the ledger; "median" is OpenCode wall seconds on
the box; "turns" is the median agent turn count.

| pick this if | model | engine | pass | median | turns | note |
|---|---|---|---|---|---|---|
| **you want the agent done fastest** | Flash-Next `UD-Q3_K_XL`, thinking off | llama.cpp CUDA sm_121 | **151/151** | **55.9 s** | 9 | the default; 6.2 s/turn |
| **you also want the KV cache halved** | same, `q8_0` KV | llama.cpp, port 8022 | **90/90** | **52.4 s** | 9 | quality-neutral memory win, marginally faster ([#344]) |
| **you want max throughput / to serve several machines** | Flash-Next `NVFP4` | vLLM (styles01 fork), MTP-3 | **60/60** | 85.2 s | 13 | ~43 tok/s single-stream, 262K ctx, 8 concurrent seqs; the field's daily-driver lane ([#331]). *Slower agent wall — see §3.* |
| **you want a second, non-Qwen lineage** | DeepSeek-V4-Flash Q2 | ds4 CUDA sm_121a | **60/60** | 213.2 s | 9 | the only non-Qwen 100%-pass option ([#369] opens SSD streaming) |
| **you want it to show its work** | same Q3, thinking on | llama.cpp CUDA sm_121 | 92/104 (88%) | 106.1 s | 9 | reasoning in the transcript, but ~2× the wall *and* below the 90% bar ([#333]) |

The first four rows clear [#23]'s bar for a **>90%** claim. **The thinking-on
row does not (88%)**, and neither does the one-command Ollama option below (84%)
— both are here for completeness, not as recommendations.

**The one-command option, and why it is not a row above.** `ollama pull
qwen3.6:27b-coding` (~20 GiB, smallest download, no flags) gets you an agent —
but it scored **59/70 (84%)** at a 139 s median on this set, under the bar we'd
stand behind. Reach for it to kick the tyres, not to do the work.

---

## 3. What does not help — and the trap in "fastest"

**Fastest tokens/sec is not fastest agent.** The NVFP4/vLLM row has the highest
raw decode on the box (~43 tok/s, MTP speculation) — yet its **median agent wall
(85.2 s) is over 1.5× the llama.cpp Q3 row (55.9 s)**. Two reasons, and neither
is decode speed: on this task set the NVFP4 quant takes **more turns**
(13 vs 9), and its agent-observed cost per turn (6.6 s) is *higher*, not lower,
than the llama.cpp lane's (6.2 s) once re-prefill is counted. The two lanes
differ in more than the engine — the NVFP4 lane also runs MTP speculation, and
[#354] found that speculation *by itself* can multiply agent turn count on this
machine — so the extra turns are not cleanly attributable to the quant alone.
What is certain, and enough for the recommendation: the higher-throughput lane
finishes this task set in more wall time. If a benchmark tweet quotes tok/s, it
is not quoting what finishes your task. That is why row 1 is the Q3 lane, not the
higher-throughput NVFP4 lane.

**Prefill levers that measured null on this machine** ([#344]): the larger
physical batch (`-ub 2048`) moved nothing and OOM-stalled the box at this model
size; `q8_0` KV is quality-neutral and a real *memory* win (its own row above)
but not a speed one. llama.cpp on this build has **no FP8 KV type**, so any recipe
pairing "chunked prefill + FP8 KV" is describing a vLLM stack, not this one.

**Thinking-off quality caveat.** Turning thinking off is the biggest speed lever
(row 1 vs the thinking-on row), but it costs character-level exactness. Two
probes that flip:

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
regardless of engine). The one decode-side lever that pays is the model choice —
NVFP4 reads fewer bytes per token than BF16 — and even that is swamped by how
many turns the agent takes (§3). Speculation (MTP) helps single-stream decode
and concurrency, but does not shorten the agent. Optimise for **fewer, cheaper
turns**: thinking off, a model that solves the task in one pass, an engine that
re-prefills fast.

---

## 5. Where the rest of it went

| for | see |
|---|---|
| operating the box: ports, Prometheus, launching safely | [`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md) |
| the served-endpoint deployment and the unmeasured WiFi cost | [#342], [#308] |
| the NVFP4 / vLLM recipe (packed PLE, MTP, memory cap) | [#331], `benchmarks/agent/tasks.toml` → `qwen38fnnvfp4dgx` |
| thinking-off as a measured variable | [#333] |
| the KV / prefill levers | [#344] |
| how any single number was measured | the issue it cites, and `results.jsonl` |

[#23]: https://github.com/evanwtf/local-llm/issues/23
[#308]: https://github.com/evanwtf/local-llm/issues/308
[#317]: https://github.com/evanwtf/local-llm/issues/317
[#331]: https://github.com/evanwtf/local-llm/issues/331
[#333]: https://github.com/evanwtf/local-llm/issues/333
[#342]: https://github.com/evanwtf/local-llm/issues/342
[#344]: https://github.com/evanwtf/local-llm/issues/344
[#354]: https://github.com/evanwtf/local-llm/issues/354
[#369]: https://github.com/evanwtf/local-llm/issues/369
