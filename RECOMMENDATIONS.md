# What to actually run

**A local coding agent on an Apple Silicon Mac, when you cannot or will not use
a hosted provider.**

This file tells a stranger what to install, in what order, and what to expect.
Every number in it was measured on one machine — an **M5 Max with 128 GB of
unified memory** — by the benchmark in `benchmarks/agent/`. Nothing here is
copied from a model card or a blog post.

Written 2026-09-01, after re-measuring everything (#67). If you read an earlier
version of this file, discard it: four of the five stacks it ranked were ranked
on numbers that measured a bug in our own test harness, not the software. The
old file is at `docs/archive/RECOMMENDATIONS-2026-08-29.md` and the explanation
is in [`docs/archive/results-opencode-pre-dir.md`](docs/archive/results-opencode-pre-dir.md).

---

## If you read nothing else

**Start here.** It is the smallest download, the easiest install, and it leaves
your Mac usable while it runs:

```sh
# 1. The agent (the thing you type at)
curl -fsSL https://opencode.ai/install | bash

# 2. The server + model (31 GB)
brew install ollama
ollama serve &
ollama pull qwen3.6:27b-coding-mxfp8

# 3. Tell the agent about the server
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/qwen3.6:27b-coding-mxfp8",
  "provider": {
    "ollama": {
      "name": "ollama (local)",
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://127.0.0.1:11434/v1", "apiKey": "ollama" },
      "models": { "qwen3.6:27b-coding-mxfp8": { "tool_call": true } }
    }
  }
}
JSON

# 4. Use it
cd ~/some/project
opencode run --dir "$PWD" "add a --verbose flag to the CLI and a test for it"
```

That is a complete working local coding agent. **18/18 on our benchmark**, and
it needs 31 GB, so you can keep using your machine for other things.

**The `--dir` flag is not optional.** `opencode run` talks to a background
server that keeps its own working directory, so it ignores the directory you
launched it from. Leave `--dir` out and it will happily solve your task and
write the files somewhere else. This cost us two weeks and 130 wasted trials.

---

## The three stacks worth running

| | model | server | download | pass rate | median task |
|---|---|---|---|---|---|
| **1. Fastest** | Qwen3.8-Flash-Next `UD-Q3_K_XL` | llama.cpp | 84 GB | **21/21** | **90s** |
| **2. Different lineage** | DeepSeek-V4-Flash | ds4 (DwarfStar) | 91 GB | **21/21** | 115s |
| **3. Start here** | Qwen3.6-27B-coding `mxfp8` | Ollama | 31 GB | **18/18** | 167s |

All three drive **OpenCode**, and that is deliberate. The whole point of a local
setup is that it keeps working when a vendor does not — so the agent has to be
open too. A proprietary client on an open model fails with its vendor.

**Why you might want #2 rather than #1.** #1 and #3 are both Qwen models. If
your reason for running locally is that a model might become unavailable, then
two of the three share a maintainer. DeepSeek-V4-Flash is the only stack here
with a genuinely independent lineage.

**Why we do not rank them by speed alone.** See the spread column below. A
median hides how bad the bad runs get.

---

## Measured results

<!-- BEGIN GENERATED -->

*Generated from `results.jsonl` — 999 rows, sha256 c6d518ee5e54.*

#### Every stack measured under OpenCode

| stack | passed | median | worst | spread |
|---|---|---|---|---|
| Qwen3.8-Flash-Next Q3 - llama.cpp | 27/27 | 90s | 208s | 4.8x |
| DeepSeek-V4-Flash - ds4 (Anthropic wire) | 18/18 | 110s | 221s | 4.3x |
| DeepSeek-V4-Flash - ds4 | 27/27 | 115s | 230s | 4.3x |
| Qwen3.8-Flash-Next Q3 - LM Studio | 18/18 | 122s | 261s | 4.2x |
| Qwen3.6-27B-coding - Ollama | 24/24 | 167s | 700s | 12.6x |
| GLM-5.3-Flash - ds4 | 22/24 | 368s | 1227s | 18.0x |

Excision tasks only; `script-*` excluded because they are a different class. **Spread is worst / best on the same task**, and it is the column most people forget to ask for.

#### Same weights, two engines

| task | llama.cpp | LM Studio |
|---|---|---|
| `mbox-scan` | 108s | 140s |
| `mbox-strip-envelope` | 50s | 94s |
| `parser-date` | 164s | 238s |
| `parser-mbox-quoting` | 70s | 93s |
| `script-reverse` | 41s | 57s |
| `storage-blob-put` | 89s | 124s |

#### How fast each stack actually serves tokens

| stack | seconds per 1k output tokens |
|---|---|
| Qwen3.8-Flash-Next Q3 - llama.cpp | 43s |
| DeepSeek-V4-Flash - ds4 (Anthropic wire) | 54s |
| GLM-5.3-Flash - ds4 | 55s |
| Qwen3.6-27B-coding - Ollama | 69s |
| DeepSeek-V4-Flash - ds4 | 71s |
| Qwen3.8-Flash-Next Q3 - LM Studio | 134s |

<!-- END GENERATED -->

**Reading the spread column.** It is the worst run divided by the best run *on
the same task*. Anything near 4x is ordinary — these models sample at
temperature and sometimes write four times as much code to solve the same
problem. The two at 12x and 18x are different in kind: on one task, GLM-5.3 took
**99 seconds once and 1,227 seconds another time**. It got the right answer both
times.

**That variance is the agent, not the machine.** We checked. Across 113 trials,
wall time correlates with output tokens at **0.97** and with turns taken at
0.77, while seconds-per-turn — the part the hardware controls — varies only
1.18x. A slow run is one where the agent wrote more and took more turns, not one
where the computer was busy.

**Which is why GLM-5.3-Flash is not in the top three.** It serves tokens faster
than almost anything here (47s per 1k) and it passes 16/18. But you cannot plan
around it: a task that usually takes 90 seconds will occasionally take twenty
minutes.

---

## Full instructions

### Before anything: the memory ceiling

**Skip this if you are only running stack 3.** For the 84 GB and 91 GB models,
macOS will not let the GPU hold enough memory by default, and the failure looks
like the model refusing to load for no clear reason.

```sh
sudo sysctl iogpu.wired_limit_mb=114688     # 112 GiB, on a 128 GB Mac
sysctl -n iogpu.wired_limit_mb              # expect 114688
```

This is a **cap, not a reservation** — with it set and nothing loaded, the GPU
holds about 5 GB. It does not survive a reboot on its own; this repo has
`scripts/install-metal-ceiling.sh` to make it permanent via a LaunchDaemon.

**A reading of `0` means "system default", not "no limit".** After a reboot, `0`
means your setting did not apply.

### Stack 1 — Qwen3.8-Flash-Next on llama.cpp (fastest)

```sh
brew install cmake

git clone https://github.com/ggml-org/llama.cpp ~/git/llama.cpp
cmake -B ~/git/llama.cpp/build -S ~/git/llama.cpp -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release
cmake --build ~/git/llama.cpp/build -j "$(sysctl -n hw.ncpu)"

pip install -U "huggingface_hub[cli]"
hf download unsloth/Qwen3.8-Flash-Next-GGUF \
    --include "UD-Q3_K_XL/*" \
    --local-dir ~/models/Qwen3.8-Flash-Next-GGUF        # 84 GB

~/git/llama.cpp/build/bin/llama-server \
    -m ~/models/Qwen3.8-Flash-Next-GGUF/UD-Q3_K_XL/Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf \
    -a qwen3.8-flash-next-q3 --host 127.0.0.1 --port 8020 \
    -c 131072 -np 1 --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0
```

Then add this provider to `~/.config/opencode/opencode.json` and set
`"model": "llamacpp/qwen3.8-flash-next-q3"`:

```json
"llamacpp": {
  "name": "llama.cpp (local)",
  "npm": "@ai-sdk/openai-compatible",
  "options": { "baseURL": "http://127.0.0.1:8020/v1", "apiKey": "local" },
  "models": { "qwen3.8-flash-next-q3": { "tool_call": true } }
}
```

**Three flags that are not decoration:**

- **`-np 1`.** llama.cpp defaults to four slots, each holding a full-size KV
  cache for concurrency a single agent never uses. One slot buys you double the
  context for 1.7 GB.
- **`-c 131072`.** At 65536 the agent thrashes its own context compaction and a
  task that takes 110 seconds took 842.
- **The sampler.** These are Qwen's published values. A different `top_p`
  measurably changed our pass rate (0.95 gave 20/21; 0.90 gave 7/15).

Pass the **first shard** of the three; llama.cpp finds the rest.

### Stack 2 — DeepSeek-V4-Flash on ds4

```sh
git clone https://github.com/antirez/ds4 ~/git/ds4
cd ~/git/ds4 && make -j "$(sysctl -n hw.ncpu)"
./download_model.sh                    # ~91 GB, follow its prompts
./ds4-server -m gguf/<the-file-it-downloaded>.gguf --ctx 100000 --port 8000
```

Provider block, with `"model": "ds4/deepseek-v4-flash"`:

```json
"ds4": {
  "name": "ds4 (local)",
  "npm": "@ai-sdk/openai-compatible",
  "options": { "baseURL": "http://127.0.0.1:8000/v1", "apiKey": "local" },
  "models": { "deepseek-v4-flash": { "tool_call": true } }
}
```

**Run `ds4-server` from inside its own directory.** It looks for its Metal
shaders relative to the working directory and fails to start if you do not.

### Stack 3 — see the top of this file

---

## What we are not recommending, and why

**LM Studio.** It works — 18/18, same weights as stack 1 — and its GUI is the
easiest way to get a model running. But it served the *identical* file at
**134 seconds per 1,000 tokens against llama.cpp's 42**, and lost on five of six
tasks. If you want a GUI, use it; if you want the machine's speed, do not.

**GLM-5.3-Flash.** 16/18 and genuinely fast per token, but on one task its
three runs took 99 s, 378 s and 1,227 s. Excellent model, unpredictable to plan
around. Revisit it.

**Anything ranked by tokens per second.** This project has now measured three
times that decode rate does not predict how long a real task takes. The 3-bit
quant of Qwen3.8-Flash-Next decodes *slower per token* than the 2-bit one and
finishes the suite **28% faster**. Two engines served identical weights with
identical correctness and wall clocks of 80.5 s against 151.7 s on one task.
Tokens per second is the
number everyone publishes and it inverted our ranking.

**A second machine, a bigger quant, exotic offloading.** All measured, none
paid. See `benchmarks/agent/RESULTS.md`.

---

## How much should you trust this?

**The pass rates are strong; the speed rankings are weaker than they look.**

- Three trials pins a task's median to about **±28%**, so two stacks need to
  differ by roughly 56% before the difference is real. Stacks 1 and 2 (90s vs
  115s) are **not** reliably distinguishable. Stack 3 (167s) is.
- A perfect run of 21/21 supports "above 85%" at 95% confidence, not "100%".
  Nothing here has run the ~35 consecutive trials a >90% claim needs.
- Every stack was measured on **one machine**, on **one repository**, on six
  tasks. Your code is not our code.

**What we are confident about**: all three stacks work, none of them is a trap,
and the client you drive them with matters more than most model choices — on
identical weights and server, we measured the same task at **6.4 seconds under
one client and 103.3 seconds under another**.

## Reproducing this

```sh
git clone https://github.com/evanwtf/local-llm && cd local-llm
uv sync
uv run python benchmarks/agent/preflight.py        # checks servers, versions, config
uv run python benchmarks/agent/run.py --client opencode --backend qwen38fnq3 --trials 3
```

The tables above are generated from `benchmarks/agent/results.jsonl` by
`gen_tables.py` and spliced in by `splice_tables.py` — they are never typed by
hand, and a test fails if this file drifts from the data.
