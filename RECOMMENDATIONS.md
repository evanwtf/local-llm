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

**These rows are not a ranking.** Each one is here for a different reason, and
the reason is the first column. The quick-start above installs the first row.

| pick this if | model | server | download | pass rate | median task |
|---|---|---|---|---|---|
| **you are starting out** | Qwen3.6-27B-coding `mxfp8` | Ollama | 31 GB | **18/18** | 167s |
| **you want it fast** | Qwen3.8-Flash-Next `UD-Q3_K_XL` | llama.cpp | 84 GB | **21/21** | **90s** |
| **you want a second lineage** | DeepSeek-V4-Flash | ds4 (DwarfStar) | 91 GB | **21/21** | 115s |

All three drive **OpenCode**, and that is deliberate. The whole point of a local
setup is that it keeps working when a vendor does not — so the agent has to be
open too. A proprietary client on an open model fails with its vendor.

**Why the slowest one is the one to install first.** It is 31 GB against 84 GB,
it installs with two `brew`/`ollama` commands, and it leaves enough memory that
you can keep working while it runs. The two faster stacks want most of a 128 GB
machine. Median task time of 167s against 90s is a real difference, but it is
the difference between a coffee and a shorter coffee — it is not what will
decide whether you keep using this.

**Why a second lineage is worth 91 GB.** The first two rows are both Qwen
models. If your reason for running locally is that a model might one day be
unavailable to you, then betting on one maintainer rebuilds the problem you
were trying to escape. DeepSeek-V4-Flash is the only stack here with a
genuinely independent lineage.

**Why we do not rank on median alone.** See the spread column below. A median
hides how bad the bad runs get.

**Why the fastest measured backend is not on this list.** `ornith15` tops the
table below — 21/21 under OpenCode, 44s median, faster than anything else we
have run. It is still not the one to install, for two reasons that the median
hides. It is **the only backend in this project's whole record that has
produced wrong code**: it failed twice on an excision task under an earlier
client, and it emitted Swift that did not compile from a run that otherwise
looked completely normal — clean exit, no error, 30 tool calls
([#45](https://github.com/evanwtf/local-llm/issues/45)). And its **worst run is
30x its median**, against 4–6x for everything above. Fastest-on-average and
occasionally, quietly wrong is a bad trade when you are not watching. The
numbers are published because they are real; the recommendation withholds it on
purpose.

---

## What is being measured

Every number below comes from a real coding agent doing a real task, timed end
to end. There are two kinds of task, and they measure different things.

**Excision tasks.** The agent gets a checkout of a real Python repository
([`gmail-archive`](https://github.com/evanwtf/gmail-archive), pinned at one
commit) in which **one function body has been deleted** and replaced with
`raise NotImplementedError`. The repository's own test suite is the only oracle.
No test is shown to the agent as a target, and editing tests is forbidden and
checked afterwards. This measures whether a stack can find its way around code
it has never seen.

| task | what the agent is asked to do |
|---|---|
| [`mbox-strip-envelope`](benchmarks/agent/PROMPTS.md#mbox-strip-envelope) | implement `strip_envelope` in an mbox parser |
| [`parser-mbox-quoting`](benchmarks/agent/PROMPTS.md#parser-mbox-quoting) | implement `unquote_mbox`, which must round-trip with `requote_mbox` |
| [`storage-blob-put`](benchmarks/agent/PROMPTS.md#storage-blob-put) | implement `BlobStore.put` |
| [`parser-date`](benchmarks/agent/PROMPTS.md#parser-date) | implement `_date`, an email date parser |
| [`mbox-scan`](benchmarks/agent/PROMPTS.md#mbox-scan) | implement `scan`, which walks an mbox file |

**Script tasks.** The agent starts in an **empty directory** and must produce a
working command-line program — the right filename, reading `argv`, printing to
stdout. Trivial logic, real boilerplate, and no repository to navigate.

| task | what the agent is asked to do |
|---|---|
| [`script-reverse`](benchmarks/agent/PROMPTS.md#script-reverse) | write `reverse.py`: take a string, print it reversed |
| [`script-transform`](benchmarks/agent/PROMPTS.md#script-transform) | write `transform.py`: `--input` plus `--reverse`, `--sort` and `--sha256`, applied in a fixed order whatever order the flags arrive in |

**The exact prompt for every task is published** in
[`benchmarks/agent/PROMPTS.md`](benchmarks/agent/PROMPTS.md), generated from the
file the harness actually reads, with a test that fails if the two drift. If a
number here looks surprising, read the prompt that produced it.

**Why both kinds.** The script tasks have almost no variance (1.0–2.1x between
the best and worst run of the same task) because there is no codebase to get
lost in, which makes them the fair way to compare stacks. The excision tasks are
noisier but closer to real work. A stack that does well on one and badly on the
other is telling you something.

## Measured results

<!-- BEGIN GENERATED -->

*Generated from `results.jsonl` — 1304 rows, sha256 979680d991d1.*

#### Every stack measured under OpenCode

| stack | passed | median | worst | spread |
|---|---|---|---|---|
| ornith15 | 21/21 | 44s | 93s | 5.9x |
| qwen38fnds4mtp7shim | 25/45 | 84s | 638s | 77.8x |
| Qwen3.8-Flash-Next Q3 - llama.cpp | 30/30 | 90s | 208s | 4.8x |
| DeepSeek-V4-Flash - ds4 (Anthropic wire) | 18/18 | 110s | 221s | 4.3x |
| qwen38fnq3reap | 21/21 | 110s | 261s | 6.8x |
| DeepSeek-V4-Flash - ds4 | 30/30 | 115s | 230s | 4.3x |
| Qwen3.8-Flash-Next Q3 - LM Studio | 21/21 | 122s | 261s | 4.2x |
| qwen38fnds4shim | 78/90 | 133s | 792s | 123.7x |
| gemma426 | 11/11 | 150s | 160s | 1.7x |
| Qwen3.6-27B-coding - Ollama | 24/24 | 167s | 700s | 12.6x |
| qwen36 | 11/12 | 173s | 565s | 5.7x |
| qwen | 12/12 | 247s | 406s | 4.0x |
| GLM-5.3-Flash - ds4 | 22/24 | 368s | 1227s | 18.0x |
| gemma4 | 12/12 | 383s | 1316s | 4.8x |

Excision tasks only; `script-*` excluded because they are a different class. **Spread is worst / best on the same task**, and it is the column most people forget to ask for.

#### Same weights, two engines

| task | what it asks for | llama.cpp | LM Studio |
|---|---|---|---|
| [`mbox-scan`](benchmarks/agent/PROMPTS.md#mbox-scan) | implement `scan`, which walks an mbox file | 108s | 140s |
| [`mbox-strip-envelope`](benchmarks/agent/PROMPTS.md#mbox-strip-envelope) | implement `strip_envelope` in an mbox parser | 50s | 94s |
| [`parser-date`](benchmarks/agent/PROMPTS.md#parser-date) | implement `_date`, an email date parser | 164s | 238s |
| [`parser-mbox-quoting`](benchmarks/agent/PROMPTS.md#parser-mbox-quoting) | implement `unquote_mbox`, round-tripping with `requote_mbox` | 70s | 93s |
| [`script-reverse`](benchmarks/agent/PROMPTS.md#script-reverse) | write `reverse.py` from nothing: read argv, print reversed | 41s | 57s |
| [`script-transform`](benchmarks/agent/PROMPTS.md#script-transform) | write `transform.py`: `--input` plus three composable flags | 39s | 70s |
| [`storage-blob-put`](benchmarks/agent/PROMPTS.md#storage-blob-put) | implement `BlobStore.put` | 89s | 124s |

#### How fast each stack actually serves tokens

| stack | seconds per 1k output tokens |
|---|---|
| ornith15 | 21s |
| gemma426 | 21s |
| qwen | 31s |
| qwen38fnq3reap | 38s |
| Qwen3.8-Flash-Next Q3 - llama.cpp | 43s |
| qwen36 | 50s |
| DeepSeek-V4-Flash - ds4 (Anthropic wire) | 54s |
| GLM-5.3-Flash - ds4 | 55s |
| Qwen3.6-27B-coding - Ollama | 69s |
| DeepSeek-V4-Flash - ds4 | 71s |
| qwen38fnds4mtp7shim | 74s |
| qwen38fnds4shim | 74s |
| gemma4 | 84s |
| Qwen3.8-Flash-Next Q3 - LM Studio | 115s |

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

### Qwen3.8-Flash-Next on llama.cpp — the fast one

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

### DeepSeek-V4-Flash on ds4 — the second lineage

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

### Qwen3.6-27B-coding on Ollama — see the top of this file

---

## What we are not recommending, and why

**LM Studio.** It works — 18/18, same weights as stack 1 — and its GUI is the
easiest way to get a model running. But it served the *identical* file at
**134 seconds per 1,000 tokens against llama.cpp's 42**, and lost on five of six
tasks. If you want a GUI, use it; if you want the machine's speed, do not.

We also stopped *testing* it on 2026-09-01. Its runtime is llama.cpp
underneath, so on the same GGUF it cannot win — it can only add a layer, and
the measurement above is that layer. The numbers here stand; they are simply
not going to be re-taken.

**GLM-5.3-Flash.** 16/18 and genuinely fast per token, but an 18x spread on one
task — its three runs took 99 s, 378 s and 1,227 s. Excellent model,
unpredictable to plan around. Revisit it.

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
  differ by roughly 56% before the difference is real. Qwen3.8-Flash-Next
  (90s) and DeepSeek-V4-Flash (115s) are **not** reliably distinguishable.
  Qwen3.6-27B-coding (167s) is.
- A perfect run of 21/21 supports "above 85%" at 95% confidence, not "100%".
  Nothing here has run the ~35 consecutive trials a >90% claim needs.
- Every stack was measured on **one machine**, on **one repository**, on six
  tasks. Your code is not our code.

**What we are confident about**: all three stacks work, none of them is a trap,
and **the client you drive them with matters more than the model you pick** —
see below.

## The client matters more than the model

Three clients, same server, same model, same session, same task, interleaved so
none of them got a warmer server. `script-transform` on Qwen3.8-Flash-Next Q3:

| client | median | slowest run | prompt sent | turns |
|---|---|---|---|---|
| **Aider** | **11.1s** | 11.9s | **737 tokens** | 1 |
| **OpenCode** | 39.5s | 55.3s | 11,721 tokens | 5 |
| **Claude Code** | 189.6s | 339.4s | **85,413 tokens** | 3 |

Aider used **6%** of Claude Code's time. All nine runs produced correct output.

**The cause is how much prompt the client sends.** This task starts in an empty
directory and writes one file — there is no repository to read. Claude Code
still sends 85,000 tokens of its own scaffolding, and the server prefills that
on every turn. Output volume does not explain the gap: Claude Code wrote 1,524
tokens against Aider's 395, under four times as many, for seventeen times the
clock.

This is why the gap grows with model size. Prefill cost scales with the model,
so an oversized prompt is nearly free on a 31 GB model and expensive on a 90 GB
one.

### So should you use Aider?

**For a self-contained script, yes — it is dramatically cheaper.** For changing
code inside an existing repository, no:

| client | one-file script tasks | tasks inside a repository |
|---|---|---|
| OpenCode | 15/15 | **91/93** |
| Aider | 15/15 | **22/34** |

Aider's speed comes partly from doing less — one turn, no exploration. That is
exactly right for "write me this script" and not enough for "find where this
behaviour lives and change it". **The recommendation stays OpenCode**, with
Aider worth reaching for on small self-contained jobs.

**Claude Code is the reference point, not a recommendation here.** It is
proprietary, so it cannot be part of a fallback that survives a vendor, and on
these measurements it is both the slowest and the least consistent (152.5s,
339.4s, 189.6s on the same task).

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
