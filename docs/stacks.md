# Running each M5 Max stack by hand

The M5 Max's [`RECOMMENDATIONS.md`](../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/RECOMMENDATIONS.md)
says what to run, and its [`howto.md`](../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/howto.md)
stands up every pick from a clean Mac. This page holds the reasoning behind
the ranking, the server commands without the wrapper, the stacks we decline to
recommend, and the two ds4 disk-KV ceilings. For which external repositories
each stack clones and where they go, see [`external-repos.md`](external-repos.md).
The two-Spark cluster's stacks are in its own
[`howto.md`](../hardware/Cortex-X925-128GB-GB10-x2/howto.md).

---

## Why the ranking reads the way it does

**Why the slowest one is the one to install first.** Qwen3.6-27B-coding on
Ollama is 31 GB, installs with two commands, and leaves enough memory that you
can keep working while it runs. The faster stacks want most of a 128 GB
machine. A 167 s median against 52 s is a real difference, but it is not what
decides whether you keep using this.

**Why a second lineage is worth 91 GB.** Most of the picks are Qwen models. If
your reason for running locally is that a model might one day be unavailable,
betting on one maintainer rebuilds the problem you were escaping.
DeepSeek-V4-Flash on ds4 is the independent lineage.

**Why we do not rank on median alone.** A median hides how bad the bad runs
get, so read the spread, and read the `passed` column first. Until 2026-09-06
the timing columns counted failed trials, and a trial that dies early is
quick: a 45% failure rate lifted one row to second place (#142). Timings now
count only passed trials.

**Why the smallest stack carries a warning.** `ornith15` (Ornith-1.5-35B-A3B,
22 GB) is fast and small, and it is **the only backend in this project's
record that has produced wrong code**. It emitted Swift that did not compile
from a run that otherwise looked normal: clean exit, no error, 30 tool calls
([#45](https://github.com/evanwtf/local-llm/issues/45)). Pick it for the small
footprint, and read what it writes. An earlier "30x its median" tail figure
divided a Codex run by an OpenCode median; never mix clients in one figure
([#137](https://github.com/evanwtf/local-llm/issues/137)).

**Why `qwen38fnds4mtp7shim` is not on the list.** It is `qwen38fnds4shim` with
ds4's MTP flags added. A controlled A/B put it at **21/30 against 28/30**
([#39](https://github.com/evanwtf/local-llm/issues/39)); paired by task the
split is 6 down, 1 up, 8 tied, a sign test p=0.125, so the gap is not
resolved. **Do not read that as the cost of speculative decoding: no draft
ever completed.** ds4 reaches the Qwen MTP path only at `temperature <= 0.0f`
(`ds4.c:80120 at ds4-metal ba01f5d`). A request that omits the field gets
`DS4_DEFAULT_TEMPERATURE`, `1.0f` (`ds4.h:56 at ds4-metal ba01f5d`), and
OpenCode sends no temperature. Replays of the captured OpenCode payload
speculated only with `temperature: 0` forced
([#151](https://github.com/evanwtf/local-llm/issues/151)). The flags add a
second graph and a different disk KV directory, either of which could carry
the pass difference (#190), and a head that never fires. Do not install it.

---

## Full instructions

### Before anything: the memory ceiling

**Skip this for the Ollama stacks.** For the 84 GB and larger models, macOS
does not let the GPU hold enough memory by default, and the failure looks like
a model that refuses to load for no clear reason.

```sh
sudo sysctl iogpu.wired_limit_mb=114688     # 112 GiB, on a 128 GB Mac
sysctl -n iogpu.wired_limit_mb              # expect 114688
```

This is a **cap, not a reservation**: with it set and nothing loaded, the GPU
holds about 5 GB. It does not survive a reboot on its own;
`scripts/install-metal-ceiling.sh` makes it permanent via a LaunchDaemon.
**A reading of `0` means "system default", not "no limit".** After a reboot,
`0` means your setting did not apply.

### Qwen3.8-Flash-Next on ds4 upstream main — the lighter-engine row

The M5 Max's "lighter engine" row since 2026-09-19
([#158](https://github.com/evanwtf/local-llm/issues/158),
[#524](https://github.com/evanwtf/local-llm/issues/524)). It needs no fork and
no PLE sidecar. In a 60-trial-per-arm A/B it passed 59/60 against the `kimat`
fork's 60/60, with a paired wall ratio of 0.96 (95% CI 0.87–1.06).

```sh
git clone https://github.com/antirez/ds4.git ~/git/ds4
cd ~/git/ds4
make
./download_model.sh qwen38-q4k
```

The rows were measured at `8db1d1d1`. Record the commit you build.

The download is one 165.11 GiB GGUF: 69.74 GiB of main and MTP weights and a
95.37 GiB n-gram table. The server reads the n-gram table from disk on
demand, so keep the file on a fast local SSD. The script puts it in `gguf/` in
the ds4 tree.

Serve it, then put the tool-format shim in front. **Both halves are
required.** OpenCode talks to the shim, not to ds4:

```sh
cd ~/git/ds4
./ds4-server --metal \
  -m gguf/Qwen3.8-Flash-Next-Q4.gguf \
  --ctx 100000 --warm-weights \
  --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192 \
  --host 127.0.0.1 --port 8000

uv run python ds4_qwen_tool_shim.py --upstream http://127.0.0.1:8000 --port 8101
```

OpenCode then points at `ds4qwenshim/qwen3.8-flash-next-q4`. **Leave MTP
off**, for the reason above.

### Qwen3.8-Flash-Next on ds4's `kimat` fork — the earlier row

The M5 Max row moved off this stack on 2026-09-19. The steps stay here for the
rows measured on it. The build is ivanfioravanti's `qwen3.8-flash-next`
branch; its upstream PR, `antirez/ds4#991`, merged on 2026-09-14.

```sh
git clone https://github.com/ivanfioravanti/ds4.git ~/git/ds4-ivan-qwen38fn
cd ~/git/ds4-ivan-qwen38fn
git checkout qwen3.8-flash-next
make
```

The 45 rows were measured at `ffd85d42`. The branch was force-pushed, so
record the commit you build.

**Check `general.architecture` before you assume an upgrade is free.** The
loader takes exactly one architecture string. After a force-push
([#228](https://github.com/evanwtf/local-llm/issues/228)), the new head loads
the `qwen4exp` Q4_K imatrix file and refuses the `qwen4-exp` Q4_0 fast-pack,
and the old head does the reverse. Both fail with `ds4: required metadata key
is missing: deepseek4.block_count`.

Weights come from `ivanfioravanti/Qwen3.8-Flash-Next-DS4-Q4` on Hugging Face:
the experts and a **32 GB PLE sidecar** that is easy to miss, ~105 GB in all.

```sh
export KIM=~/models/qwen3.8-flash-next-ds4-q4k-imatrix
# Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf
# Qwen3.8-Flash-Next-PLE-Q4_1.gguf

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
**79.7 GiB resident** at `ctx=100000`, which fits the 112 GiB ceiling.

### Qwen3.8-Flash-Next on llama.cpp — the mainline engine

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
- **`-c 131072`.** At 65536 the agent thrashes its own context compaction, and
  a task that takes 110 seconds took 842.
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

### The Ollama and mlx-serve stacks

These have no by-hand form beyond the steps in the M5 Max
[`howto.md`](../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/howto.md)
(rows 1 to 3).

---

## What we are not recommending, and why

**LM Studio.** It works, and its GUI is the easiest way to get a model running.
But it served the *identical* file at **134 seconds per 1,000 tokens against
llama.cpp's 42**, and lost on five of six tasks. Its runtime is llama.cpp
underneath, so on the same GGUF it can only add a layer. We stopped testing it
on 2026-09-01.

**GLM-5.3-Flash on ds4.** Fast per token, but unpredictable to plan around:
one task's three runs took 99 s, 378 s and 1,227 s.

**Anything ranked by tokens per second.** This project has measured three
times that decode rate does not predict how long a real task takes. The 3-bit
quant of Qwen3.8-Flash-Next decodes *slower per token* than the 2-bit one and
finishes the suite **28% faster**. Two engines served identical weights with
identical correctness and wall clocks of 80.5 s against 151.7 s on one task.

---

## If you run ds4 with its disk KV cache, two ceilings decide whether it does anything

Measured 2026-09-07 on `qwen38fnds4kimat`, three runs per arm, every run
identical to the digit ([#190](https://github.com/evanwtf/local-llm/issues/190),
[#199](https://github.com/evanwtf/local-llm/issues/199)). Both are one flag
wide, and at ds4's defaults a coding agent gets much less from the cache than
the flags suggest.

**A cold checkpoint stops existing above 30,000 tokens.** `cold_max_tokens`
defaults to 30000. At 53,845 and 77,845 tokens a fresh session reuses **0.0%**:
no store is written at all, so there is nothing to hit later. Raise the cap
and the same prompts read **98.9%** and **97.3%**.

**A continued checkpoint lands only on an exact multiple of 10,240 tokens.**
`ds4_kvstore_continued_store_target` returns 0 unless `live_tokens % step == 0`
(`ds4_kvstore.c:751 at ds4-ivan-qwen38fn ffd85d42`), and the step is
`ceil(10000/2048) × 2048 = 10240`. Prefill advances in 8,192-token chunks, so
from any resume point the next landing is **five chunks, 40,960 tokens,
away**. At 29,845 tokens the default gives **34.3%** reuse; a 2,048 step gives
**89.2%**.

**Both bite a coding agent at once.** A new session over 30k gets nothing from
disk, and a continuing one must grow by ~41k tokens **in a single turn** to
leave a new checkpoint. Neither shows up as an error; the session is simply
slower than the cache flags imply.

The disk budget is not a knob over the range tested (8 GiB and 32 GiB gave
byte-identical results), and neither is the context size (32k against 128k).

**A `kv cache stored` line is not evidence of reuse.** An **evicted** store is
written and never hit: at 29,845 tokens the server writes a 29,877-token entry
with `reason=evict`, then serves the request from a 10,240-token entry left by
an earlier, shorter run. The store a request reads is not necessarily the most
recent one.
