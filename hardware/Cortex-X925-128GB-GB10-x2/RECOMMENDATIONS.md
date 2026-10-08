# What to run on the dual DGX Spark cluster

> **Ledger read 2026-10-08, 00:30 EDT.** 2,298 rows in
> [`results.jsonl`](results.jsonl), from 2026-09-22T04:13-0400 to
> 2026-10-07T23:34-0400: 16 days of testing, 29 stack configurations. This page
> was rewritten from scratch on 2026-10-08. The previous version (2026-10-07)
> is in git history.
>
> **The ledger has two halves that do not compare.** Until 2026-10-06 09:14 EDT,
> OpenCode's `grep` and `glob` tools failed on every call in the client
> ([#968][i968]): 1,993 rows. After the fix: 305 rows. Section 3 ranks the
> stacks measured after the fix; section 4 keeps the earlier results, compared
> only with each other.

## The short answer

| you want | run | evidence |
|---|---|---|
| **A coding agent (the pick)** | **GLM-5.3-Flash EXL3 4bpw TensorFold quant on TensorFold v0.6.0**, [MiaAI-Lab recipe][r-tf-mia] v1.8 @`33b50fd` | 124 of 126 replay and hard-set trials passed over 3 runs on the fixed client ([#970][i970]). Fastest stack measured: sum of medians 4,111.1 s on 14 tasks. |
| The measured alternative | the same recipe with the **TR3 4bpw** checkpoint | 122 of 126 over the same 3 interleaved runs, 4,271.0 s (104% of the pick). |
| Image input, or more than 262k tokens of context | DeepSeek-V4-Flash-Vision-Exp on vLLM, [MiaAI-Lab DSpark recipe][r-dsv4v] | 1M context, vision. 63 of 63 replay trials, at 272% of TensorFold's replay time. Broken client only (section 4). |
| One Spark, not two | See the [single-Spark picks](../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md) | Qwen3.8-Flash-Next NVFP4 fits one node. |

**Not recommended for coding on this cluster: Qwen3.8-Flash-Next.** Every
Qwen stack passes nearly every trial, but none finishes the set in less than
188% of the pick's time. The time goes to reasoning: a median of about 14,000
reasoning tokens per trial, against the pick's 2,800 (section 3).

**What "best" means here.** Each stack runs [OpenCode][opencode] as a coding
agent on real tasks, and must pass the task's test suite. We rank on pass rate
first, then wall-clock time. Code quality beyond "passes the suite" is not
measured. Throughput with several clients at once is not measured either: there
is one client.

## 1. Test environment

### The cluster

| | head node | worker node |
|---|---|---|
| machine | NVIDIA DGX Spark | NVIDIA DGX Spark |
| chip | GB10 Grace Blackwell: 10 × Cortex-X925 + 10 × Cortex-A725 cores | same |
| memory | 128 GB LPDDR5x, unified CPU/GPU (121.7 GiB visible to Linux) | same |
| OS | DGX OS 7, OTA 7.6.0 (Ubuntu 24.04.5 LTS) | same |
| kernel | `7.0.0-1019-nvidia` | same |
| NVIDIA driver | 580.178.04 (open). **580.173.02 until 2026-09-22T23:38-0400** | 580.178.04 since 2026-09-21T22:43-0400 |
| CUDA (driver API) | 13.0 | 13.0 |
| Docker / NVIDIA Container Toolkit | 29.6.2 / 1.20.1 | 29.6.2 / 1.20.1 |
| OOM guard | earlyoom 1.7, kill at 1.0 GiB / 0.5 GiB available ([#700][i700]) | same |

**Fabric:** the nodes connect directly through their ConnectX-7 ports, at
200 Gb/s RoCE. All four RoCE interfaces are up on each node, MTU 4096,
firmware 28.45.4028. Every stack here is tensor-parallel (TP=2) across both
nodes, one rank per node. Setup is in
[`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md).

Rows from the first day (to 2026-09-22T23:38-0400) ran on mismatched driver
point releases. Every later row ran on 580.178.04 on both nodes.

### The client

The coding agent never runs on the cluster. It runs on a separate machine on
the LAN and calls the head node's OpenAI-compatible API ([#562][i562],
[#649][i649]), so the nodes' memory holds only the server.

| dates | client machine | client image |
|---|---|---|
| 2026-09-22 → 2026-09-30, 06:41 | Intel Core i3-7100, 2 cores, 16 GB | OpenCode 1.18.31 → 1.18.33, uv 0.12.13 → 0.12.21, CPython 3.14.4 → 3.14.7 |
| 2026-09-30, 10:12 → 2026-10-03 | Intel Core i9-13900H laptop, 20 threads, 64 GB, Linux 7.0.0-34-generic | `local-llm-client:1.18.34`: OpenCode 1.18.34, uv 0.12.21, CPython 3.14.7 |
| 2026-10-05, 12:44 → 16:38 | Intel Core i3-7100 (#912 arm A on recipe v1.7 only) | `local-llm-client:1.18.34`: OpenCode 1.18.34, uv 0.12.23, CPython 3.14.8 |
| 2026-10-05, 18:25 → 2026-10-06, 09:13 | Intel Core i9-13900H laptop | the same image |
| **2026-10-06, 09:14 →** | **Intel Core i9-13900H laptop** | **the #968 fix:** `local-llm-client:1.18.34`, then `:1.18.35` from 16:54: OpenCode 1.18.34 → 1.18.35, uv 0.12.23, CPython 3.14.8, ripgrep 15.2.0 |

From 2026-09-22T23:48-0400 the client runs in a Docker container limited to
12 GiB, with the kernel enforcing the limit ([#683][i683]). Each trial runs
inside a `bwrap` sandbox. Since the #968 fix, the image build and every batch
run a tool self-test that refuses an image with a broken tool. A row from the
fixed image carries `ripgrep=` in its client image.

**The client machine did not move the times.** The jayleaton TensorFold stack
ran on both machines. Its median replay trial was 156 s on the i3 and 145 s on
the i9. **The client also runs other work**, recorded and not gated: a load
average of 0.6–2.5, with a camera-detection process at 11–48% CPU.

### The tasks

All tasks run against [gmail-archive][gmail-archive] at commit `56e55cc`, 3
trials each, with a limit of 1,800 s per trial. Reasoning effort is `low`
everywhere except where a row says otherwise.

- **Standard set:** 10 excision tasks. The harness deletes a function, and the
  agent must restore it from the tests.
- **Replay set ([#714][i714]):** 7 tasks. The agent rebuilds a real commit of
  60–600 changed lines from its tests.
- **Hard set ([#726][i726]):** 7 tasks. Five carry **hidden tests** that the
  agent never sees. Two ask for one 1,643-line change that spans five commits;
  one of the two hides the test names.

Runs since 2026-10-01 launch replay and hard together: 14 tasks × 3 trials = 42
trials a run. Task details are in
[`benchmarks/agent/METHODOLOGY.md`](../../benchmarks/agent/METHODOLOGY.md).

### How to read the numbers

- **Passed** is the visible verdict. A trial fails if its suite fails, if it
  times out, or if the agent edited a test file to pass.
- **Sum of medians** is each task's median trial time, summed over the set: the
  time to run the set once at typical speed. A timeout counts at its full
  limit.
- **One run is not a result.** A 3-trial median carries ±28% ([#23][i23]), so
  two stacks need about a 56% gap on a task before it is real. A sum over 14
  tasks is steadier than one task, but we still ask for three runs before a
  firm claim.
- **Hidden tests** are counted per test, over the trials whose task holds tests
  out. Tests in one trial are not independent, and a trial with no result drops
  out of the count.
- **The screen ([#762][i762]).** A stack needs 90% or more passed and a sum of
  medians under 200% of the leader's, or it is "screened out". Then the fastest
  3 are kept, plus any within 25% of the third, up to 5. The rest are "cut":
  fine, but not worth more runs. During a run, an early stop ends it when its
  time reaches 200% of the leader's on the same trials.
- Every number on this page comes from committed scripts:

  ```sh
  L=hardware/Cortex-X925-128GB-GB10-x2/results.jsonl
  CUT=2026-10-06T09:14:00-0400                                       # the #968 fix
  uv run python scripts/screen_stacks.py --results $L --since $CUT   # section 3
  uv run python scripts/screen_stacks.py --results $L --before $CUT  # section 4
  uv run python scripts/screen_stacks.py --results $L --before $CUT --standard
  uv run python scripts/report.py --results $L --backend <name> \
      --since 2026-10-06T13:14:00+0000                               # per task, hidden tests
  ```

  `report.py` compares its bounds as strings, so give them in UTC (`+0000`),
  the form the rows use ([#992][i992]).

## 2. The pick: GLM-5.3-Flash on TensorFold, MiaAI-Lab recipe

[`MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold`][r-tf-mia] runs
[TensorFold][tensorfold] v0.6.0, TensorFold's Python engine, plus the recipe's
82 patches at v1.8 @`33b50fd`. The image tag is
`tensorfold-glm53:v0.6.0-31557ed1cef6`, which carries the hash of the patches;
it runs in NVIDIA's PyTorch container (`nvcr.io/nvidia/pytorch:26.07-py3`). We
built it locally (`PULL=0`). It serves the TensorFold quant,
[`Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold`][w-tfq] @`76c0b517`: the
recipe's own default download. The recipe pins @`078455ff`, which holds the
same weights; the later commits change only the model card. The drafter is
DFlash2 ([`incoai/GLM-5.3-Flash-DFlash2`][w-dflash2] @`bf582e4`). The server
runs an FP8 KV cache shared by 4 requests and a 1,048,576-token window.

### #970: the quant against TR3, on the fixed client

[#970][i970] was pre-registered on 2026-10-06. It ran the two checkpoints
interleaved, A B A B A B, 3 runs per arm, on replay + hard. All six runs used
the i9 client, the fixed client image, and one image of recipe v1.8. The
transcript audit ([#969][i969]) found no harness fault in any run.

- **A, TR3:** `Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw` @`9eaebb7`, backend
  `glm53fexl3tfmiaaiv18dual2xrc`.
- **B, the quant:** `Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold` @`76c0b517`,
  backend `glm53fexl3tfqmiaaiv18dual2xrc`.

| | A: TR3 | B: quant |
|---|---|---|
| passed, by verdict | 122/126 (42, 40, 40) | **124/126 (42, 41, 41)** |
| 95% CI (Wilson) | 92.1–98.8% | 94.4–99.6% |
| sum of medians, 14 tasks × 9 trials | 4,271.0 s | **4,111.1 s** (96.3%) |
| sum of medians, per run | 4,345.9 · 4,184.7 · 4,246.6 s | 4,154.1 · 4,123.0 · 4,062.5 s |
| median trial | **188.3 s** | 204.6 s (108.7%) |
| hidden tests passed | 851/1,170 (72.7%) | 878/1,170 (75.0%) |
| trials that passed every hidden test of their task | 3/63 | 5/63 |

**The rule, fixed before the first run:** B becomes the pick if it is not
worse on passes and its sum of medians is at most 5% above A's. 124 ≥ 122, and
4,111.1 s ≤ 4,484.6 s. **The quant became the pick.**

- **Keep the claim small.** Three runs per arm, a 4% gap on the sum, and
  overlapping intervals on the pass rate. This is "not worse", not "faster".
- **Failures.** TR3 failed `replay-gmail-api-sources` in two runs, on 1 of 24
  tests each time. Each arm also lost 2 passes because the agent edited a test
  file.
- **Time.** The quant's sum of medians was lower in every pair (95.6%, 98.5%,
  95.7% of TR3's), but its median single trial was longer. It is faster on the
  long tasks, which dominate the sum.

**TR3's original repository has left the Hub.** Download it from its author,
[`brandonmusic/GLM-5.3-Flash-tr3-4bpw`][w-tr3] @`a5fee929`, which is
byte-identical by sha256 to `9eaebb7` (#937). Section 6 shows the override.

**What to keep in mind:**

- **The engine is new and the recipe carries it.** TensorFold has few users,
  and the recipe's 82 patches sit on v0.6.0. TensorFold has since moved to
  v0.6.6 on its Python line and to a native Zig engine in v1.0 (2026-10-07).
  The native engine does not yet serve GLM on the GB10 ([#986][i986]). The
  recipe's patches did not apply to v0.6.5 (28 of 70 failed), so the pick
  moves when its recipe does.
- **Rows do not record the engine build** (`tensorfold_version=unknown`,
  [#320][i320], [#904][i904]). The recipe commit and the image tag are the
  record.
- **No standard-set run.** No TensorFold stack has run the 10 excision tasks.

## 3. Every stack on the fixed client

Five stacks ran the 14 replay + hard tasks after the #968 fix:

| stack (backend) | runs | passed | sum of medians | % of the pick | median reasoning tokens a trial | hidden tests | screen |
|---|---|---|---|---|---|---|---|
| **GLM TensorFold quant, MiaAI v1.8** (`glm53fexl3tfqmiaaiv18dual2xrc`), the pick | 3 | 124/126 | **4,111.1 s** | 100% | 2,815.5 | 878/1,170 (75.0%) | keep |
| GLM TensorFold TR3, MiaAI v1.8 (`glm53fexl3tfmiaaiv18dual2xrc`) | 3 | 122/126 | 4,271.0 s | 104% | 3,319.5 | 851/1,170 (72.7%) | keep |
| Qwen3.8-Flash-Next NVFP4 on TensorFold Zig, MiaAI ([#897][i897]) (`qwen38fnnvfp4tfmiaaidual2xrc`) | 1, stopped at 25 of 42 | 25/25 | 7,746.8 s | 188% | 13,991 | 160/194 (82.5%) | stopped early |
| Qwen3.8-Flash-Next hibrid48, thinking on, vLLM v5.2 ([#977][i977]) (`qwen38fnhibrid48v52dual2xrc`) | 1, stopped at 14 of 42 | 13/14 | 8,138.6 s | 198% | 15,570 | 87/101 (86.1%) | stopped early |
| Qwen3.8-Flash-Next hibrid48, thinking off, vLLM v5.2 ([#977][i977]) (`qwen38fnhibrid48v52nothinkdual2xrc`) | 1, stopped at 14 of 42 | 12/14 | 10,889.4 s | 265% | 0 | 59/78 (75.6%) | screened out |

A timeout counts at the 1,800 s limit in every sum. A run stopped early has
fewer trials behind each median.

`screen_stacks.py` marks the two early-stopped Qwen runs "keep" here, because
only four stacks pass its gate on this side of the cut and it keeps the fastest
three. They do not earn more runs. The run-time early stop ended each one at
over 200% of its leader on the same trials. That leader was the broken-client
run of 2026-10-01 (3,953.4 s), not the fixed-client pick ([#992][i992]). Against
the pick, they are at 188% and 198%: no verdict changes.

**What the Qwen runs show:**

- **Qwen passes, but slowly.** 25 of 25 and 13 of 14 visible verdicts, and the
  #897 run passed `replay-span-web-ui-no-test-names` (1,321.3 s), which
  hibrid48 timed out on. But every trial carries about 5 times the pick's
  reasoning tokens.
- **Thinking off is slower, not faster.** Without reasoning, hibrid48 took about
  three times as many steps (the #977 read-out), 134% of the thinking-on time,
  and lost two trials to timeouts.
- **The engine is not the gap.** On the same NVFP4 weights and the 7 replay
  tasks, the Zig engine's sum of medians was 2,359.6 s, against 3,157.4 s on
  vLLM 0.30 (`qwen38fnnvfp4dual2xrcv030`): 75%. That compares across the
  client fix, so it is indicative only. The gap to the pick is about 2x.
- **Qwen's hidden-test rate is the highest measured**, 82.5% and 86.1%, against
  72.7–75.0% for GLM. But that is 11 and 7 trials, and tests in one trial are
  not independent. It is a reason to look again with a faster Qwen stack, not
  a result.

## 4. Before the fix (broken client)

These runs had OpenCode's `grep` and `glob` failing ([#968][i968]); the model
fell back to `bash`. They compare with each other, not with section 3. Their
numbers stand; the history of how the pick was chosen lives here.

### Replay + hard: 14 tasks

| stack (backend) | runs | passed | sum of medians | % of v1.3 | hidden tests | screen |
|---|---|---|---|---|---|---|
| GLM TensorFold quant, MiaAI v1.7.1 (`glm53fexl3tfqmiaaiv171dual2xrc`) | 1 | 40/42 | **3,602.5 s** | 93% | 289/390 (74.1%) | keep |
| GLM TensorFold TR3, MiaAI v1.3 @`978b225` (`glm53fexl3tfmiaaidual2xrc`) | 1 | 42/42 | 3,885.1 s | 100% | 283/390 (72.6%) | keep |
| GLM TensorFold TR3, MiaAI v1.5 @`1576746` (`…v15…`) | 3 | 124/126 | 4,061.5 s | 105% | 853/1,170 (72.9%) | keep |
| GLM TensorFold TR3, MiaAI v1.4 @`cf28cc4` (`…v14…`) | 2 | 82/84 | 4,069.4 s | 105% | 576/780 (73.8%) | keep |
| GLM TensorFold TR3, MiaAI v1.8 @`33b50fd` (`…v18…`) | 1 | 41/42 | 4,076.9 s | 105% | 280/390 (71.8%) | keep |
| GLM TensorFold TR3, MiaAI v1.3.2 @`92bf731` (`…v132…`) | 2 | 82/84 | 4,161.7 s | 107% | 557/780 (71.4%) | cut |
| GLM TensorFold quant, MiaAI v1.8 (`glm53fexl3tfqmiaaiv18dual2xrc`) | 1 | 41/42 | 4,245.7 s | 109% | 286/390 (73.3%) | cut |
| GLM TensorFold TR3, MiaAI v1.7 (`…v17…`) | 1 | 42/42 | 4,435.8 s | 114% | 296/390 (75.9%) | cut |
| GLM TensorFold, jayleaton (`glm53ftfjaydual2xrc`) ² | 3 | 123/126 | 4,647.9 s | 120% | 746/1,084 (68.8%) | cut |
| GLM vLLM NVFP4, kindlingai (`glm53fnvfp4kindlingdual2xrc`) ² | 3 | 120/126 | 4,872.1 s | 125% | 873/1,170 (74.6%) | cut |
| GLM vLLM TR3, MiaAI @`943912c` (`glm53fexl3dual2xrclatest`) | 2 | 78/84 | 6,811.6 s | 175% | 210/292 (71.9%) | cut |
| GLM TensorFold abliterated quant, MiaAI v1.8 ([#940][i940]) | 1 | 35/42 | 3,467.3 s | 89% | 215/315 (68.3%) | screened out: 83% passed |
| GLM TensorFold, jayleaton, effort `high` | 1 | 36/42 | 7,765.8 s | 200% | 190/216, 5 trials with no result | screened out |
| DeepSeek V4.1 Flash EXL3 (`dsv41fexl3dual2xrc`) | 1 | 33/38 | 11,165.6 s | 287% | 418/494, 3 trials with no result | screened out |

² Different weights: jayleaton serves the
[neko-legends abliterated EXL3][w-neko]; kindlingai serves
[nvidia/GLM-5.3-Flash-NVFP4][w-glm-nvfp4].

**Why the quant on v1.7.1 does not rank first overall.** It was one run, on
the broken client. On the fixed client, three runs of the quant on v1.8 took
4,111.1 s. One fast run is the reason #970 retested, not a result.

**What these runs showed about the engine:**

- **TensorFold is the fastest engine measured, by a margin that clears the
  noise.** On the same TR3 checkpoint and client, vLLM took 178% of
  TensorFold's replay time (1,802.3 s against 1,013.5 s). Over the 14 tasks,
  TR3 on every MiaAI TensorFold release took 57–65% of vLLM's 6,811.6 s.
- **The recipe releases from v1.3 to v1.8 cannot be told apart.** TR3 on six
  recipe commits, ten runs in all: sums of medians from 3,885.1 s to
  4,435.8 s, a 14% range against the ~56% two 3-trial medians need.
- **Abliteration cost passes.** The abliterated quant (#940) was the fastest
  run but failed 7 of 42, 6 of them on the hard set.

### Replay only: 7 tasks, stacks that did not run the hard set

| stack (backend) | runs | passed | sum of medians | % of v1.3 | screen |
|---|---|---|---|---|---|
| GLM vLLM TR3, MiaAI @`0f49cfd` (`glm53fexl3dual2xrcctx`) | 3 | 63/63 | 1,901.1 s | 188% | cut |
| Qwen3.8-Flash-Next hibrid48, recipe v4.1 (`qwen38fnhibrid48dual2xrc`) | 1 | 21/21 | 2,545.7 s | 251% | screened out |
| DeepSeek-V4-Flash-Vision-Exp (`dsv4flashvisiondspark2xrc`) | 3 | 63/63 | 2,757.7 s | 272% | screened out |
| Qwen3.8-Flash-Next NVFP4, vLLM (`qwen38fnnvfp4dual2xrcflags`) | 3 | 63/63 | 2,954.3 s | 291% | screened out |
| DeepSeek V4.1 Flash EXL3 (`dsv41fexl3dual2xrc`) | 4 | 84/84 | 3,155.9 s | 311% | screened out |
| Qwen3.8-Flash-Next NVFP4, vLLM 0.30 (`qwen38fnnvfp4dual2xrcv030`) | 1 | 21/21 | 3,157.4 s | 312% | screened out |
| Qwen3.8-Flash-Next FP8 (`qwen38fnfp8dual2xrc`) | 1 | 21/21 | 3,823.1 s | 377% | screened out |
| MiMo-V2.6-Flash, thinking on (`mimo26fdual2xrcthink`) | 1 | 11/19 | 7,891.4 s | 779% | screened out |

"% of v1.3" is against TensorFold TR3 v1.3's replay sum, 1,013.5 s. Some of
these stacks also ran parts of the hard set; their full numbers are in
`screen_stacks.py --before`.

### Standard set: 10 excision tasks

| stack (backend) | runs | passed | sum of medians | screen |
|---|---|---|---|---|
| **GLM vLLM TR3** @`0f49cfd` (`glm53fexl3dual2xrcctx`) | 2 | 60/60 | **446.9 s** | keep |
| GLM vLLM TR3, client context 65,536 (`glm53fexl3dual2xrc`) | 1 | 30/30 | 514.8 s | keep |
| DeepSeek V4.1 Flash EXL3 | 3 | 90/90 | 734.1 s | keep |
| DeepSeek-V4-Flash-Vision-Exp | 4 | 100/100 | 763.5 s | keep |
| Qwen3.8-Flash-Next NVFP4 | 1 | 30/30 | 780.9 s | keep |
| Qwen3.8-Flash-Next FP8 | 1 | 30/30 | 812.2 s | cut |
| Qwen3.8-Flash-Next NVFP4, two-node flags | 2 | 60/60 | 851.6 s | cut |
| MiMo-V2.6-Flash, thinking off | 1 | 23/28 | 2,150.2 s | screened out |
| MiMo-V2.6-Flash, thinking on | 2 | 60/60 | 2,598.1 s | screened out |

This set is saturated: every main stack passes every trial, so it separates the
stacks on speed only. No TensorFold stack ran it.

### Dropped for a reason beyond the screen

| stack | why | issue |
|---|---|---|
| MiMo-V2.6-Flash | 11 of 19 on replay: 6 timeouts and 2 collection errors. | [#717][i717] |
| Ling-3.0-flash FP8 | 507% of GLM vLLM's time on the first 4 replay tasks; stopped at 3 of 4 passed. | [#752][i752] |
| TensorFold v0.3.5 on a GLM 4-bit MLX build | The largest context that fit was about 36k tokens, too small for 19 of 63 replay trials. | [#798][i798] |
| GLM TensorFold, effort `high` | 15 of 21 on the hard set; span tasks 0 of 6. | [#840][i840] |

## 5. What sixteen days of data show

- **The engine matters more than the quantization.** The same TR3 weights took
  57–65% of vLLM's time on TensorFold. No change of quantization came close.
- **Reasoning volume decides the Qwen–GLM gap.** Qwen3.8-Flash-Next takes
  about twice as long a task as the pick, and reasons about 5 times as many
  tokens (13,991 against 2,815.5 a trial). Turning thinking off made it slower
  still: it took about three times as many steps.
- **Agent time is mostly re-reading context.** Prefill and prefix caching
  dominate a trial, not tokens per second. The decode table is in
  [`docs/results.md`](../../docs/results.md#cortex-x925-128gb-gb10-x2).
- **Set reasoning effort `low` explicitly.** On jayleaton's TensorFold stack,
  `high` took 171% of `low`'s time on the 12 non-span tasks and passed 0 of 6
  span trials ([#840][i840], [#894][i894]). GLM's template falls back to `max`
  when nothing is set; Qwen3.8-Flash-Next's falls back to `xhigh`.
- **Hidden tests separate the stacks little.** Every GLM stack lands between
  68.3% and 75.9%. Qwen sits higher (section 3), on few trials.
- **Two task traps cost some trials on every stack:**
  - gmail-api-sources builds a source from `tests/fixtures/simple.mbox`, which
    the target repository's `.gitignore` excludes. An agent that reads the
    file early fails one visible test.
  - OpenCode's per-step output cap cut a few span-task steps at 16,384 tokens.
    The pick runs at 32,768.

## 6. How to run the pick

On the head node, as the user that owns Docker:

1. Clone the recipe at its latest commit. The pick was measured at v1.8
   @`33b50fd`:

   ```sh
   git clone https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold ~/miaai-tf-glm
   cd ~/miaai-tf-glm
   ```

2. Create `scripts/local.sh`. Set the worker's SSH target on the fabric, build
   the image locally, and copy the weights to the worker:

   ```sh
   WORKER=<user>@<worker-fabric-address>
   PULL=0
   WORKER_WEIGHTS=copy
   MODEL_REVISION=76c0b5173166d2795dd48860f45d8224817f894c
   ```

   Do not set `MODEL_ID`: the recipe's default checkpoint is the pick.
   `MODEL_REVISION` selects the exact snapshot #970 measured; it is optional.
   For TR3, set both instead:

   ```sh
   MODEL_ID=brandonmusic/GLM-5.3-Flash-tr3-4bpw
   MODEL_REVISION=a5fee929cf4888b1824323e33e8a19b60129e025
   ```

3. Run `./scripts/prepare.sh`. It downloads the checkpoint and the drafter on
   both nodes (about 176 GB each), builds the image, and checks that both nodes
   hold the same image.
4. Run `./start.sh`. The first start compiles CUDA kernels for GB10, about 6
   minutes; later starts use the cache. The API listens on `:8888` as model
   `GLM-5.3-Flash-EXL3`.
5. Stop it with `./stop.sh`. It stops both ranks.

On the client, add this provider to `~/.config/opencode/opencode.json`. The
recipe has no server default for reasoning effort, so the client must send
`low`:

```json
"tfmia8888": {
  "npm": "@ai-sdk/openai-compatible",
  "options": {
    "baseURL": "http://<head-node>:8888/v1",
    "apiKey": "local",
    "chunkTimeout": 900000,
    "timeout": false
  },
  "models": {
    "GLM-5.3-Flash-EXL3": {
      "tool_call": true,
      "reasoning": false,
      "temperature": false,
      "options": { "reasoningEffort": "low" },
      "limit": { "context": 262144, "output": 32768 }
    }
  }
}
```

**Memory:** under load, the head keeps 6.4–8.4 GiB available and the worker
8.7–10.6 GiB. Keep earlyoom at 1.0 / 0.5 GiB on both nodes.

## 7. Tool versions, checked 2026-10-08

| tool | used for these results | latest | status |
|---|---|---|---|
| OpenCode | 1.18.31 → 1.18.35 | [1.18.35][opencode-rel], 2026-10-06 | current |
| uv, in the client image | 0.12.13 → 0.12.23 | [0.12.23][uv-rel], 2026-10-03 | current |
| CPython, in the client image | 3.14.4 → 3.14.8 | [3.14.8][py-rel] | current |
| TensorFold | v0.6.0 + 82 patches (pick); `zig-flashnext` @`db281878` (#897); v0.3.4 (jayleaton) | [v1.0.2][tf-rel], 2026-10-07; Python line v0.6.6 | the pick's recipe pins v0.6.0 ([#986][i986]) |
| MiaAI TensorFold GLM recipe | v1.8 @`33b50fd` | @`33b50fd` | current |
| MiaAI TensorFold Qwen recipe | @`cbfc429` | @`cbfc429` | current |
| myllmbox hibrid48 recipe | v5.2 @`d2721ec` | @`d2721ec` | current |
| kindlingai GLM recipe | @`c748079` | @`c748079` | current |
| MiaAI vLLM GLM recipe | @`94ae731` at its last run | @`a246263` | behind; not re-run |
| MiaAI vLLM Qwen recipe | @`d23790b` at its last run | @`cd839d0` | behind; not re-run |
| vLLM | dev builds in the recipe images; 0.30.0 in three | [0.31.0][vllm-rel], 2026-10-05 | behind where a recipe pins it |
| SGLang | nightly 2026-09-21 (MiMo, Ling) | [0.5.21][sglang-rel], 2026-10-02 | behind; not re-run |
| DGX OS | OTA 7.6.0 | 7.6.0 | current |
| NVIDIA driver | 580.178.04 | 580 LTS branch | kept on 580 by policy |
| Docker / Container Toolkit | 29.6.2 / 1.20.1 | same | current |
| Ubuntu userland | — | 95 updates pending on each node | to apply in a gap |

The driver stays on the 580 branch. DGX OS 7.6.0 ships it, and every image here
was built and measured on it.

## 8. Open, and what would change this page

- **[#986][i986]:** a MiaAI GLM recipe on TensorFold's native engine. If native
  prompt caching comes with it, agent turns could get faster.
- **A faster Qwen stack.** Qwen's hidden-test rate leads, on few trials. The
  #897 recipe's INT4-AutoRound quant (5 of 10 experts, about 32% faster decode
  per the recipe) is not approved. Faster decode alone would not close a 2x gap
  that comes from reasoning volume.
- **[#992][i992]:** the early stop's leader should be the fixed-client pick.
- **[#865][i865]:** RedHat's Qwen3.8-Flash-Next NVFP4 build against MiaAI's.
  MiaAI says its Qwen quant will change soon.
- **[#904][i904]:** rows should record the serving engine's version.
- **Not measured:** several clients at once, and code quality beyond the tests.

## References

**Recipes:**
[MiaAI-Lab TensorFold GLM][r-tf-mia] ·
[MiaAI-Lab TensorFold Qwen][r-tf-qwen] ·
[MiaAI-Lab vLLM GLM][r-glm-vllm] ·
[jayleaton TensorFold GLM][r-tf-jay] ·
[myllmbox hibrid48][r-hibrid] ·
[MiaAI-Lab Qwen dual][r-qwen] ·
[MiaAI-Lab DSV4 DSpark][r-dsv4v] ·
[MiaAI-Lab DSV4.1 EXL3][r-dsv41] ·
[MiaAI-Lab MiMo][r-mimo] ·
[kindlingai GLM NVFP4][r-kindling]

**Engines and client:** [TensorFold][tensorfold] · [vLLM][vllm] ·
[SGLang][sglang] · [OpenCode][opencode]

**Issues:** [#711][i711] the cluster's question ·
[#714][i714] replay · [#726][i726] hard set · [#762][i762] screening ·
[#840][i840] jayleaton TensorFold · [#892][i892] MiaAI TensorFold ·
[#968][i968] client tools · [#970][i970] TR3 against the quant ·
[#977][i977] hibrid48 v5.2 · [#897][i897] Qwen on TensorFold ·
[#986][i986] · [#992][i992] · [#904][i904]

[r-tf-mia]: https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold
[r-tf-qwen]: https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold
[r-glm-vllm]: https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks
[r-tf-jay]: https://github.com/jayleaton/glm53-tensorfold-spark
[r-hibrid]: https://github.com/myllmbox/qwen38-flash-next-cluster-recipe
[r-qwen]: https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Dual-DGX-Sparks
[r-dsv4v]: https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark
[r-dsv41]: https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-EXL3-2x-DGX-Sparks
[r-mimo]: https://github.com/MiaAI-Lab/MiMo-V2.6-Flash-2x-DGX-Sparks
[r-kindling]: https://github.com/kindlingai/glm-5.3-flash-gx10
[w-tfq]: https://huggingface.co/Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold
[w-tr3]: https://huggingface.co/brandonmusic/GLM-5.3-Flash-tr3-4bpw
[w-dflash2]: https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2
[w-neko]: https://huggingface.co/neko-legends/GLM-5.3-Flash-Uncensored-EXL3
[w-glm-nvfp4]: https://huggingface.co/nvidia/GLM-5.3-Flash-NVFP4
[tensorfold]: https://github.com/ashhart/TensorFold
[tf-rel]: https://github.com/ashhart/TensorFold/releases
[vllm]: https://github.com/vllm-project/vllm
[vllm-rel]: https://github.com/vllm-project/vllm/releases
[sglang]: https://github.com/sgl-project/sglang
[sglang-rel]: https://github.com/sgl-project/sglang/releases
[opencode]: https://github.com/anomalyco/opencode
[opencode-rel]: https://github.com/anomalyco/opencode/releases
[uv-rel]: https://github.com/astral-sh/uv/releases
[py-rel]: https://www.python.org/downloads/
[gmail-archive]: https://github.com/evanwtf/gmail-archive
[i23]: https://github.com/evanwtf/local-llm/issues/23
[i320]: https://github.com/evanwtf/local-llm/issues/320
[i562]: https://github.com/evanwtf/local-llm/issues/562
[i649]: https://github.com/evanwtf/local-llm/issues/649
[i683]: https://github.com/evanwtf/local-llm/issues/683
[i700]: https://github.com/evanwtf/local-llm/issues/700
[i711]: https://github.com/evanwtf/local-llm/issues/711
[i714]: https://github.com/evanwtf/local-llm/issues/714
[i717]: https://github.com/evanwtf/local-llm/issues/717
[i726]: https://github.com/evanwtf/local-llm/issues/726
[i752]: https://github.com/evanwtf/local-llm/issues/752
[i762]: https://github.com/evanwtf/local-llm/issues/762
[i798]: https://github.com/evanwtf/local-llm/issues/798
[i840]: https://github.com/evanwtf/local-llm/issues/840
[i865]: https://github.com/evanwtf/local-llm/issues/865
[i892]: https://github.com/evanwtf/local-llm/issues/892
[i894]: https://github.com/evanwtf/local-llm/pull/894
[i897]: https://github.com/evanwtf/local-llm/issues/897
[i904]: https://github.com/evanwtf/local-llm/issues/904
[i940]: https://github.com/evanwtf/local-llm/issues/940
[i968]: https://github.com/evanwtf/local-llm/issues/968
[i969]: https://github.com/evanwtf/local-llm/issues/969
[i970]: https://github.com/evanwtf/local-llm/issues/970
[i977]: https://github.com/evanwtf/local-llm/issues/977
[i986]: https://github.com/evanwtf/local-llm/issues/986
[i992]: https://github.com/evanwtf/local-llm/issues/992
