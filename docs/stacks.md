# Running each stack by hand

**Split out of `RECOMMENDATIONS.md` on 2026-09-08 ([#232](https://github.com/evanwtf/local-llm/issues/232)).**
That file is the answer to "what do I run"; this one is the answer to "how,
without the wrapper". Nothing here was rewritten in the move — the ranking
discussion, the full per-stack instructions, the stacks we decline to
recommend and the two ds4 disk-KV ceilings, in the order they stood.

Start at [`RECOMMENDATIONS.md`](../RECOMMENDATIONS.md). Come here when you
want to run a server yourself, or want the reasoning behind the ranking.

---


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
have run. (That ranking was written on 2026-09-01, when every OpenCode row was
1.18.25; three backends have since been measured under a later client, so read
it against the caveat under that table — [#137](https://github.com/evanwtf/local-llm/issues/137).) It is still not the one to install, for two reasons that the median
hides. It is **the only backend in this project's whole record that has
produced wrong code**: it failed twice on an excision task under an earlier
client, and it emitted Swift that did not compile from a run that otherwise
looked completely normal — clean exit, no error, 30 tool calls
([#45](https://github.com/evanwtf/local-llm/issues/45)). Its tail is also the
longest here, though smaller than this file used to claim: **93s worst against
a 44s median under OpenCode (2.1x)**, and 999.6s against 96s pooled across
every client (10.4x). The figure published until 2026-09-07 was "30x its
median", which no cut of the data supports — it came from dividing the worst
Codex run by the OpenCode median, the cross-client mix this file warns about
two tables down ([#137](https://github.com/evanwtf/local-llm/issues/137)).
Fastest-on-average and
occasionally, quietly wrong is a bad trade when you are not watching. The
numbers are published because they are real; the recommendation withholds it on
purpose.

**And the row that used to sit second is worse than the table once said.**
`qwen38fnds4mtp7shim` is the `qwen38fnds4shim` stack with ds4's MTP flags
added and nothing else changed: same weights, same engine, same shim, same
fifteen tasks. It passes fewer of them. A controlled A/B on 2026-09-07 --
four sweeps, arms alternated per pair, the server restarted between them, the
harness head pinned -- put it at **21/30 against 28/30**
([#39](https://github.com/evanwtf/local-llm/issues/39)).

**Do not read that as the cost of speculative decoding, because no draft was
ever completed.** The arm's own server loaded the MTP head, reported it
`state=ready draft=7`, drafted on its start-up prompts at 47-61% acceptance,
and then emitted **not one MTP timing line** across the thirty agent trials.
Not zero acceptance, and not the `verifier=scheduler-bypass` a turned-away
cycle prints: no line at all.

**The reason is temperature, and it was found on 2026-09-08.** ds4 reaches
the Qwen MTP path only when `temperature <= 0.0f`
(`ds4.c:80120 at ds4-metal ba01f5d`). Above zero a Qwen session is neither GLM
nor DSpark, so the speculative call does one plain eval and returns
(`ds4.c:80216 at ds4-metal ba01f5d`). A request that omits the field gets
`DS4_DEFAULT_TEMPERATURE`, which is `1.0f`
(`ds4.h:56 at ds4-metal ba01f5d`, `ds4_server.c:12734 at ds4-metal ba01f5d`),
and **OpenCode sends no temperature**. So the flag is passed, the sidecar
loads, and the speculative code never runs.

Three replay rounds against the captured OpenCode payload, eight arms each,
agreed to the cycle: only the arm with `temperature: 0` forced ever
speculated — 52 cycles each time — against zero for the payload verbatim,
without tools, without the system prompt, with one tool, with `max_tokens`
cut, and with `stream_options` removed
([#151](https://github.com/evanwtf/local-llm/issues/151)).

**Two earlier readings on this page were wrong and are withdrawn.** The
`Qwen MTP history frontier short` aborts were described here as speculation
entered and abandoned; the line is printed from `qwen4_graph_forward`
(`ds4.c:56314 at ds4-metal ba01f5d`, `ds4.c:56588 at ds4-metal ba01f5d`) — the
ordinary forward pass on an MTP-enabled graph — as well as from the
speculative implementation (`ds4.c:79189 at ds4-metal ba01f5d`), so it is not
evidence of either. And the tool-free/tool-bearing separation was attributed
here to prompt **size**: an 11,000-token prompt speculates normally at
temperature 0, so size is not the cause either. Both readings came from
correct logs and an inference that did not follow.

**And the pass gap does not resolve at this size.** Thirty rows against thirty
are fifteen tasks run twice per arm, not sixty independent trials -- a task
the arm cannot do at all contributes two failures, and a pooled count reads
the second as fresh evidence. Paired by task the split is 6 down, 1 up, 8
tied; a two-sided sign test gives **p=0.125**. The direction has been the same
at every look, which is a reason to run it again and not a reason to publish a
number.

Until 2026-09-06 it ranked **second of fifteen at an 84s median**, above every
stack that passed all of its trials, because the timing columns counted failed
trials. A trial that dies early is quick, so a 45% failure rate pulled the
median down and lifted the row up a list sorted by median — two effects
compounding on the one column a reader scans for "which is quickest"
([#142](https://github.com/evanwtf/local-llm/issues/142)). The timings now
count only trials that passed, which puts this row **twelfth at 177s** and left
every stack that passes everything on the number it already had. Read the
`passed` column first, always.

None of this says speculative decoding loses work. On this machine it has
never been measured doing any: through a coding agent no draft has ever
completed.
What the MTP flags do change is still open -- they allocate a different graph
and, as we have configured them, a different disk KV directory, and either
could carry the pass difference
([#142](https://github.com/evanwtf/local-llm/issues/142),
[#39](https://github.com/evanwtf/local-llm/issues/39),
[#190](https://github.com/evanwtf/local-llm/issues/190)). What it does say is
**do not install this one**: it adds a head that never fires and a pass rate
that has been worse every time it has been looked at.

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

### Qwen3.8-Flash-Next on ds4 — the fast one

**This is a fork, and that is the whole caveat.** The build lives on
ivanfioravanti's `qwen3.8-flash-next` branch, not mainline ds4;
`antirez/ds4#991` is still open. The fork's own `download_model.sh` carries the
comment *"will move to the antirez org — flip this one line then"*, so the
authors expect it to land. Until it does, this stack is one force-push from
needing attention. If that is not a risk you want, run the llama.cpp stack
below instead and give up 16%.

```sh
git clone https://github.com/ivanfioravanti/ds4.git ~/git/ds4-ivan-qwen38fn
cd ~/git/ds4-ivan-qwen38fn
git checkout qwen3.8-flash-next
make
```

The 45 rows behind the table were measured at `ffd85d42`. The branch moves
daily and is force-pushed, so record the commit you built — a bare file:line
against this tree is unverifiable a week later.

**The force-push risk is no longer hypothetical, and the outcome splits.**
Measured 2026-09-08 ([#228](https://github.com/evanwtf/local-llm/issues/228)):
`ivanfioravanti/ds4-metal` moved 149 commits ahead of the `ba01f5d` this
project had been building, and `ba01f5d` is **not an ancestor** of the new
head. Which of our two ds4 stacks survives that depends on one string in the
GGUF, `general.architecture`:

| stack | architecture | `ba01f5d` | `18ca8ec` (new head) |
|---|---|---|---|
| `Q4_K imatrix` — the row in the table above | `qwen4exp` | refused | **loads** |
| `Q4_0` fast-pack — the withdrawn shim build | `qwen4-exp` | loads | **refused** |

Each build refuses the other's file with the same error, `ds4: required
metadata key is missing: deepseek4.block_count`, because the loader takes
exactly one architecture string and validation falls through to DeepSeek when
it does not match.

**For the stack recommended here this is good news, and worth taking.** On the
new head, three 4-rep runs put it **+6.2% decode and +7.7% prefill** over the
`ffd85d42` these numbers were measured on, with 0 of 8 frontiers against it in
any run, and output **bit-exact** — identical selected tokens and top-20
logits over 128 steps at 2047 and 16380 prompt tokens. Nothing about quality
moves; it is faster and the same model.

**For the Q4_0 build it is the end of the line.** That file cannot follow the
head without a re-quant or a re-declared architecture string. It was already
withdrawn by its author (see below); this makes the withdrawal permanent
rather than merely inconvenient.

So the durability risk [#141](https://github.com/evanwtf/local-llm/issues/141)
raises is real and has now fired once — but it fired in the direction of the
stack this file recommends, not against it. **Record the commit you build, and
re-check that your weight file's `general.architecture` matches the loader
before assuming an upgrade is free.**

Weights come from `ivanfioravanti/Qwen3.8-Flash-Next-DS4-Q4` on Hugging Face.
Two files are needed and they total ~105 GB (98 GiB): the experts, and a
**PLE sidecar** which is a further 32 GB and is easy to miss.

```sh
export KIM=~/models/qwen3.8-flash-next-ds4-q4k-imatrix
# Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf
# Qwen3.8-Flash-Next-PLE-Q4_1.gguf
```

Serve it, then put the tool-format shim in front. **Both halves are required** —
OpenCode talks to the shim, not to ds4:

```sh
cd ~/git/ds4-ivan-qwen38fn
./ds4-server --metal \
  -m "$KIM/Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf" \
  --ple "$KIM/Qwen3.8-Flash-Next-PLE-Q4_1.gguf" \
  --ctx 100000 --warm-weights \
  --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192 \
  --host 127.0.0.1 --port 8000

uv run python ds4_qwen_tool_shim.py --upstream http://127.0.0.1:8000 --port 8101
```

OpenCode then points at `ds4qwenshim/qwen3.8-flash-next-q4`. The server plans
**79.7 GiB resident** at `ctx=100000` (68.3 GiB model + 8.4 GiB buffers +
2.9 GiB KV), which fits the 112 GiB ceiling with room to spare.

**Leave MTP off**, and know that with these flags alone it was never on.
The sidecar loads and reports `state=ready draft=7`, but ds4 reaches the Qwen
MTP path only at `temperature <= 0.0f` (`ds4.c:80120 at ds4-metal ba01f5d`)
and OpenCode sends no temperature, so the default `1.0f`
(`ds4.h:56 at ds4-metal ba01f5d`) applies and no draft is ever attempted
([#151](https://github.com/evanwtf/local-llm/issues/151)). Adding the flags
buys a heavier server — a second graph, 359.86 MiB of state capture and its
own `--kv-disk-dir` — and no speculation.

[#39](https://github.com/evanwtf/local-llm/issues/39) measured the arm losing
pass rate and error recovery. **Do not read that as the cost of speculative
decoding**; whatever it costs, no draft completed. Turning MTP into a real
treatment means pinning `temperature: 0` on the client, which is its own
change to the regime and has not been measured here.

### Qwen3.8-Flash-Next on llama.cpp — the mainline fallback

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

## If you run ds4 with its disk KV cache, two ceilings decide whether it does anything

Measured 2026-09-07 on `qwen38fnds4kimat`, three runs per arm, every run
identical to the digit ([#190](https://github.com/evanwtf/local-llm/issues/190),
[#199](https://github.com/evanwtf/local-llm/issues/199)). Both are one flag
wide, and at ds4's defaults a coding agent gets much less from the cache than
the flags suggest.

**A cold checkpoint stops existing above 30,000 tokens.** `cold_max_tokens`
defaults to 30000. At 53,845 and 77,845 tokens a fresh session reuses **0.0%**
— no store is written at all, so there is nothing to hit later. Raise the cap
and the same prompts read **98.9%** and **97.3%**.

**A continued checkpoint lands only on an exact multiple of 10,240 tokens.**
`ds4_kvstore_continued_store_target` returns 0 unless `live_tokens % step == 0`
(`ds4_kvstore.c:751 at ds4-ivan-qwen38fn ffd85d42`), and the step is
`ceil(10000/2048) × 2048 = 10240`. Prefill advances in 8,192-token chunks, so
from any resume point the next landing is **five chunks — 40,960 tokens —
away**. At 29,845 tokens the default gives **34.3%** reuse; a 2,048 step gives
**89.2%**, storing at exactly 26,624 = 10,240 + 2 × 8,192, which the arithmetic
named before the run produced it.

**Both bite a coding agent at once.** A new session over 30k gets nothing from
disk, and a continuing one must grow by ~41k tokens **in a single turn** to
leave a new checkpoint. Neither shows up as an error; the session is simply
slower than the cache flags imply.

Two other things that look like knobs here and are not. The disk budget is
inert over the range we tested — 8 GiB and 32 GiB produced byte-identical
results — and so is the context size, 32k against 128k. Do not spend time on
either.

One trap if you go looking in the logs. An **evicted** store is written and
never hit: at 29,845 tokens the server writes a 29,877-token entry with
`reason=evict` and then serves the very request that produced it from a
10,240-token entry left by an earlier, much shorter run. So a `kv cache stored`
line is not evidence of reuse, and the store a request reads is not necessarily
the most recent one.

