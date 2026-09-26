# What to run on the dual DGX Spark cluster

> **Provisional.** Rounds done so far, per stack (a round is one run of a task
> set on its own server launch):
>
> | stack | standard-set rounds | replay rounds (#714) |
> |---|---|---|
> | GLM-5.3-Flash EXL3 | **3** | 1 |
> | DeepSeek V4.1 Flash EXL3 | 2 | 1 |
> | DeepSeek-V4-Flash-Vision-Exp | 2 | 1 |
> | Qwen3.8-Flash-Next NVFP4 | 3 (2 with the #664 flags) | 1 |
> | Qwen3.8-Flash-Next FP8 | 1 | 1 |
> | MiMo-V2.6-Flash, thinking on | 2 | 1 |
>
> The repo's rule is three datapoints before a claim; only GLM's standard set
> meets it. GLM was the fastest stack in every round it ran and passed every
> trial, so the pick is unlikely to move. The four stacks behind it sit within
> about 30% of each other, which one round cannot order. Rounds 2 and 3 on
> replay and the #726 hard set are running under #717, and this file is
> updated as they land. "Passes the suite" is the whole quality claim.
> Aggregate throughput across concurrent clients is **not measured** (one
> client machine). **Ledger last read 2026-09-25.**

The cluster is two GB10 DGX Sparks (2 × 128 GB unified), joined by 200 Gb/s
ConnectX-7 RoCE, serving a coding agent (OpenCode) on another machine over the
LAN. Every stack here is **tensor-parallel across both nodes** and lands in
[`results.jsonl`](results.jsonl). A single-node run belongs to the single Spark,
which has its own picks in
[`../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md`](../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md).
Operating the pair: [`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md).

## 1. The pick: GLM-5.3-Flash EXL3

Run **GLM-5.3-Flash EXL3 TR3-4bpw** on vLLM across both Sparks
([MiaAI-Lab recipe](https://github.com/MiaAI-Lab/GLM-5.3-Flash-2x-DGX-Sparks)),
with reasoning effort `low` server-side.

- **It passed every trial it ran: 111 of 111.** Three standard-set runs (30/30
  each) and one replay run (21/21).
- **It was the fastest stack in every run.** Standard-set medians of 62 s, 49 s
  and 48 s (2026-09-22, 09-23, 09-24). Its *worst* run's median (62 s) is below
  every other stack's *best* (75 s).
- **On the replay tasks** (#714: rebuild a real 60–600-line feature commit from
  its tests), it took 1,856.9 s summed over the seven per-task medians, against
  2,854.2–3,823.1 s for the next four. It was fastest on six of seven tasks and
  level on the seventh.

## 2. Every stack measured

**Standard set** (10 excision tasks × 3 trials; timing is excision tasks only,
passed trials only). One row per run:

| stack | runs: passed, median, worst |
|---|---|
| **GLM-5.3-Flash EXL3** (`glm53fexl3dual2xrc*`) | 30/30, 62 s, 130 s · 30/30, 49 s, 123 s · 30/30, 48 s, 96 s |
| DeepSeek V4.1 Flash EXL3 (`dsv41fexl3dual2xrc`) | 30/30, 75 s, 214 s · 30/30, 87 s, 232 s |
| DeepSeek-V4-Flash-Vision-Exp (`dsv4flashvisiondspark2xrc`) | 40/40, 82 s, 187 s · 30/30, 91 s, 202 s |
| Qwen3.8-Flash-Next NVFP4 (`qwen38fnnvfp4dual2xrc*`) | 30/30, 99 s, 178 s · 30/30, 86 s, 412 s · 30/30, 114 s, 304 s |
| Qwen3.8-Flash-Next FP8, official (`qwen38fnfp8dual2xrc`) | 30/30, 93 s, 278 s |
| MiMo-V2.6-Flash, thinking on (`mimo26fdual2xrcthink`) | 30/30, 211 s, 796 s · 30/30, 310 s, 749 s |

**Replay tasks** (#714; 7 tasks × 3 trials, one run each, 2026-09-24/25, OpenCode 1.18.32):

| stack | passed | sum of per-task medians | median | worst |
|---|---|---|---|---|
| **GLM-5.3-Flash EXL3** | 21/21 | **1,856.9 s** | 216 s | 556 s |
| DeepSeek-V4-Flash-Vision-Exp | 21/21 | 2,854.2 s | 453 s | 740 s |
| Qwen3.8-Flash-Next NVFP4 | 21/21 | 3,036.8 s | 430 s | 1,006 s |
| DeepSeek V4.1 Flash EXL3 | 21/21 | 3,207.7 s | 410 s | 998 s |
| Qwen3.8-Flash-Next FP8 | 21/21 | 3,823.1 s | 502 s | 1,152 s |
| MiMo-V2.6-Flash, thinking on | **11/19**, stopped early | — | — | 8 failures: 6 timeouts at 1,800 s, 2 collection errors |

21/21 has a Wilson 95% interval of 84.5–100%, so the five 21/21 stacks are not
separated on pass rate. None of the 105 trials in those rows restored the
original commit byte for byte (the target repo is public, so recall was
checked).

**What the rest of the data supports:**

- **DSV4-Flash-Vision is the second choice, and the one to run when the task
  needs images or 1M tokens of context.** It decodes fastest by far (29–30 s per
  1k output tokens, against 63–88 for GLM) and had the tightest spread of any
  stack on replay. Its task times still trail GLM, because agent wall time is
  mostly re-prefill and context handling, not decode.
- **V4.1, NVFP4 Flash-Next and FP8 Flash-Next are not separated from DSV4-Vision
  or from each other.** Their standard-set medians (75–114 s) and replay sums
  (2,854–3,823 s) overlap within the spread one run carries. V4.1 costs the most
  disk: 385 GiB with its Engram tables.
- **The official FP8 Flash-Next bought nothing over NVFP4 here.** Same model,
  same pass rate (21/21 each), and it took 126% of NVFP4's replay time
  (3,823.1 s against 3,036.8 s). NVFP4 also fits one Spark; FP8 does not.
- **Do not run MiMo-V2.6-Flash as a coding backend.** It is the slowest stack on
  every task, and the only one to fail replay tasks: web-auth and
  search-operators timed out, gmail-api-sources errored, and on round 3 the
  smallest task (121–192 s in rounds 1–2) ran to the 1,800 s limit. The run
  was stopped at 19 of 21 trials under the #762 early-stop rule (7 failures on a
  21-trial set). Thinking off is worse (23/28 on the standard set, with runaway
  16,384-token steps).

## 3. How to run the pick

On the cluster's head, from a checkout of the MiaAI-Lab GLM recipe (tested at
`0f49cfd`), with `.env` set to:

- `GLM53_DEFAULT_REASONING_EFFORT=low`. The template otherwise runs at effort
  Max and truncates answers ([gotcha 23](../../docs/dgx-cluster-setup.md#23-a-large-models-default-reasoning-effort-eats-the-whole-token-budget--hit)).
  Check that `default-chat-template-kwargs {"reasoning_effort":"low"}` is in the
  server's argv.
- All four fabric interfaces and HCAs in `HEAD_CX7_IF` / `WORKER_CX7_IF` /
  `*_CX7_IB`.
- `MAX_MODEL_LEN=262144`, `GPU_MEM_UTIL=0.86`, and the recipe's explicit KV pool
  (`--kv-cache-memory-bytes 15032385536`).

Then `SKIP_BUILD=1 ./start.sh`. That uses the published GHCR image: a local
rebuild after the 2026-09-24 recipe update failed at weight load (InstantTensor
buffer over the device budget). The API comes up on `:8888` about 8–9 minutes
after launch with the weights cached on both nodes. Point OpenCode at model
`GLM-5.3-Flash-EXL3` with a 262,144-token context.

Memory: the head idles at 3.6–4.1 GiB `MemAvailable` with it loaded, and the
recipe has no memory guard of its own. Keep `earlyoom` at the #700 line
(1.0 / 0.5 GiB) on both nodes.

## 4. Weights on disk (#697)

The operator ran the #697 prune on 2026-09-24 (tiers 1 and 3, keeping
Nemotron-3-Super), and the head went from 742 to 1,267 GB free. Every model in
section 2 stays. Tier 2 (MiniMax-H3, GLM EXL3-K2, the Mia-AiLab Flash-Next NVFP4
build and its image, about 401 GiB) is still undecided. Going by this ranking,
**MiMo-V2.6-Flash-RL (172.5 GiB, first-party, re-downloadable) is now the first
of the cluster's models to let go** if space is needed.

## 5. Still running

- **Rounds 2 and 3** for GLM, DSV4-Vision, NVFP4 Flash-Next and V4.1 (#717). #3
  and #4 could not be separated, so both go forward.
- **The #726 hard set** on the leaders: held-out tests, and a 1,643-line
  five-commit span task. It is the one place pass rates can still separate the
  stacks behind GLM.
- **Ling-3.0-flash** (#752), a model family not yet measured here: official FP8
  across both Sparks.
- **Not measured at all:** concurrent-client throughput, and the #680 audit's
  gaps (SGLang rows record no server argv).
