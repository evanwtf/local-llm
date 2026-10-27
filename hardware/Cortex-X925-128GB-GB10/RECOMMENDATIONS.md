# What to actually run on a DGX Spark

**A local coding agent *served from* an NVIDIA DGX Spark (GB10, 128 GB unified,
Linux/aarch64, sm_121), driven from a laptop over your LAN.** Every number below
was measured by `benchmarks/agent/` on this machine with **OpenCode**. Nothing
here is from a model card. Paste section 1, pick a row in section 2, or read
what does not help in section 3. For two Sparks, read
[the two-Spark page](../Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md).

**Ledger last read 2026-10-21** (the S1–S8 refresh, [#1086]; Bolt, [#1103],
[#1127]; FP4-Local, [#1108], [#1143]). A read date more than a couple of weeks
old means re-check `results.jsonl` before trusting these rows.

> **Visible tests overstate every row here.** On the replay and hard sets, no
> one-Spark stack passed the held-out tests ([#726]) on more than 1 of 21 whole
> tasks. Read [the held-out table](#replay-hard-and-the-held-out-tests-2026-10-21)
> before you trust an agent's work unreviewed.

> **Not measured: the LAN round trip.** Every median was taken on the box. A
> laptop over WiFi adds a per-turn cost that hurts the fast rows most, so treat
> each median as a lower bound ([#342]).

---

## 1. Paste this

The fastest **agent** row (S1): **Qwen3.6-35B-A3B NVFP4** on **SGLang** ([#553]).

**On the Spark.** SGLang's official image, pinned by digest, thinking off.
`--moe-runner-backend flashinfer_cutlass` is required: the default runner
refuses NVFP4 MoE on GB10.

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

**On the laptop.** Replace `SPARK-IP` with the Spark's LAN address:

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

**90/90 on our benchmark, 28.5 s median task, worst 136 s** ([#553]): 42% of the
wall time of the simplest option (llama.cpp Q3, 67.3 s). Refreshed 2026-10-20
([#1087]): standard 30/30, replay + hard 42/42, but 0 of 21 whole held-out tasks
and 223 of 382 held-out tests (58.4%), the lowest rate on either machine. It is
the fastest row, not the most correct one. Review its work.

**`--dir` is not optional.** `opencode run` ignores where you launched it, and
without `--dir` it writes the files somewhere else. That cost the Mac project
two weeks and 130 wasted trials. No Docker, or a plain binary instead: rows S2
and S3 below, step by step in [`howto.md`](howto.md).

---

## 2. Or pick a row

**Not a ranking.** Each row is here for the reason in its first column. "pass"
is usable trials on the ledger; "median" and "worst" are OpenCode wall seconds
on passing non-script tasks; "turns" is the median turn count. S1–S8 are the
[#1086] picks, one issue each.

| pick this if | model | engine | pass | median | worst | turns | note |
|---|---|---|---|---|---|---|---|
| **S1: you want the agent done fastest** | Qwen3.6-35B-A3B `NVFP4`, thinking off | SGLang nightly (digest-pinned) | **90/90** | **28.5 s** | **136 s** | 7.5 | 405 tok/s at 16 streams ([#553], [#308], [#1087]) |
| **S2: a pip-installed engine, or the most multi-client throughput** | same model | vLLM 0.29.0, MARLIN | 379/402 (94%) | 35.3 s | 318 s | 11 | 452 tok/s at 16 streams; misses `script-transform` systematically ([#335], [#477], [#1088]) |
| **S3: the simplest server to stand up** | Flash-Next `UD-Q3_K_XL`, thinking off | llama.cpp CUDA sm_121 | **331/331** | 67.3 s | 1,059 s | 10 | one `llama-server` binary, no venv ([#524], [#1089]) |
| **S4: …and the KV cache halved** | same, `q8_0` KV | llama.cpp | **90/90** | 61.4 s | 1,528 s | 9 | quality-neutral memory win ([#344], [#1090]) |
| **long tasks finished, with an NVIDIA model** | Nemotron-3.5-Bolt-74B-A9B (IQ2 QAT GGUF), thinking off | llama.cpp b11669 + LiLiCorr DFlash | **30/30** | 85.7 s | 123.9 s | 8.5 | 37/42 on replay + hard, the most after S1 ([#1103]) |
| **S5: a dense model** | Qwen3.8-27B `NVFP4`, thinking off | SGLang + DFlash2 (MiaAI-Lab) | **90/90** | 44.2 s | 279 s | 8 | run at `--mem-fraction-static 0.70` so a client fits ([#303], [#350], [#1091]) |
| **S6: a larger model / 262K context** | Flash-Next `NVFP4` (NVIDIA), thinking off | vLLM (MiaAI-Lab recipe), MTP-3 | **90/90** | 68.0 s | 1,115 s | 13.5 | thinking on: 90/90, 120.8 s, worst 281 s ([#493], [#1092]) |
| **S7: a second, non-Qwen lineage** | Nemotron-3.5-Lightning-30B-A3B `NVFP4` | TensorFold 1.0.11 native, MTP | **30/30** | 33.7 s | 236.5 s | 11.5 | replay 16/21 ([#1066], [#1093]) |
| **S8: it shows its work** | Flash-Next Q3, thinking on | llama.cpp CUDA sm_121 | 118/119 (99%) | 127.4 s | 394 s | 10 | 193% of S3's wall ([#333], [#1094]) |

Every row regenerates with `uv run python scripts/reco_rows.py --results
hardware/Cortex-X925-128GB-GB10/results.jsonl <backend>...` ([#524]). All nine
clear [#23]'s bar for a >90% claim on the standard set.

### Replay, hard, and the held-out tests (2026-10-21)

The same i9 client ran every row, on OpenCode 2.0.29 to 2.1.1; medians are of
passing trials.

| stack | standard | replay | hard | held-out: whole tasks | held-out: tests |
|---|---|---|---|---|---|
| S1: A3B NVFP4, SGLang ([#1087]) | 30/30, 34.8 s | **21/21**, 219.4 s | **21/21**, 377.2 s | 0/21 | 223/382 (58.4%) |
| Bolt, llama.cpp + DFlash ([#1103]) | 30/30, 85.7 s | 19/21, 435.5 s | 18/21, 780.1 s | 0/21 | 203/315 (64.4%) |
| S5: 27B NVFP4, SGLang + DFlash2 ([#1091]) | 30/30, 78.2 s | 20/21, 500.2 s | 16/21, 946.2 s | 0/21 | 172/266 (64.7%) |
| Bolt, TensorFold ([#1127]) | 29/30, 95.6 s | 20/21, 496.9 s | 15/21, 681.4 s | 0/21 | 156/222 (70.3%), 2 invalid |
| S3: Flash-Next Q3, llama.cpp ([#1089]) | 29/30, 70.5 s | 20/21, 413.0 s | 12/21, 823.4 s | 1/21 | 150/199 (75.4%) |
| Flash-Next FP4-Local, vLLM ([#1108]) | 30/30, 40.5 s | 16/20 | 11/17 | 1/17 | 146/175 (83.4%) |
| Flash-Next FP4-Local, TensorFold ([#1143]) | 30/30, 36.8 s | 11/14 | 7/14 | 1/14 | 89/101 (88.1%) |

A trial that times out has no held-out result, so a stack that fails the long
tasks is graded on fewer, shorter ones: the FP4-Local rows have the highest
rates over the fewest tests. The whole-task column has no such bias, and it is
0 or 1 for every stack. No row is safe unreviewed on long work.

---

## 3. What does not help

- **Fast turns beat fast tokens.** S2 takes more turns than S3 (11 against 9)
  in half the wall time, because each turn costs 2.8 s against 6.2 s: about 3B
  active parameters read far fewer bytes per turn. The Flash-Next NVFP4 lanes
  have the highest raw decode (39–50 tok/s) and a median agent wall at 193% of
  S2's ([#317], [#354]).
- **Thinking on costs time and gains no passes.** The dense 27B went 89/90 at 157.4 s,
  356% of S5's median ([#303]). With thinking on, give it a token budget: on a
  short prompt, `max_tokens` 256 or less returned empty content every time
  ([#349]).
- **Thinking off costs character-level exactness.** It reversed "mailbox" as
  `xioblam`; thinking on got `xobliam`.
- **Ollama.** `ollama pull qwen3.6:27b-coding` works but scored 59/70 (84%),
  under the bar. Its `nvfp4` and `mxfp8` tags need MLX and do not run here
  ([#293]).
- **Measured and not recommended:** Flash-Next FP4-Local for long tasks (both
  engines hit the failure stop on replay + hard, [#1108], [#1143]); Flash-Next
  EXL3 through vllm-exl3 with MTP (corrupt output, [#434]); GLM-5.3-Flash EXL3
  K2 (fits, but leaves no room for an agent, [#298]).
- **Setup traps.** Leave memory for the client beside the server ([#485]); pass
  `--moe-runner-backend flashinfer_cutlass` to SGLang ([#553]); llama.cpp has no
  FP8 KV type here, and `-ub 2048` moved nothing ([#344]).

---

## Where the rest went

| for | see |
|---|---|
| this page before the 2,000-word budget, with the full reasoning of sections 3 and 4 | [`recommendations-archive-2026-10-27.md`](recommendations-archive-2026-10-27.md) (frozen) |
| every row stood up step by step, from a clean Spark | [`howto.md`](howto.md) |
| operating the box: ports, Prometheus, the vLLM venv + `nvcc`/`ninja` launch | [`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md) |
| how any single number was measured | the issue it cites, and `results.jsonl` |

This page stays at or under 2,000 words: `tests/test_recommendations_budget.py`
fails CI past that, and its docstring says where new evidence goes.

[#23]: https://github.com/evanwtf/local-llm/issues/23
[#293]: https://github.com/evanwtf/local-llm/issues/293
[#298]: https://github.com/evanwtf/local-llm/issues/298
[#303]: https://github.com/evanwtf/local-llm/issues/303
[#308]: https://github.com/evanwtf/local-llm/issues/308
[#317]: https://github.com/evanwtf/local-llm/issues/317
[#333]: https://github.com/evanwtf/local-llm/issues/333
[#335]: https://github.com/evanwtf/local-llm/issues/335
[#342]: https://github.com/evanwtf/local-llm/issues/342
[#344]: https://github.com/evanwtf/local-llm/issues/344
[#349]: https://github.com/evanwtf/local-llm/issues/349
[#350]: https://github.com/evanwtf/local-llm/issues/350
[#354]: https://github.com/evanwtf/local-llm/issues/354
[#434]: https://github.com/evanwtf/local-llm/issues/434
[#477]: https://github.com/evanwtf/local-llm/issues/477
[#485]: https://github.com/evanwtf/local-llm/issues/485
[#493]: https://github.com/evanwtf/local-llm/issues/493
[#524]: https://github.com/evanwtf/local-llm/issues/524
[#553]: https://github.com/evanwtf/local-llm/issues/553
[#726]: https://github.com/evanwtf/local-llm/issues/726
[#1066]: https://github.com/evanwtf/local-llm/issues/1066
[#1086]: https://github.com/evanwtf/local-llm/issues/1086
[#1087]: https://github.com/evanwtf/local-llm/issues/1087
[#1088]: https://github.com/evanwtf/local-llm/issues/1088
[#1089]: https://github.com/evanwtf/local-llm/issues/1089
[#1090]: https://github.com/evanwtf/local-llm/issues/1090
[#1091]: https://github.com/evanwtf/local-llm/issues/1091
[#1092]: https://github.com/evanwtf/local-llm/issues/1092
[#1093]: https://github.com/evanwtf/local-llm/issues/1093
[#1094]: https://github.com/evanwtf/local-llm/issues/1094
[#1103]: https://github.com/evanwtf/local-llm/issues/1103
[#1108]: https://github.com/evanwtf/local-llm/issues/1108
[#1127]: https://github.com/evanwtf/local-llm/issues/1127
[#1143]: https://github.com/evanwtf/local-llm/issues/1143
