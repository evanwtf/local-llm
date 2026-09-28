# What to run on the dual DGX Spark cluster

> **Ledger last read 2026-09-27, 23:55 EDT.** The pick has three runs behind it on
> every task set that has three runs. Three things are still running and will
> be folded in when they land (section 6). None of them can make another stack
> faster than GLM. At most they can change the order behind it.
>
> | stack | standard-set runs | replay runs (#714) | hard-set runs (#726) |
> |---|---|---|---|
> | **GLM-5.3-Flash EXL3** | 3 | 3 | 1 (2nd running) |
> | DeepSeek-V4-Flash-Vision-Exp | 3 | 3 | 1 |
> | Qwen3.8-Flash-Next NVFP4 | 3 | 3 + 1 on vLLM 0.30 | 1 |
> | DeepSeek V4.1 Flash EXL3 | 3 | 3 | 1 |
>
> "Passes the suite" is the whole quality claim. Aggregate throughput with
> several clients at once is **not measured**, because there is one client
> machine.

The cluster is two GB10 DGX Sparks (2 × 128 GB unified memory), joined by
200 Gb/s ConnectX-7 RoCE. It serves a coding agent (OpenCode 1.18.31/1.18.32)
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
([MiaAI-Lab recipe](https://github.com/MiaAI-Lab/GLM-5.3-Flash-2x-DGX-Sparks)),
with reasoning effort `low` set on the server.

- **Quality: level with the best on every task set.**
  - It passed all 90 standard-set trials and all 63 replay trials.
  - On the hard set it passed 20 of 21 visible verdicts, the same as the next
    two stacks.
  - It passed the most whole hidden tasks (3 of 18). NVFP4 passed 5 more
    individual hidden tests (section 2).
- **Speed: fastest on every task set, in every run.**

  | task set | GLM | best other stack |
  |---|---|---|
  | standard set, worst of GLM's three runs vs best of any other run | 62 s median | 75 s median |
  | replay, sum of per-task medians | 1,901.1 s | 2,757.7 s |
  | hard set, median trial | 546 s | 682 s |

  So the next-fastest stack took 145% of GLM's replay time.
- **Nothing else is close enough to reorder.** The replay sums below pool
  three runs per stack. The stacks behind GLM change places from run to run,
  but GLM never moves.

## 2. Every stack measured

### Replay tasks (#714)

Rebuild a real commit of 60–600 lines from its tests. 7 tasks × 3 trials per
run.

| stack | runs | passed | sum of per-task medians | median trial | worst trial |
|---|---|---|---|---|---|
| **GLM-5.3-Flash EXL3** | 3 | 63/63 | **1,901.1 s** | 216 s | 608 s |
| DeepSeek-V4-Flash-Vision-Exp | 3 | 63/63 | 2,757.7 s | 435 s | 923 s |
| Qwen3.8-Flash-Next NVFP4 | 3 | 63/63 | 2,954.3 s | 404 s | 1,082 s |
| Qwen3.8-Flash-Next NVFP4, vLLM 0.30 + recipe `d23790b` | 1 | 21/21 | 3,157.4 s | 438 s | 1,081 s |
| DeepSeek V4.1 Flash EXL3 | 3 | 63/63 | 3,159.5 s | 410 s | 999 s |
| Qwen3.8-Flash-Next FP8, official | 1 | 21/21 | 3,823.1 s | 502 s | 1,152 s |
| MiMo-V2.6-Flash, thinking on | 1 | **11/19**, stopped | — | 531 s | 1,467 s |
| Ling-3.0-flash FP8 | 1 | 3/4, stopped | — | 530 s | 633 s |

A sum of medians is the time to run the whole set once, at each task's typical
time.

### Hard set (#726)

This set has held-out tests the agent never sees, and a 1,643-line task that
spans five commits. 7 tasks × 3 trials, 3,600 s timeout.

| stack | visible verdict | hidden, whole task passed | hidden tests passed | median trial | worst trial |
|---|---|---|---|---|---|
| **GLM-5.3-Flash EXL3** | 20/21 | 3/18 | 229/321 | **546 s** | 2,067 s |
| Qwen3.8-Flash-Next NVFP4 | 20/21 | 1/18 | 234/321 | 740 s | 2,557 s |
| DeepSeek-V4-Flash-Vision-Exp | 20/21 | 0/18 | 208/295 | 682 s | 3,557 s |
| DeepSeek V4.1 Flash EXL3 | 16/17 | 2/14 | 157/217 | 1,140 s | 3,471 s |

**How to read the hard-set table:**

- **The visible verdict includes the touched-tests guard.** GLM and NVFP4 each
  had one trial that passed by editing the tests, and that trial is counted as
  a fail.
- **The hidden columns leave out web-auth-hidden for every stack.** Its
  held-out file imports a private name (`_client_id`) that the commit's own
  tests never define, so no stack can pass it (#801).
- **V4.1 has 4 of its 21 rows excluded for answer exposure (#54).** The agent
  opened the directory that holds earlier trials' solution patches. Its counts
  cover the 17 valid rows, so its denominators are smaller. On those rows it
  passed 2 of 14 whole hidden tasks, not the "most hidden passes" that a count
  including excluded rows suggested.
- **Hidden-test pass rates do not separate the four.** They run from 70.5% to
  72.9% of the tests collected. One run of 18 trials cannot tell that spread
  apart. Every stack writes code that passes the tests it can see and
  fails some of the tests it can't, and none does clearly better at that.

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

- **DSV4-Flash-Vision is the second choice.** It is also the one to run when a
  task needs images or 1M tokens of context.
  - It is second-fastest on replay, taking 145% of GLM's time.
  - It decodes fastest of any stack, at 27–30 s per 1k output tokens against
    50–85 s for GLM. Its task times still trail GLM's, because agent wall time
    is mostly re-prefill and context handling, not decode.
- **NVFP4 Flash-Next and V4.1 are tied for third.** Their replay sums are
  within 7% of each other, and their standard-set medians overlap run to run.
  - NVFP4 fits on one Spark.
  - V4.1 is the slowest of the four on the hard set, and it costs the most
    disk: 385 GiB with its Engram tables.
- **The vLLM 0.30 update did not help NVFP4.** One replay run on vLLM 0.30 with
  the recipe at `d23790b` took 3,157.4 s, against 2,954.3 s averaged over three
  runs on the earlier stack. That is within the run-to-run range.

### Screened out

These were stopped under the #762 rules (below 90% passed, or more than 2× the
leader's time). They are not ranked.

| stack | why it was stopped | issue |
|---|---|---|
| Qwen3.8-Flash-Next FP8, official | 201.1% of GLM's replay time, with the same pass rate as NVFP4. NVFP4 is the better build of this model. | #717 |
| MiMo-V2.6-Flash | 11/19 on replay: 6 timeouts at 1,800 s and 2 collection errors. Slowest on every standard task. | #717 |
| Ling-3.0-flash FP8 | 507% of GLM's time on the first four replay tasks. The run was stopped by the time rule at 4/21, with 3 passed. | #752 |
| TensorFold v0.3.5 + GLM-5.3-Flash 4-bit | The largest context that fits is about 36k tokens (35,664 at `--context 0`). That is too small for 19 of 63 replay trials and 11 of 21 hard-set trials. No agent run was possible. | #798 |

## 4. How to run the pick

On the cluster's head, check out the MiaAI-Lab GLM recipe. It was tested at
`0f49cfd`; see section 6 for the update. Set these in `.env`:

- `GLM53_DEFAULT_REASONING_EFFORT=low`. Otherwise the template runs at effort
  Max and truncates answers
  ([gotcha 23](../../docs/dgx-cluster-setup.md#23-a-large-models-default-reasoning-effort-eats-the-whole-token-budget--hit)).
  Check that `default-chat-template-kwargs {"reasoning_effort":"low"}` is in
  the server's argv.
- All four fabric interfaces and HCAs, in `HEAD_CX7_IF`, `WORKER_CX7_IF` and
  the `*_CX7_IB` variables.
- `MAX_MODEL_LEN=262144`, `GPU_MEM_UTIL=0.86`, and the recipe's explicit KV
  pool (`--kv-cache-memory-bytes 15032385536`).

Then run `SKIP_BUILD=1 ./start.sh`, which uses the published GHCR image. A
local rebuild after the 2026-09-24 recipe update failed at weight load: the
InstantTensor buffer went over the device budget.

With the weights already cached on both nodes, the API comes up on `:8888`
about 8–9 minutes after launch. Point OpenCode at model `GLM-5.3-Flash-EXL3`
with a 262,144-token context.

**Memory:** with the model loaded, the head idles at 3.6–4.1 GiB
`MemAvailable`, and the recipe has no memory guard of its own. Keep `earlyoom`
at the #700 line (1.0 / 0.5 GiB) on both nodes.

## 5. Weights on disk

- **MiMo-V2.6-Flash was deleted** from both nodes on 2026-09-25, after its
  rows landed.
- **Ling-3.0-flash** (120 GiB per node, plus a 2.6 GiB drafter) has a prune
  script ready and is waiting for the operator's go.
- **TensorFold's GLM 4-bit weights** (about 170 GiB per node) are also waiting
  for a keep-or-delete decision.
- **FP8 Flash-Next is the next to let go** if space is needed: NVFP4 is the
  same model and ranks above it.

## 6. Still running

This file will be updated when these land.

- **GLM hard set, run 2.** Started 2026-09-27 at 22:08 EDT. At 23:55 EDT it had
  9 of 21 trials done: all passed the visible verdict, none touched tests, and
  none were excluded.
- **GLM on the updated recipe.** Upstream is 30 commits past `0f49cfd`,
  including a KV tail-seed stride fix. The plan is one replay run and one
  hard-set run, to decide which commit section 4 should name.
- **NVFP4 hard set, run 2.**
- **Not measured:** throughput with several clients at once, and the hidden
  verdicts on web-auth, which wait on the #801 task fix.
