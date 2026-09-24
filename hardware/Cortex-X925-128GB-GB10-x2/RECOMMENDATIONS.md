# What to run on the dual DGX Spark cluster

> **Provisional, and based on limited test runs.** Every figure below comes from
> **one run per stack**: 10 excision tasks × 3 trials (Vision-Exp: × 4), on one
> day (2026-09-22/23), from one client (a Core i3-7100 in a pinned OpenCode
> container). The repo's own rule is three datapoints before a claim. A 3-trial
> median carries ±28%, so two medians need about a 56% gap before the
> difference is real. Most gaps below are smaller than that. The task set is
> at its ceiling for most of these models (every stack except MiMo with
> thinking off passed every trial), so this ranks **speed to a green suite** and says little about
> problem-solving. Code quality is not measured: "passes the suite" is the
> whole claim. Aggregate throughput across concurrent clients was **not
> measured**, because there is one client machine. **Ledger last read
> 2026-09-23.** Rows can move this ranking. Re-read the ledger before relying
> on it.

The cluster is two GB10 DGX Sparks (2 × 128 GB unified), joined by 200 Gb/s
ConnectX-7 RoCE, serving a coding agent on another machine over the LAN. Every
stack here is **tensor-parallel across both nodes** (TP=2) and lands in
[`results.jsonl`](results.jsonl). A single-node run belongs to the single
Spark, which has its own picks in
[`../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md`](../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md).
Operating the pair: [`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md).

## 1. The ranking, provisional

Read the **passed** column first. Timing counts only trials that passed. From
`docs/results.md`, generated from this ledger:

| rank | model | engine and recipe | weights | passed | median | worst | s / 1k output tokens |
|---|---|---|---|---|---|---|---|
| 1 | **GLM-5.3-Flash** EXL3 TR3-4bpw | vLLM + EXL3, [MiaAI-Lab recipe](https://github.com/MiaAI-Lab/GLM-5.3-Flash-2x-DGX-Sparks) @c1b7d4c, DFlash2 (7 tokens), fp8 KV | 164 GiB | 30/30 | **49 s** | 123 s | 85 |
| 2 | **DeepSeek V4.1 Flash** EXL3 2.9 bpw + Engram | vLLM + ExLlamaV3, MiaAI-Lab recipe | 385 GiB on disk | 30/30 | 75 s | 214 s | 68 |
| 3 | **DeepSeek-V4-Flash-Vision-Exp** (official) | vLLM, `ghcr.io/anemll/dspark-vllm-gx10:0.1.1` by digest, MTP 6, 1M context | 156 GiB | 40/40 | 82 s | 187 s | **30** |
| 4 | **Qwen3.8-Flash-Next** NVFP4 (nvidia) | vLLM TP=2 + EP, MTP 3, published two-node flags | 124 GiB | 30/30 | 86 s | 412 s | 76 |
| 5 | **MiMo-V2.6-Flash-RL**, thinking on | SGLang TP=2 EP=2, MiaAI-Lab recipe, DFlash | 172 GiB | 30/30 | 211 s | 796 s | 44 |
| — | MiMo-V2.6-Flash-RL, thinking off | same, with `enable_thinking=false` server-side | same | **23/28** | 86 s | **862 s** | 38 |

**What the data supports:**

- **Run GLM-5.3-Flash first.** It has the lowest median and the lowest worst
  case, and it passed every trial. Its rerun with the client fixed
  (`glm53fexl3dual2xrcctx`) is the row above. The first run
  (`glm53fexl3dual2xrc`, 62 s median) used the same server. Launch it with
  reasoning effort `low` server-side (gotcha 23). Head `MemAvailable` sits at
  2.7–3.3 GiB at idle (1.67 GiB once on kernel 7.0, #700), so it needs
  earlyoom at the #700 line, not 5%.
- **DeepSeek V4.1 is a sound second.** It took 153% of GLM's median time
  (75 s against 49 s). That gap is short of the 56% that three trials can
  resolve, so the two are **not separated**. It also costs the most disk:
  385 GiB, because the Engram tables are a second model-sized download.
- **Vision-Exp decodes fastest, but that is not the fastest agent.** 30 s per
  1k output tokens is the fastest by far. Its task median (82 s) is still
  behind GLM, because agent wall time is mostly re-prefill and context
  handling, not decode. It is the pick when the model must **see images** or
  hold **1M tokens** of context.
- **Qwen3.8-Flash-Next gains nothing from the second node here.** Two-node
  median: 99 s without the published flags, 86 s with them. The same weights
  single-node, from the same client image, read 97 s
  (`qwen38fnnvidianvfp4nothinkfmrcdgx @ Corei3-7100-16GB+image`, 90/90). Those
  rows differ in more than the node count (thinking off by template versus
  reasoning effort low, and different engine images). So this is not a
  controlled A/B. But no row shows two nodes helping. It fits on one Spark,
  so run it there.
- **MiMo: run it with thinking on, or not at all.** It is the only model here
  that **cannot run on one node** (161 GiB of weights against 121.7 GiB).
  - **Thinking off is not recommended.** It passed 23 of 28, and its worst
    trial took 862 s. At least 9 of its trials (10 as first reported on #672)
    had a step that generated exactly 16,384 tokens, the client's output cap,
    and every failure was among them.
  - **Thinking on fixes the runaways**: 30/30 passed, and no step came near
    the cap (the largest was 5,231 tokens). The pass rates overlap at 95%, so
    the runaway count is the evidence.
  - **But it is the slowest stack here.** Its 211 s median took 431% of GLM's
    time (49 s). Use it when a task needs this model specifically, not as the
    default.

## 2. What this means for the #697 deletions

The weights question is "which ones does this ranking still need?" The
operator's standing rule still applies: **weights are an archive**, and
nothing is deleted without an explicit decision. Sizes and paths are from the
[#697 audit](https://github.com/evanwtf/local-llm/issues/697), 2026-09-22.

**Keep: the cluster needs these.**

| weights | where | GiB | why |
|---|---|---|---|
| GLM-5.3-Flash EXL3 TR3-4bpw | head + worker hub | 163.6 each | rank 1 |
| DeepSeek V4.1 Flash EXL3 + Engram | head (worker reads over NFS) | 385.3 | rank 2 |
| DeepSeek-V4-Flash-Vision-Exp | head + worker hub | 156.3 each | rank 3, and the only vision / 1M-context option |
| GLM DFlash2 draft (incoai) | head + worker hub | 2.2 each | GLM's speculative decoding |
| MiMo-V2.6-Flash-RL | head (worker reads over NFS) | 172.5 | rank 5, and the only model that needs both nodes. **It is the first of these to let go if space is needed**: it is the slowest stack, and the weights are first-party (XiaomiMiMo) and can be downloaded again |

**Can go, going by this ranking** (these were tier B in the audit):

| weights | GiB | why the ranking does not need it |
|---|---|---|
| GLM-5.3-Flash EXL3-K2 (vcruz305) | 91.0 | superseded by TR3-4bpw, the rank-1 stack. It was last used single-node, 09-21 |
| Qwen3.8-FN NVFP4 (Mia-AiLab) + its PLE cache | 125.4 | superseded by nvidia's first-party NVFP4, which stays |
| MiniMax-H3 | 134.1 | never benchmarked on either machine. First-party, re-downloadable |

**Worker only: the nvidia Qwen3.8-FN NVFP4 copy (123.6 GiB).** Two nodes
showed no gain for this model, so the worker copy only serves a two-node Qwen
arm the ranking does not recommend. The head copy stays: the single Spark
still uses it. The worker is at 20% used, so this frees space nobody needs
yet. **Leave it unless the worker fills.**

**Not this file's call.** Nemotron-3-Super, Nemotron-3.5-Lightning,
gpt-oss-20b, both Qwen3.6-27B copies, the Qwen3.8-FN GGUFs, and the 27B
recipe caches are single-node weights. Whether they go is a question for the
single Spark's picks, not this ranking. None of them has been run across two
nodes. The single Spark's first pick, Qwen3.6-35B-A3B-NVFP4, and its
llama.cpp Q3 row **must stay**.

**Tier A is unaffected** (the byte-identical Q4_K_XL duplicate, the aborted
download, caches, the superseded GLM build image on the worker). Nothing in
this ranking uses those.

## 3. What would make this a recommendation rather than a ranking

1. **Three runs per stack**, relaunching between runs, for the top three.
   Today each has one.
2. **Harder tasks.** Five stacks passed every trial, so pass rate cannot
   separate them.
3. **The #680 audit's HIGH findings closed.** The SGLang rows record
   `sglang_version=unknown` and no server argv, and worker-node facts are
   thin.
4. **Concurrency**, if a second client machine becomes available. Aggregate
   throughput across streams is what the pair is for, and it is unmeasured.
