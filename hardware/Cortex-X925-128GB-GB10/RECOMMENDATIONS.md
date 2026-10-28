# What to run on one DGX Spark

**This page is for you if you have one NVIDIA DGX Spark and want a coding agent
that runs on it, not in the cloud.** It names the model and server to run,
gives the commands, and shows the tests behind each choice. With two Sparks,
read [the two-Spark page](../Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md).

**The results are from 2026-09-11 to 2026-10-24, and they stop there.** We
moved to two Sparks, and single-Spark testing stopped on 2026-10-23. Each row
gives its own dates. Models and servers change every week, so treat every row
here as a starting point, not a current measurement.

> **Review this agent's work.** On the long tasks below, no single-Spark setup
> passed every hidden test on more than 1 of 21 tasks. The agents finish, but
> their code often fits only the tests they could see.

---

## How we test

We give a coding agent real programming work, and we let the project's own
tests decide whether it did the work. No model grades another model.

**The agent.** [OpenCode](https://opencode.ai). In October it ran on a separate
laptop and sent its requests to the Spark over the network, as you would use
it. The September rows ran it on the Spark itself, so they leave out the
network round trip, which costs the fast rows the most ([#342]).

**The project.** [gmail-archive](https://github.com/evanwtf/gmail-archive) is
a real Python program that archives Gmail and serves it in a web page. Its
tests were written before this benchmark existed.

**The tasks.** There are three sets:

- **The standard set (10 tasks).** In 8 tasks we delete the body of one or two
  functions. We keep the name and the types, and usually the description, and
  ask the agent to write the body again. In 2 tasks the agent writes a small
  command-line script from a description.
- **Replay (7 tasks).** We take a real commit that added a feature, undo it,
  describe the feature in plain words, and ask the agent to build it again.
  The features change 37 to 583 lines across 1 to 9 files.
- **Hard (7 tasks).** Five replay features again, but we hide some of the
  tests from the agent. Two more tasks undo five commits at once: a rebuild of
  the web interface, 1,643 added lines across 17 files. One of the two does not
  tell the agent which tests to run.

**Pass or fail.** A trial passes when the tests the agent could see pass. A
trial fails if the agent edits a test, or if it does not finish in 30 minutes.
Every task runs 3 times. The September rows ran the standard set only, often
9 times a task.

**The hidden re-grade.** After a hard task, we put the hidden tests back and
run them ([#726]). An agent can pass the visible tests and still fail the
hidden ones: it wrote code to fit the tests it saw, not the feature.

**Time.** Each time on this page is a median over a set's passing trials: how
long one task takes.

**What this does not show.** It shows whether the agent finishes the job, not
whether the code is good. gmail-archive is public, so a model may have seen
these commits in training. The full method is in
[`METHODOLOGY.md`](../../benchmarks/agent/METHODOLOGY.md).

---

## 1. Run the fastest choice

**Qwen3.6-35B-A3B, NVFP4, on SGLang, with thinking off** ([#553], [#1087]).

**On the Spark.** This is SGLang's official image, pinned by digest.
`--moe-runner-backend flashinfer_cutlass` is required: the default runner
refuses this model's format on the GB10.

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

**On the laptop.** Replace `SPARK-IP` with the Spark's address on your network:

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
opencode run --standalone "add a --verbose flag to the CLI and a test for it"
```

**Run it from the project directory, with `--standalone`.** OpenCode 2 works
in the directory you launch it from; without `--standalone` it attaches to a
background service that can hang the next run (#1086). OpenCode 1.x needs
`--dir "$PWD"` instead, or it writes the files somewhere else. To run it without Docker, or from a single
binary, see section 2 and [`howto.md`](howto.md).

**Why this one.** On 2026-10-20 it passed every visible test: standard 30/30,
replay 21/21, hard 21/21. It was the fastest row in section 2 on every set.
But it passed only 58.4% of the hidden tests, the lowest in that table. It is
the fastest choice, not the most correct one.

---

## 2. Or choose by what you need

These rows ran from the laptop, on OpenCode 2.0.29 to 2.1.1, with all three
task sets. They compare with each other.

| choose this if | model and server | tested | standard | replay | hard | hidden tests passed |
|---|---|---|---|---|---|---|
| **you want the fastest agent** | Qwen3.6-35B-A3B NVFP4, thinking off, on SGLang ([#1087]) | 2026-10-20 | 30/30, 34.8 s | **21/21**, 219.4 s | **21/21**, 377.2 s | 223 of 382 (58.4%); 0 of 21 tasks whole |
| **you want an NVIDIA model that finishes long tasks** | Nemotron-3.5-Bolt-74B-A9B (IQ2 QAT GGUF), thinking off, on llama.cpp b11669 + LiLiCorr DFlash ([#1103]) | 2026-10-15 to 10-16 | 30/30, 85.7 s | 19/21, 435.5 s | 18/21, 780.1 s | 203 of 315 (64.4%); 0 of 21 |
| **you want a dense model** | Qwen3.8-27B NVFP4, thinking off, on SGLang + DFlash2 ([#1091]) | 2026-10-20 | 30/30, 78.2 s | 20/21, 500.2 s | 16/21, 946.2 s | 172 of 266 (64.7%); 0 of 21 |
| **you want the simplest server: one binary, no Python** | Qwen3.8-Flash-Next UD-Q3_K_XL, thinking off, on llama.cpp ([#1089]) | 2026-10-15 | 29/30, 70.5 s | 20/21, 413.0 s | 12/21, 823.4 s | 150 of 199 (75.4%); 1 of 21 |

"Tasks whole" counts hard tasks where every hidden test passed. A trial that
times out gets no hidden result, so a setup that fails the long tasks is
graded on fewer, shorter ones: its percentage looks better than it is. The
"tasks whole" count has no such bias, and it is 0 or 1 for every row.

**For a dense model, use NVIDIA's checkpoint.** On 2026-10-23 and 10-24,
`nvidia/Qwen3.8-27B-NVFP4` (@`482ca0f3`) did at least as well as the build in
the table: standard 30/30 against 29/30, replay + hard 37/42 against 34/41.
It is 11 GB smaller ([#1220]).

---

## 3. Tested and not chosen

| model and server | tested | result | issue |
|---|---|---|---|
| Qwen3.6-35B-A3B NVFP4 on vLLM 0.32.0 | 2026-10-22 | replay + hard 41/42 at 105.3% of the fastest choice's time; hidden 192 of 361 (53.2%). No gain | [#1088] |
| Flash-Next Q3 with a half-size (`q8_0`) KV cache, llama.cpp b11827 | 2026-10-23 | standard 30/30; replay + hard 17 of 26, all 9 failures 30-minute timeouts | [#1090] |
| Nemotron-3.5-Bolt on TensorFold | 2026-10-18 | standard 29/30; replay 20/21; hard 15/21; hidden 156 of 222 (70.3%), 2 invalid | [#1127] |
| Qwen3.8-Flash-Next FP4-Local on vLLM | 2026-10-17 | replay 16/20, hard 11/17; stopped for too many failures | [#1108] |
| Qwen3.8-Flash-Next FP4-Local on TensorFold | 2026-10-21 | replay 11/14, hard 7/14; stopped for too many failures | [#1143] |
| Flash-Next EXL3 on vllm-exl3 with MTP | September | corrupt output | [#434] |
| GLM-5.3-Flash EXL3 K2 | September | fits, but leaves no memory for the agent | [#298] |
| Ollama, `qwen3.6:27b-coding` | September | 59 of 70 (84%); its `nvfp4` and `mxfp8` tags need MLX and do not run here | [#293] |

---

## 4. Older results: the standard set only

These rows ran before the laptop setup: from 2026-09-11 to 09-19 with the
agent on the Spark itself (OpenCode 1.18), and the Lightning row on 2026-10-14
from the laptop (OpenCode 1.19.0). They ran only the standard set, so they do
not compare with section 2. "Worst" is the slowest passing task.

| setup | tested | passed | median | worst | issue |
|---|---|---|---|---|---|
| Qwen3.6-35B-A3B NVFP4 on SGLang (the fastest choice) | 2026-09-19 | **90/90** | **28.5 s** | 136 s | [#553] |
| the same model on vLLM 0.29.0: a pip install, and 452 tokens/s at 16 requests at once | 2026-09-14 to 09-19 | 379/402 (94%) | 35.3 s | 318 s | [#335], [#477] |
| Flash-Next Q3 on llama.cpp (the simplest server) | 2026-09-12 to 09-19 | **331/331** | 67.3 s | 1,059 s | [#524] |
| the same, with a half-size `q8_0` KV cache | 2026-09-12 to 09-14 | 90/90 | 61.4 s | 1,528 s | [#344] |
| Qwen3.8-27B NVFP4 on SGLang + DFlash2 (the dense model) | 2026-09-18 to 09-19 | 90/90 | 44.2 s | 279 s | [#303], [#350] |
| Flash-Next NVFP4 (NVIDIA) on vLLM with MTP-3, for 262K context | 2026-09-18 | 90/90 | 68.0 s | 1,115 s | [#493] |
| Flash-Next Q3 on llama.cpp, thinking on, to show its reasoning | 2026-09-11 to 09-14 | 118/119 | 127.4 s | 394 s | [#333] |
| Nemotron-3.5-Lightning-30B-A3B NVFP4 on TensorFold 1.0.11 | 2026-10-14 | 30/30 | 33.7 s | 236.5 s | [#1066], [#1093] |

What these runs taught, and still holds:

- **Fast turns beat fast tokens.** The vLLM row took more turns than the Q3
  row (11 against 9) in half the time, because each turn cost 2.8 s against
  6.2 s. The Flash-Next NVFP4 setups had the highest token rate (39 to 50
  tokens/s) and took 193% of the vLLM row's time ([#317], [#354]).
- **Thinking on costs time and gains no passes.** The dense 27B with thinking
  on went 89/90 at 157.4 s, 356% of its time with thinking off ([#303]). With
  thinking on, give it a token budget: at `max_tokens` 256 or less, a short
  prompt came back empty every time ([#349]).
- **If the agent runs on the Spark too, leave memory for it** beside the
  server ([#485]). llama.cpp has no FP8 KV cache type here ([#344]).

---

## Where the rest went

| for | see |
|---|---|
| this page before 2026-10-27, with the full reasoning | [`recommendations-archive-2026-10-27.md`](recommendations-archive-2026-10-27.md) (frozen) |
| every row, step by step from a clean Spark | [`howto.md`](howto.md) |
| running the box: ports, Prometheus, the vLLM setup | [`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md) |
| every trial, one JSON line each | [`results.jsonl`](results.jsonl) |
| a section 4 row, again | `uv run python scripts/reco_rows.py --results hardware/Cortex-X925-128GB-GB10/results.jsonl <backend>...` |

This page stays at or under 2,000 words: `tests/test_recommendations_budget.py`
fails CI past that, and its docstring says where new evidence goes.

[#293]: https://github.com/evanwtf/local-llm/issues/293
[#298]: https://github.com/evanwtf/local-llm/issues/298
[#303]: https://github.com/evanwtf/local-llm/issues/303
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
[#1087]: https://github.com/evanwtf/local-llm/issues/1087
[#1088]: https://github.com/evanwtf/local-llm/issues/1088
[#1089]: https://github.com/evanwtf/local-llm/issues/1089
[#1090]: https://github.com/evanwtf/local-llm/issues/1090
[#1091]: https://github.com/evanwtf/local-llm/issues/1091
[#1093]: https://github.com/evanwtf/local-llm/issues/1093
[#1103]: https://github.com/evanwtf/local-llm/issues/1103
[#1108]: https://github.com/evanwtf/local-llm/issues/1108
[#1127]: https://github.com/evanwtf/local-llm/issues/1127
[#1143]: https://github.com/evanwtf/local-llm/issues/1143
[#1220]: https://github.com/evanwtf/local-llm/issues/1220
