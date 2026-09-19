# What to actually run on a DGX Spark

**A local coding agent *served from* an NVIDIA DGX Spark (GB10, 128 GB unified,
Linux/aarch64, sm_121), driven from a laptop over your LAN.** Every number below
was measured by `benchmarks/agent/` on this machine with **OpenCode** — the only
client this file uses, so there is no client column. Nothing here is from a model
card. Paste section 1, pick a row in section 2, or read what does not help in 3.

**Ledger last read 2026-09-19** (#524). A read date more than a couple of weeks old means
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
CLI-agent playbook defaults to — served by **SGLang**, the fastest and most
reliable agent backend measured here ([#553]).

**On the Spark** — SGLang's official image, pinned by digest, thinking off,
bound to all interfaces. `--moe-runner-backend flashinfer_cutlass` is required:
the default runner refuses NVFP4 MoE on GB10. Running the container as your own
user keeps the Hugging Face cache yours:

```sh
# SGLang nightly (nightly-cu134-20260909-708f51e) serving Qwen3.6-35B-A3B-NVFP4 (~22 GiB)
docker run --rm --name a3b-sglang --gpus all --network host --ipc=host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp/home -e HF_HOME=/hf \
  -v "$HOME/.cache/huggingface:/hf" \
  lmsysorg/sglang@sha256:00205b89f74691f76a0ffbd6846376d9323971930a5d59bf63a65dadc7d67927 \
  python3 -m sglang.launch_server \
  --model-path nvidia/Qwen3.6-35B-A3B-NVFP4 --served-model-name qwen3.6-35b-a3b-nvfp4 \
  --host 0.0.0.0 --port 8030 --context-length 262144 --kv-cache-dtype fp8_e4m3 \
  --mem-fraction-static 0.60 --max-running-requests 8 \
  --moe-runner-backend flashinfer_cutlass \
  --reasoning-parser qwen3 --tool-call-parser qwen3_coder \
  --default-chat-template-kwargs '{"enable_thinking":false}'
```

**No Docker?** The same model on vLLM from a venv is the second row of section 2.
`nvcc` **and** `ninja` must be on `PATH` at launch, or it loads fully and then dies
at the FlashInfer JIT (see [`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md)):

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

**90/90 on our benchmark, 28.5 s median task, worst 136 s, on the box** ([#553]) —
**42% of the wall time** of the simplest option (llama.cpp Q3, 67.3 s). The same
model on vLLM, measured the same day on the same harness, went 83/90 at 30.9 s:
all seven of its misses are `script-transform`, where the agent ends in about 12 s
without creating `transform.py` ([#389], [#477]). SGLang passed that task 9 of 9.
Replace `SPARK-IP` with the Spark's LAN address; the WiFi round trip is on top
and unmeasured ([#562]).

**Prefer a plain binary?** The **llama.cpp Q3** row in section 2 is 236% of the
wall time but a single self-contained `llama-server`: no container, no venv, no
`nvcc`/`ninja`, no MoE quirks. If standing up SGLang or vLLM is friction, start there.

**`--dir` is not optional.** `opencode run` talks to a background server with its
own working directory, so it ignores where you launched it and writes files
elsewhere. That cost the sibling Mac project two weeks and 130 wasted trials.

---

## 2. Or pick a row

**Not a ranking.** Each row is here for the reason in its first column. Two axes
only — engine and model — plus how the laptop reaches it (all `:PORT/v1`).
"pass" is usable trials on the ledger; "median" is OpenCode wall seconds on the
box; "turns" is the median agent turn count.

| pick this if | model | engine | pass | median | worst | turns | note |
|---|---|---|---|---|---|---|---|
| **you want the agent done fastest** | Qwen3.6-**35B-A3B** `NVFP4`, thinking off | **SGLang** nightly (digest-pinned), `flashinfer_cutlass` MoE | **90/90** | **28.5 s** | **136 s** | 7.5 | fastest and the only A3B arm with no systematic miss ([#553]). 79.9 tok/s single-stream, 405 tok/s at 16 streams with `--max-running-requests 16` ([#308]) |
| **you want a pip-installed engine, or the most multi-client throughput** | same model | vLLM 0.29.0, MARLIN | **379/402 (94%)** | 35.3 s | 318 s | 11 | 452 tok/s at 16 concurrent streams with `--max-num-seqs 16` ([#335], [#308]). Misses `script-transform` systematically ([#477]); its same-day control against the SGLang row was 83/90, 30.9 s ([#553]) |
| **you want the simplest server to stand up** | Flash-Next `UD-Q3_K_XL`, thinking off | llama.cpp CUDA sm_121 | **331/331** | 67.3 s | 1,059 s | 10 | one `llama-server` binary, no venv. Re-measured 2026-09-19 on the current harness: 90/90, 69.6 s, worst 340 s ([#524]) |
| **…and the KV cache halved** | same, `q8_0` KV | llama.cpp, port 8022 | **90/90** | 61.4 s | 1,528 s | 9 | quality-neutral memory win ([#344]) |
| **you want a dense model** | Qwen3.8-**27B** `NVFP4` (RadixArk BF16-head), thinking off | **SGLang** nightly + **DFlash2** drafter (MiaAI-Lab `start-dflash.sh`) | **90/90** | 44.2 s | 279 s | 8 | 34.2 tok/s single-stream, 198.6 at 8 streams, 247 at 12. The DSpark drafter on the same image is 89/90, 53.7 s; on the older image, 65.6 s: the image and the drafter each account for about half the gap ([#303]). Recipe ships `--mem-fraction-static 0.90`; run it at 0.70 so an agent client fits beside it ([#350]) |
| **you want a larger model / 262K context** | Flash-Next `NVFP4` (NVIDIA's own checkpoint), thinking off | vLLM (MiaAI-Lab recipe), MTP-3 | **90/90** | 68.0 s | 1,115 s | 13.5 | the big-model lane. Thinking off is the fastest median but carries a long tail on the parser tasks; **thinking on** is 90/90, 120.8 s median, worst 281 s — the predictable choice ([#493]). The older styles01 build is 60/60, 95.4 s ([#331]) |
| **you want a second, non-Qwen lineage** | DeepSeek-V4-Flash Q2 | ds4 CUDA sm_121a | **60/60** | 247.6 s | 493 s | 10 | the only non-Qwen 100%-pass option ([#369] opens SSD streaming) |
| **you want it to show its work** | Flash-Next Q3, thinking on | llama.cpp CUDA sm_121 | **118/119 (99%)** | 127.4 s | 394 s | 10 | reasoning in the transcript, at 193% of the thinking-off Q3 row's wall ([#333]) |

Every row is regenerated from the ledger by `uv run python scripts/reco_rows.py
--results hardware/Cortex-X925-128GB-GB10/results.jsonl <backend>...` ([#524]):
**pass** counts every usable OpenCode trial; **median / worst / turns** count
only the non-script tasks that *passed*, the same basis as the generated tables
in `docs/results.md` (a trial that dies early is quick, [#142]). That basis is
why these medians differ from the full-suite figures this file quoted before
2026-09-18. All eight rows clear [#23]'s bar for a **>90%** claim; the
one-command Ollama option below (84%) does not.

**The one-command option, and why it is not a row above.** `ollama pull
qwen3.6:27b-coding` (~20 GiB, smallest download, no flags) gets you an agent —
but it scored **59/70 (84%)** at a 139 s median on this set, under the bar we'd
stand behind. Reach for it to kick the tyres, not to do the work.

**Do not reach for Ollama's `nvfp4` or `mxfp8` tags here** — this box's native
NVFP4 has no Ollama path. `27b-coding-nvfp4` and `27b-coding-mxfp8` publish as
MLX per-tensor layers (`application/vnd.ollama.image.tensor`), and Ollama 0.34.0
routes those to an MLX runtime that does not exist on a CUDA box — the pull fails
with *"this model requires MLX support"* before a byte moves. Only the plain
`27b-coding` (a GGUF `image.model` blob, q4) runs under Ollama here. For NVFP4,
serve it with vLLM (the fast row above), not Ollama (#293).

---

## 3. What does not help — and the trap in "fastest"

**What actually made the fastest lane fastest: cheaper turns, not fewer.** The
vLLM A3B row takes **more** turns than llama.cpp Q3 (11 vs 9) yet finishes in **half**
the wall time, because each turn costs **2.8 s vs 6.2 s**. A 35B model with only
~3B active parameters per token reads far fewer bytes per turn — for both decode
and re-prefill — so the per-turn cost, which [#317] identifies as the thing that
dominates agent wall time, collapses. Active-parameter count is the model lever
that paid off here (§4).

**Fastest tokens/sec is still not fastest agent.** The Flash-Next NVFP4 lanes have
the highest *raw* single-stream decode on the box (39–50 tok/s with MTP) — yet their
median agent wall (68.0 s for NVIDIA's checkpoint, thinking off) is **193% of the vLLM
A3B row's** (239% of the SGLang one). They run more turns (13.5 vs 11), and each turn costs more once re-prefill
is counted — the 125B-A6B Flash-Next has twice A3B's active params. [#354] also found
speculation can *itself* multiply agent turn count here, so the extra turns are not
cleanly attributable to one cause. If a benchmark
tweet quotes tok/s, it is not quoting what finishes your task.

**Measured and not recommended, 2026-09-19.**

- **Thinking on for the dense 27B.** 89/90 at 157.4 s, 356% of the thinking-off
  DFlash2 row's median, with no pass-rate gain ([#303]). The same held for
  Flash-Next (120.8 s vs 68.0 s, [#493]).

- **Flash-Next EXL3 through vLLM + vllm-exl3 with MTP k=3 corrupts output.** 2 of 5
  trials degenerated into tens of thousands of tokens of gibberish, and in both the
  draft acceptance collapsed to 0.02–0.07 (clean trials: 0.82–0.88). With MTP off,
  6 trials produced no corruption but thrashed (4/6, two coherent timeouts) ([#434]).
  A per-row acceptance collapse is a usable detector for this failure.
- **GLM-5.3-Flash EXL3 K2 serves on one Spark but leaves no room for an agent.** Idle
  MemAvailable settles at 14.5–16 GiB, under what a trial needs above the server's
  safety floor; 16.3 tok/s single-stream ([#298]).
- **The same 27B class on vLLM + MTP is 30/30 but takes 256% of the SGLang + DFlash2 row's
  median** (113.0 s vs 44.2 s on this basis) ([#303], [#350]).

**A median read across weeks drifts.** The vLLM A3B row's ledger-wide median fell
from 36.8 s to 35.3 s as rows accumulated, and its same-day control ran 30.9 s: most
of the gap first seen between vLLM and SGLang was harness and client drift, not the
engine ([#553]). Compare two rows on the same day and the same harness commit
before calling one faster.

**Setup traps worth knowing.** A server's `--gpu-memory-utilization` has to leave
room for the agent client *and* the box's own MemAvailable floor, or the harness's
headroom gate refuses the trial ([#485]); the recipe defaults above (0.80–0.90) are
set for a server alone. Thinking off is a server default via
`--default-chat-template-kwargs`; in launchers that build argv as a shell array the
JSON form loses its quotes, so use `--default-chat-template-kwargs.enable_thinking=false`.
SGLang's default `FLASHINFER_TRTLLM` MoE runner refuses NVFP4 MoE on GB10
(`NotImplementedError: Unsupported moe_runner_backend`); pass
`--moe-runner-backend flashinfer_cutlass` ([#553]).
A container engine that runs as root leaves `~/.cache/vllm/deep_gemm` root-owned,
which breaks DeepGEMM's JIT in a later venv engine; point `DG_JIT_CACHE_DIR` elsewhere.

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

**If you turn thinking on, give it a token budget.** With thinking on and a low
`max_tokens`, the model can spend the entire budget reasoning and return **empty
content** — a turn-1 death with no answer, not a wrong one. Measured on the A3B
NVFP4 server ([#349], temp 0, a one-line arithmetic prompt): `max_tokens` ≤ 256
returned empty content every time with `finish_reason=length` and
`reasoning_tokens` exactly equal to the ceiling; the answer only appeared at 512+,
where reasoning wanted ~400–550 tokens and needed room for the reply on top. The
same prompt with thinking **off** returned a correct short answer at every budget
down to `max_tokens=16`. So a thinking-on ceiling must clear the reasoning the
prompt induces *plus* the answer — an agent turn needs far more than 512 — or the
turn comes back empty. (Thinking-off, the default in §1, has no such floor.)

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
[#293]: https://github.com/evanwtf/local-llm/issues/293
[#308]: https://github.com/evanwtf/local-llm/issues/308
[#317]: https://github.com/evanwtf/local-llm/issues/317
[#331]: https://github.com/evanwtf/local-llm/issues/331
[#333]: https://github.com/evanwtf/local-llm/issues/333
[#335]: https://github.com/evanwtf/local-llm/issues/335
[#342]: https://github.com/evanwtf/local-llm/issues/342
[#344]: https://github.com/evanwtf/local-llm/issues/344
[#349]: https://github.com/evanwtf/local-llm/issues/349
[#354]: https://github.com/evanwtf/local-llm/issues/354
[#369]: https://github.com/evanwtf/local-llm/issues/369
[#142]: https://github.com/evanwtf/local-llm/issues/142
[#298]: https://github.com/evanwtf/local-llm/issues/298
[#303]: https://github.com/evanwtf/local-llm/issues/303
[#350]: https://github.com/evanwtf/local-llm/issues/350
[#434]: https://github.com/evanwtf/local-llm/issues/434
[#477]: https://github.com/evanwtf/local-llm/issues/477
[#485]: https://github.com/evanwtf/local-llm/issues/485
[#493]: https://github.com/evanwtf/local-llm/issues/493
[#524]: https://github.com/evanwtf/local-llm/issues/524
[#389]: https://github.com/evanwtf/local-llm/issues/389
[#553]: https://github.com/evanwtf/local-llm/issues/553
[#562]: https://github.com/evanwtf/local-llm/issues/562
