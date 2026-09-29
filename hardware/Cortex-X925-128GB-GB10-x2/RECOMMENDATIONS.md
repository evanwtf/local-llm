# What to run on the dual DGX Spark cluster

> **Ledger last read 2026-09-29, 12:50 EDT.** Testing is paused here by the
> operator. The pick has three or more runs behind it on every task set.
> Since then, one TensorFold run was faster than GLM on both task sets, but on
> different weights and with fewer hidden tests passed (section 3). It does
> not change the pick.
>
> | stack | standard-set runs | replay runs (#714) | hard-set runs (#726) |
> |---|---|---|---|
> | **GLM-5.3-Flash EXL3** | 3 | 3 + 1 on recipe @943912c | 4 |
> | DeepSeek-V4-Flash-Vision-Exp | 3 | 3 | 2 |
> | Qwen3.8-Flash-Next NVFP4 | 3 | 3 + 1 on vLLM 0.30 | 2 |
> | Qwen3.8-Flash-Next hibrid48 (vr8vr8's recipe) | 0 | 1 | 0 |
> | DeepSeek V4.1 Flash EXL3 | 3 | 3 | 1 |
> | GLM-5.3-Flash abliterated EXL3 on TensorFold (jayleaton's recipe) | 0 | 1 | 1 |
>
> "Passes the suite" is the whole quality claim. Aggregate throughput with
> several clients at once is **not measured**, because there is one client
> machine.

The cluster is two GB10 DGX Sparks (2 × 128 GB unified memory), joined by
200 Gb/s ConnectX-7 RoCE. It serves a coding agent (OpenCode 1.18.31–1.18.33)
running on another machine on the LAN. Every stack here is tensor-parallel
across both nodes, and its rows are in [`results.jsonl`](results.jsonl).
Per-run tables are generated into
[`docs/results.md`](../../docs/results.md#cortex-x925-128gb-gb10-x2). A
single-node run belongs to the single Spark, which has its own picks in
[`../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md`](../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md).
For operating the pair, see
[`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md).

## 1. The pick: GLM-5.3-Flash EXL3

Run **GLM-5.3-Flash EXL3 TR3-4bpw** on vLLM across both Sparks
([MiaAI-Lab recipe](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks)),
at the recipe's latest commit, with reasoning effort `low` set on the server.

- **Quality: level with the best on every task set.**
  - It passed all 90 standard-set trials and 63 of 63 replay trials on the
    recipe it ran three times.
  - On the hard set it passed 20 of 21 visible verdicts in three of its four
    runs, the same as the next two stacks' best.
  - Hidden-test pass rates do not separate any of the stacks (section 2).
- **Speed: fastest on every task set, among stacks that match its quality.**
  One TensorFold run on abliterated GLM weights was faster (section 3).

  | task set | GLM | next-fastest stack |
  |---|---|---|
  | standard set, worst of GLM's three runs against the best of any other run | 62 s median | 75 s median |
  | replay, sum of per-task medians | 1,901.1 s | 2,545.7 s (hibrid48, one run) |
  | hard set, median trial, best run | 359.9 s | 682 s |

  So the next-fastest stack took 134% of GLM's replay time.
- **The newest GLM run was its fastest.** On recipe @94ae731 with OpenCode
  1.18.33, the hard set's median trial was 359.9 s and its summed time
  12,744.9 s. The three earlier runs had medians of 491.9–546 s and sums of
  14,978.4–16,566.7 s. That is one run, so it is not yet a result.

## 2. Every stack measured

### Replay tasks (#714)

Rebuild a real commit of 60–600 lines from its tests. 7 tasks × 3 trials per
run.

| stack | runs | passed | sum of per-task medians | median trial | worst trial |
|---|---|---|---|---|---|
| GLM-5.3-Flash abliterated EXL3 on TensorFold, jayleaton's recipe | 1 | 20/21 | **1,261.7 s** | 156.1 s | 469.5 s |
| **GLM-5.3-Flash EXL3**, recipe @0f49cfd | 3 | 63/63 | 1,901.1 s | 216 s | 608 s |
| GLM-5.3-Flash EXL3, recipe @943912c | 1 | 19/21 | 2,018.9 s | 232.9 s | 592.2 s |
| Qwen3.8-Flash-Next hibrid48, vr8vr8's recipe | 1 | 21/21 | 2,545.7 s | 343.8 s | 692.9 s |
| DeepSeek-V4-Flash-Vision-Exp | 3 | 63/63 | 2,757.7 s | 435 s | 923 s |
| Qwen3.8-Flash-Next NVFP4, MiaAI-Lab @d2f54b7 | 3 | 63/63 | 2,954.3 s | 404 s | 1,082 s |
| Qwen3.8-Flash-Next NVFP4, vLLM 0.30 + recipe @d23790b | 1 | 21/21 | 3,157.4 s | 438 s | 1,081 s |
| DeepSeek V4.1 Flash EXL3 | 3 | 63/63 | 3,159.5 s | 410 s | 999 s |
| Qwen3.8-Flash-Next FP8, official | 1 | 21/21 | 3,823.1 s | 502 s | 1,152 s |
| MiMo-V2.6-Flash, thinking on | 1 | **11/19**, stopped | — | 531 s | 1,467 s |
| Ling-3.0-flash FP8 | 1 | 3/4, stopped | — | 530 s | 633 s |

A sum of medians is the time to run the whole set once, at each task's typical
time. Both of GLM @943912c's failures were one test that the task sets up as a
trap (below). TensorFold's one fail passed, but edited the test file.

### Hard set (#726)

This set has held-out tests the agent never sees, and a 1,643-line task that
spans five commits. 7 tasks × 3 trials, 3,600 s timeout.

| stack | run | visible verdict | hidden, whole task | hidden tests passed | median trial |
|---|---|---|---|---|---|
| **GLM-5.3-Flash EXL3** | @0f49cfd, 1 | 20/21 | 3/18 | 229/321 | 546 s |
| | @0f49cfd, 2 | 20/21 | 1/18 | 202/295 | 491.9 s |
| | @943912c | 18/21 | 4/18 | 210/295 | 514.9 s |
| | @94ae731, OpenCode 1.18.33, output cap 65,536 | 20/21 | 2/18 | 230/321 | **359.9 s** |
| Qwen3.8-Flash-Next NVFP4 | 1 | 20/21 | 1/18 | 234/321 | 740 s |
| | 2 | 20/21 | 1/18 | 234/321 | 866.7 s |
| DeepSeek-V4-Flash-Vision-Exp | 1 | 20/21 | 0/18 | 208/295 | 682 s |
| | 2 | 20/21 | 0/18 | 220/321 | 841.8 s |
| DeepSeek V4.1 Flash EXL3 | 1 | 16/17 valid | 2/14 | 157/217 | 1,140 s |
| GLM-5.3-Flash abliterated EXL3 on TensorFold, jayleaton's recipe | 1 | 20/21 | 2/18 | 204/321 | 354.8 s |

**How to read the hard-set table:**

- **The visible verdict includes the touched-tests guard.** Four passing
  trials count as fails because they edited test files. Three were GLM runs
  removing unused imports, and one was NVFP4 creating a missing fixture file.
- **The hidden columns leave out web-auth-hidden for every stack.** Its
  held-out file imports a private name (`_client_id`) that the commit's own
  tests never define, so no stack can pass it (#801).
- **V4.1 has 4 of its 21 rows excluded for answer exposure (#54).** The agent
  opened the directory that holds earlier trials' solution patches. Its counts
  cover the 17 valid rows.
- **Hidden-test pass rates do not separate the stacks.** Every run on
  vLLM lands between 68.5% and 72.9% of the tests collected. The one
  TensorFold run is below that range, at 63.6%. Every stack writes code that
  passes the tests it can see and fails some of the tests it can't, and none
  does clearly better at that.

**Two task traps to know about:**

- **gmail-api-sources' missing fixture.** One visible test builds the mbox
  source from `tests/fixtures/simple.mbox`, which the task cannot ship because
  the target repository's `.gitignore` excludes `*.mbox`. The reference
  implementation reads the file lazily and passes; an implementation that
  reads it in the constructor fails. GLM missed it in some runs and called it
  unfixable. NVFP4 once created the fixture itself, which the touched-tests
  guard fails.
- **The client's output cap (#726 finding).** OpenCode's per-step output cap
  was 16,384 for GLM and V4.1, 262,144 for NVFP4 and 1,048,576 for DSV4-V. It
  cut 2 of 132 GLM trials at the cap on the span tasks. Raising GLM's cap to
  65,536 changed nothing measurable: in that run no step reached 16,000
  tokens.

### Standard set

10 excision tasks × 3 trials per run. Only passed trials are timed.

| stack | runs: passed, median, worst |
|---|---|
| **GLM-5.3-Flash EXL3** | 30/30, 62 s, 130 s · 30/30, 49 s, 123 s · 30/30, 48 s, 96 s |
| DeepSeek V4.1 Flash EXL3 | 30/30, 75 s, 214 s · 60/60 over two runs, 77 s, 330 s |
| DeepSeek-V4-Flash-Vision-Exp | 40/40, 82 s, 187 s · 60/60 over two runs, 85 s, 347 s |
| Qwen3.8-Flash-Next NVFP4 | 30/30, 99 s, 178 s · 30/30, 86 s, 412 s · 30/30, 114 s, 304 s |
| Qwen3.8-Flash-Next FP8, official | 30/30, 93 s, 278 s |
| MiMo-V2.6-Flash, thinking on | 30/30, 211 s, 796 s · 30/30, 310 s, 749 s |
| MiMo-V2.6-Flash, thinking off | 23/28, 86 s, 862 s |

"Over two runs" means the generated table pools two runs that used the same
client image.

## 3. What the rest of the data supports

- **Qwen3.8-Flash-Next hibrid48 on vr8vr8's recipe is the most interesting
  newcomer (#806).** It passed 21 of 21 replay trials, and its one run took
  86% of the NVFP4 arm's replay time (2,545.7 s against 2,954.3 s). That puts
  it second on replay speed. The recipe's reported ~2× decode gain did not
  carry into agent wall time. It still took 134% of GLM's time, and GLM was
  faster on every task. It has one run and no hard-set data; the weights and
  image are third-party, approved for this screen.
- **DSV4-Flash-Vision is the choice when a task needs images or 1M tokens of
  context.** It decodes fastest of any stack, at 27–30 s per 1k output tokens,
  but agent wall time is mostly re-prefill and context handling, so it still
  trails GLM: 145% of GLM's replay time.
- **NVFP4 Flash-Next and V4.1 are tied behind them.** Their replay sums are
  within 7% of each other.
  - NVFP4 fits on one Spark.
  - V4.1 is the slowest of the leaders on the hard set and costs the most
    disk: 385 GiB with its Engram tables.
- **Newer versions help GLM but not NVFP4.** GLM's fastest hard-set run came on
  the latest recipe and client. NVFP4 on vLLM 0.30 (recipe @d23790b) took
  3,157.4 s on replay, within its three-run range.

- **TensorFold on jayleaton's recipe is the fastest stack measured, but not
  the best (#840).** It ran GLM-5.3-Flash on TensorFold v0.3.4 with the
  recipe's own patches
  ([jayleaton/glm53-tensorfold-spark](https://github.com/jayleaton/glm53-tensorfold-spark),
  rows in #839 and #843).
  - **Speed:** replay took 66% of GLM's time (1,261.7 s against 1,901.1 s).
    The hard set took 85% of the time of GLM's fastest run (10,838.3 s summed
    against 12,744.9 s; median trial 354.8 s against 359.9 s).
  - **Quality:** it passed 204 of 321 hidden tests (63.6%). That is below every
    vLLM run (68.5–72.9%), and below GLM's latest run at 230 of 321 (71.7%).
  - **Different weights:** the recipe serves an abliterated, re-quantized
    build (`neko-legends/GLM-5.3-Flash-Uncensored-EXL3`), not the TR3-4bpw
    weights the pick uses. The speed and the quality gap can come from the
    engine, the weights, or both. One run cannot say which.
  - **One serving failure:** on the hard set, one trial got a tool call back
    as plain text, which the client did not run. The replay fail edited a
    test file.
  - It has one run per task set. The next step is stock TensorFold on the
    pick's own weights (section 6).

### Screened out

These were stopped under the #762 rules (below 90% passed, or more than 2× the
leader's time). They are not ranked.

| stack | why it was stopped | issue |
|---|---|---|
| Qwen3.8-Flash-Next FP8, official | 201.1% of GLM's replay time, with the same pass rate as NVFP4. NVFP4 is the better build of this model. | #717 |
| MiMo-V2.6-Flash | 11/19 on replay: 6 timeouts at 1,800 s and 2 collection errors. Slowest on every standard task. The timeouts were long reasoning steps, not the repeated tool calls Xiaomi's MOPD fix targets. | #717 |
| Ling-3.0-flash FP8 | 507% of GLM's time on the first four replay tasks. The run was stopped by the time rule at 4/21, with 3 passed. | #752 |
| TensorFold v0.3.5 + GLM-5.3-Flash 4-bit | The largest context that fits is about 36k tokens (35,664 at `--context 0`), too small for 19 of 63 replay trials and 11 of 21 hard-set trials. MiaAI-Lab has announced a TensorFold GLM recipe; see #798. | #798 |

## 4. How to run the pick

On the cluster's head, check out the MiaAI-Lab GLM recipe at **upstream main**
(tested at `94ae731`). The daily preflight refuses a stale checkout (#820).
Start from its `.env.example` and set:

- `GLM53_DEFAULT_REASONING_EFFORT=low`. Otherwise the template runs at effort
  Max and truncates answers
  ([gotcha 23](../../docs/dgx-cluster-setup.md#23-a-large-models-default-reasoning-effort-eats-the-whole-token-budget--hit)).
  Check that `default-chat-template-kwargs {"reasoning_effort":"low"}` is in
  the server's argv.
- All four fabric interfaces and HCAs, in `HEAD_CX7_IF`, `WORKER_CX7_IF` and
  the `*_CX7_IB` variables.
- `MAX_MODEL_LEN=262144`, which every stack here was measured at.
- Keep the recipe's own defaults for the rest. At `94ae731` those are
  `GPU_MEM_UTIL=0.85`, an 11.8 GB KV pool
  (`--kv-cache-memory-bytes 11811160064`), `GLM53_DENSE_FP8=all`,
  `GLM53_KDA_BF16_LARGE_M=1`, the loader clone on, prefetch off, and host
  memory hygiene on.

Then run `SKIP_BUILD=1 ./start.sh`, which uses the published GHCR image
(digest `447114ee` as of 2026-09-29). With the weights cached on both nodes,
the API comes up on `:8888` in about 2 minutes, and the warmup and canary
finish about a minute later.

On the client, point OpenCode at model `GLM-5.3-Flash-EXL3` with a 262,144-token
context. Set its per-step output limit to 65,536, the server's own default; at
16,384 it cut a few span-task steps.

**Memory:** with the model loaded, the head idles at about 6 GiB
`MemAvailable` on this recipe, and the recipe's memory hygiene is on. Keep
`earlyoom` at the #700 line (1.0 / 0.5 GiB) on both nodes.

## 5. Weights on disk

- **MiMo-V2.6-Flash was deleted** from both nodes on 2026-09-25, after its
  rows landed.
- **Ling-3.0-flash** (120 GiB per node, plus a 2.6 GiB drafter) has a prune
  script ready and is waiting for the operator's go.
- **TensorFold's GLM 4-bit weights** (about 170 GiB per node) stay, at the
  operator's word.
- **The abliterated GLM EXL3 weights** for jayleaton's recipe (164 GiB per
  node) stay for more runs (#840).
- **Qwen3.8-Flash-Next hibrid48** (99 GB per node) arrived for #806 and stays
  while it has only one run.
- **FP8 Flash-Next is the next to let go** if space is needed: NVFP4 is the
  same model and ranks above it.

## 6. Paused, and what would resume it

Testing on the cluster is paused by the operator as of 2026-09-29. When it
resumes, in order:

- **Stock TensorFold (v0.3.6.3) on the pick's own EXL3 weights** (#840).
  This separates the engine's speed from the abliterated weights' quality.
- **More runs of GLM on the latest recipe.** It has one hard-set run and no
  replay run on @94ae731. Its fastest-ever result needs two more to count.
- **hibrid48's second and third replay runs, and its hard set** (#806).
- **MiaAI-Lab's TensorFold GLM recipe,** when it ships (#798).
- **Not measured:** throughput with several clients at once, and the hidden
  verdicts on web-auth, which wait on the #801 task fix.
