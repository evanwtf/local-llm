# What to run on the dual DGX Spark cluster

> **Ledger read 2026-10-02, 03:20 EDT; the pick restamped 2026-10-03, 23:30 EDT.**
> 1,447 rows in [`results.jsonl`](results.jsonl) at the first read, from
> 2026-09-22T04:13-0400 to 2026-10-02T02:51-0400: ten days of continuous
> testing, 18 stack configurations of 9 checkpoints on 3 engines. This page was
> rewritten from scratch on 2026-10-01. On 2026-10-03 the pick moved to the
> recipe's v1.5 release, measured over 3 runs ([#892][i892]). The previous
> version (2026-09-29) is in git history.

## The short answer

| you want | run | evidence |
|---|---|---|
| **A coding agent (the pick)** | **GLM-5.3-Flash EXL3 TR3 4bpw on TensorFold v0.6.0**, [MiaAI-Lab recipe][r-tf-mia] v1.5 @`1576746` | 124 of 126 replay and hard-set trials passed over 3 runs of v1.5 (95% CI 94.4–99.6%). The fastest engine measured on both task sets, in every run: 59–62% of vLLM's time on the same weights. Hidden-test pass rate level with every other stack. |
| The most-measured fallback | The same weights on vLLM, [MiaAI-Lab vLLM recipe][r-glm-vllm] | 3 standard, 5 replay and 5 hard-set runs. It passed every standard trial and was the fastest stack on that set. On replay it took 178% of the pick's time. |
| Image input, or more than 262k tokens of context | DeepSeek-V4-Flash-Vision-Exp on vLLM, [MiaAI-Lab DSpark recipe][r-dsv4v] | 1M context, vision. Passed 63 of 63 replay trials, at 272% of the pick's time. |
| One Spark, not two | See the [single-Spark picks](../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md) | Qwen3.8-Flash-Next NVFP4 fits one node. |

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

**The driver changed once during testing.** The head ran 580.173.02 until
2026-09-22T23:38-0400, while the worker already ran 580.178.04. Rows from that
first day ran on mismatched driver point releases. Every row after it ran on
580.178.04 on both nodes.

### The client

The coding agent never runs on the cluster. It runs on a separate machine on
the LAN and calls the head node's OpenAI-compatible API ([#562][i562],
[#649][i649]), so the
nodes' memory holds only the server.

| dates | client machine | client image |
|---|---|---|
| 2026-09-22 → 2026-09-30, 06:41 | Intel Core i3-7100, 2 cores, 16 GB | OpenCode 1.18.31 → 1.18.33, uv 0.12.13 → 0.12.21, CPython 3.14.4 → 3.14.7 |
| 2026-09-30, 10:12 → 2026-10-03 | Intel Core i9-13900H laptop, 20 threads, 64 GB, Linux 7.0.0-34-generic | `local-llm-client:1.18.34`: OpenCode 1.18.34, uv 0.12.21, CPython 3.14.7 |
| 2026-10-05, 12:44 → 16:38 | Intel Core i3-7100, 2 cores, 16 GB (#912 arm A on recipe v1.7 only) | `local-llm-client:1.18.34`: OpenCode 1.18.34, uv 0.12.23, CPython 3.14.8 |
| 2026-10-05, 18:25 → | Intel Core i9-13900H laptop, 20 threads, 64 GB | `local-llm-client:1.18.34`: OpenCode 1.18.34, uv 0.12.23, CPython 3.14.8 |

From 2026-09-22T23:48-0400 the client runs in a Docker container limited to
12 GiB, with the kernel enforcing the limit ([#683][i683]). 160 earlier rows ran
without it. Each trial runs inside a `bwrap`
sandbox. **The change of client machine did not move the times.** The jayleaton
TensorFold stack ran on both. Its median replay trial was 156 s on the i3 and
145 s on the i9. Its median hard-set trial was 355 s and 366 s.

**Rows from 2026-09-22 until the #968 fix ran with OpenCode's `grep` and
`glob` tools unavailable** ([#968][i968]). In the client image, all 731 `grep`
calls and all 3,829 `glob` calls in that period failed, so the model fell back
to `bash`. Arms within that period compare with each other, not with rows
after the fix. A row from the fixed image carries `ripgrep=` in its client
image, so the tables keep the two apart. Since the fix, the image build and
every batch run a tool self-test that refuses an image with a broken tool.

### The tasks

All tasks run against [gmail-archive][gmail-archive] at commit `56e55cc`, 3
trials each, with a limit of 1,800 s per agent step. Reasoning effort is `low`
everywhere except one arm that tests `high`.

- **Standard set:** 10 excision tasks. The harness deletes a function, and the
  agent must restore it from the tests.
- **Replay set ([#714][i714]):** 7 tasks. The agent rebuilds a real commit of
  60–600 changed lines from its tests.
- **Hard set ([#726][i726]), from 2026-09-26:** 7 tasks. Five carry **hidden
  tests** that the agent never sees. Two ask for one 1,643-line change that
  spans five commits; one of the two hides the test names.

Later runs launch replay and hard together, 14 tasks. Task details are in
[`benchmarks/agent/METHODOLOGY.md`](../../benchmarks/agent/METHODOLOGY.md).

### How to read the numbers

- **Passed** is the visible verdict. A trial fails if its suite fails, if it
  times out, or if the agent edited a test file to pass.
- **Sum of medians** is each task's median trial time, summed over the set: the
  time to run the set once at typical speed. Every trial counts, and a timeout
  counts at its full limit.
- **One run is not a result.** A 3-trial median carries ±28% ([#23][i23]), so
  two stacks need about a 56% gap on a task before it is real. A sum over 7 or
  14 tasks is steadier than one task, but we still ask for three runs before a
  firm claim.
- **Hidden tests** are counted per test, over the trials whose task holds tests
  out. A trial that produced no result drops out of the denominator, so a stack
  with many timeouts can show a high rate on few tests.
- Every number on this page comes from two committed scripts:

  ```sh
  L=hardware/Cortex-X925-128GB-GB10-x2/results.jsonl
  uv run python scripts/screen_stacks.py --results $L              # replay + hard, then each set
  uv run python scripts/screen_stacks.py --results $L --standard   # standard set
  uv run python scripts/report.py --results $L --backend <name>    # per task, hidden tests
  ```

## 2. The pick: GLM-5.3-Flash on TensorFold, MiaAI-Lab recipe

[`MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold`][r-tf-mia] runs
[TensorFold][tensorfold] v0.6.0 plus the recipe's patches on both nodes (70 at
v1.5, patch-set hash `9f73cca659a1`), in NVIDIA's
PyTorch container (`nvcr.io/nvidia/pytorch:26.07-py3`). It serves the TR3 4bpw
EXL3 checkpoint (`Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw` @`9eaebb7`; that repo
has left the Hub, so download it from its author,
[`brandonmusic/GLM-5.3-Flash-tr3-4bpw`][w-tr3] @`a5fee929`, whose 120 weight
shards, config and tokenizer are byte-identical by sha256 to `9eaebb7` (#937))
with the DFlash2 drafter ([`incoai/GLM-5.3-Flash-DFlash2`][w-dflash2]
@`bf582e4`), an FP8 KV cache shared by 4 requests, and a 1,048,576-token
window. We built the image locally (`PULL=0`).

| run | recipe | passed | replay, sum of medians | hard, sum of medians | hidden tests |
|---|---|---|---|---|---|
| **2026-10-03, 09:51 EDT** | **v1.5 @`1576746`** | **41/42** | **1,025.1 s** | **3,221.1 s** | **284/390 (72.8%)** |
| **2026-10-03, 13:51 EDT** | **v1.5 @`1576746`** | **42/42** | **997.4 s** | **3,074.4 s** | **281/390 (72.1%)** |
| **2026-10-03, 17:37 EDT** | **v1.5 @`1576746`** | **41/42** | **1,064.1 s** | **2,949.5 s** | **288/390 (73.8%)** |
| 2026-10-03, 02:02 EDT | v1.4 @`cf28cc4` | 42/42 | 1,119.8 s | 2,923.0 s | 289/390 (74.1%) |
| 2026-10-03, 05:36 EDT | v1.4 @`cf28cc4` | 40/42 | 1,126.5 s | 2,913.4 s | 287/390 (73.6%) |
| 2026-10-01, 14:10 EDT | v1.3 @`978b225` | 42/42 | 1,013.5 s | 2,871.6 s | 283/390 (72.6%) |
| 2026-10-01, 17:54 EDT | v1.3.2 @`92bf731` | 41/42 | 1,140.4 s | 3,053.2 s | 274/390 (70.3%) |
| 2026-10-01, 23:29 EDT | v1.3.2 @`92bf731` | 41/42 | 1,060.1 s | 3,050.2 s | 283/390 (72.6%) |

**Why v1.5, not v1.3 @`978b225`.** The two cannot be told apart. Over the 14
tasks, v1.5's three runs took 4,246.2 s, 4,071.8 s and 4,013.6 s (sum of
per-task medians over all three: 4,061.5 s); v1.3's one run took 3,885.1 s, a
4.5% gap, far inside the ~56% two 3-trial medians need. Every release from
v1.3 to v1.5 falls within 7%. v1.5 is the current release, it is the only one
with three runs, and the client's currency gate ([#820][i820]) will not run an
older commit again.

**Why it is the pick:**

- **It is the fastest stack measured, by a margin that clears the noise.** On
  the same checkpoint, vLLM took 178% of its replay time (1,802.3 s against
  1,013.5 s) and 145% of its hard-set time on vLLM's fastest run (4,177.3 s
  against 2,871.6 s). Over the 14 tasks, v1.5 took 62%, 60% and 59% of
  vLLM's 6,811.6 s in its three runs (4,246.2 s, 4,071.8 s and 4,013.6 s), and
  every earlier release of the recipe took 57–62%.
- **The speed comes from the engine, not the weights.** Same TR3 checkpoint,
  same client, same tasks. Only the engine changed.
- **Its pass rate is the best measured:** 124 of 126 trials over v1.5's three
  runs. Both misses were touched-tests guards: the suite passed, but the agent
  edited a test file, so the guard fails the trial. v1.3 and v1.3.2 also
  passed 124 of 126 together.
- **Its hidden-test rate is level with the rest:** 72.8%, 72.1% and 73.8%
  (853/1,170 over v1.5's three runs), against 68.8–76.7% for every other stack
  with comparable counts (section 3).
- **The runs agree.** Eight runs over five recipe commits, 2026-10-01 to 10-03,
  span 3,885.1 s to 4,246.2 s over the 14 tasks. v1.4 made a new checkpoint the
  recipe's default; this page kept TR3 4bpw @`9eaebb7` for every run. v1.5 adds
  eight decode streams and a serial stop (patches 0069 and 0070).

**What to keep in mind:**

- **The engine is new.** TensorFold has few users yet, and the recipe adds 70
  patches of its own, against a TensorFold release (v0.6.0) five releases
  behind the latest (v0.6.5).
- **Rows do not record the engine build** (`tensorfold_version=unknown`,
  [#320][i320]). The recipe commit and the patch-set hash above are the record.
- **No standard-set run.** No TensorFold stack has run the 10 excision tasks.

## 3. Every stack measured

### Replay + hard: what each stack is

| stack (backend name) | recipe | engine and image | weights | OpenCode | dates |
|---|---|---|---|---|---|
| **GLM TensorFold, MiaAI** (`glm53fexl3tfmiaaiv15dual2xrc`; earlier `glm53fexl3tfmiaaidual2xrc`, `…v132…`, `…v14…`) | [MiaAI-Lab TensorFold][r-tf-mia] v1.5 @`1576746`; earlier @`978b225`, @`92bf731`, @`cf28cc4` | TensorFold v0.6.0 + 70 patches at v1.5 (53 at v1.3), built locally | [TR3 4bpw][w-tr3] @`9eaebb7`, [DFlash2][w-dflash2] @`bf582e4` | 1.18.34 | 10-01 → 10-03 |
| GLM TensorFold, jayleaton (`glm53ftfjaydual2xrc`) | [jayleaton/glm53-tensorfold-spark][r-tf-jay] @`e9c8cbb` | TensorFold v0.3.4 + 57 patches | [neko-legends abliterated EXL3][w-neko] @`07135ec` (**different weights**) | 1.18.33 | 09-29 → 09-30 |
| … same, effort `high` (`…high`) | same @`ad63acf` | same | same | 1.18.34 | 10-01 |
| GLM vLLM NVFP4, kindlingai (`glm53fnvfp4kindlingdual2xrc`) | [kindlingai/glm-5.3-flash-gx10][r-kindling] @`c748079` | vLLM nightly `ddd6fbca148a` + kindlingai overlays, image `spark-glm53:v9` built locally | [nvidia/GLM-5.3-Flash-NVFP4][w-glm-nvfp4] (**different weights**), [DFlash2][w-dflash2] | 1.18.34 | 10-02 → 10-03 |
| **GLM vLLM, MiaAI** (`glm53fexl3dual2xrc*`) | [MiaAI-Lab vLLM][r-glm-vllm] @`c1b7d4c` → `0f49cfd` → `943912c` → `94ae731` | `ghcr.io/miaai-lab/glm-5.3-flash-2x-dgx-sparks:exl3-instanttensor`, digest `447114ee` from 09-28; vLLM `0.1.dev20051+g487ecf187` | [TR3 4bpw][w-tr3] @`25a44fd`, [DFlash2][w-dflash2] @`dc77ff1` | 1.18.31 → 1.18.33 | 09-22 → 09-30 |
| Qwen3.8-Flash-Next hibrid48 (`qwen38fnhibrid48dual2xrc`) | [myllmbox/qwen38-flash-next-cluster-recipe][r-hibrid] v4.1 @`4e4747b` | `myllmbox/qwen38-flash-next-cluster-vllm:v6`, vLLM 0.30.0 + patches | [myllmbox/Qwen3.8-Flash-Next-hibrid48][w-hibrid] | 1.18.33 | 09-29 → 09-30 |
| Qwen3.8-Flash-Next NVFP4 (`qwen38fnnvfp4dual2xrcflags`) | [MiaAI-Lab Qwen dual][r-qwen] @`d2f54b7` | `vllm/vllm-openai:qwen38-flash-next`, vLLM `0.1.dev20073+g8e685d198` | [nvidia/Qwen3.8-Flash-Next-NVFP4][w-nvfp4] | 1.18.31, 1.18.32 | 09-22 → 09-28 |
| … on vLLM 0.30 (`…v030`) | same @`d23790b` | `vllm/vllm-openai:v0.30.0` | same | 1.18.32 | 09-26 |
| Qwen3.8-Flash-Next FP8 (`qwen38fnfp8dual2xrc`) | same, `start-fp8.sh` | `vllm/vllm-openai:qwen38-flash-next` | [Qwen/Qwen3.8-Flash-Next-FP8][w-fp8] | 1.18.32 | 09-24 |
| DeepSeek-V4-Flash-Vision-Exp (`dsv4flashvisiondspark2xrc`) | [MiaAI-Lab DSpark][r-dsv4v] @`97e8733` | `ghcr.io/anemll/dspark-vllm-gx10:0.1.1`, pinned by digest | [deepseek-ai/DeepSeek-V4-Flash-Vision-Exp][w-dsv4v] @`86f746b3` | 1.18.31, 1.18.32 | 09-22 → 09-28 |
| DeepSeek V4.1 Flash EXL3 (`dsv41fexl3dual2xrc`) | [MiaAI-Lab DSV4.1][r-dsv41] @`6f7d159` | built locally on `vllm/vllm-openai:deepseekv41-flash-0909`; vLLM `0.1.dev20904+g179dd0fa9` | [EXL3 2.9bpw][w-dsv41] @`64ba41b` + Engram tables from [DeepSeek-V4.1-Flash][w-dsv41-base] @`dba1be0` | 1.18.31 → 1.18.34 | 09-22 → 10-01 |
| MiMo-V2.6-Flash (`mimo26fdual2xrc*`) | [MiaAI-Lab MiMo][r-mimo] @`201be3e` | SGLang `nightly-dev-cu13-20260921-0f6761b5` | [XiaomiMiMo/MiMo-V2.6-Flash-RL][w-mimo] @`3b38d06` | 1.18.31, 1.18.32 | 09-22 → 09-25 |
| Ling-3.0-flash FP8 (`ling30fdual2xrc`) | own launcher | SGLang `nightly-dev-cu13-20260921-0f6761b5` | [inclusionAI Ling-3.0-flash FP8][w-ling] @`88d48c5` | 1.18.32 | 09-25 |

Every backend's full settings (KV type, drafter, memory fraction, context) are
in its `[backend.*]` entry in
[`benchmarks/agent/tasks.toml`](../../benchmarks/agent/tasks.toml). The vLLM
versions above were read from inside each image on 2026-10-01; the rows'
`engine_version` field is wrong for this cluster ([#904][i904]).

**Recipe commits are the commit each backend was registered at.** We run every
recipe at its newest commit ([#820][i820]), so a later run of the same backend
may have run a newer commit. The rows do not record the recipe commit. Where it
matters, this page names both.

### Replay set: 7 tasks

| stack | runs | passed | sum of medians | % of v1.3 @`978b225` | screen |
|---|---|---|---|---|---|
| **GLM TensorFold, MiaAI v1.5** @`1576746` (the pick) | 3 | 63/63 | **1,017.2 s** | 100% | keep |
| GLM TensorFold, MiaAI v1.3 @`978b225` | 1 | 21/21 | 1,013.5 s | 100% | keep |
| GLM TensorFold, MiaAI v1.4 @`cf28cc4` | 2 | 42/42 | 1,124.0 s | 111% | keep |
| GLM TensorFold, MiaAI v1.3.2 @`92bf731` | 2 | 42/42 | 1,125.0 s | 111% | keep |
| GLM TensorFold, jayleaton | 3 | 62/63 | 1,183.3 s | 117% | keep |
| GLM vLLM NVFP4, kindlingai | 3 | 63/63 | 1,301.6 s | 128% | keep |
| GLM vLLM @`943912c` | 2 | 39/42 | 1,802.3 s | 178% | cut |
| GLM vLLM @`0f49cfd` | 3 | 63/63 | 1,901.1 s | 188% | cut |
| GLM TensorFold, jayleaton, effort `high` | 1 | 21/21 | 1,916.8 s | 189% | cut |
| Qwen3.8-Flash-Next hibrid48 | 1 | 21/21 | 2,545.7 s | 251% | screened out |
| DeepSeek-V4-Flash-Vision-Exp | 3 | 63/63 | 2,757.7 s | 272% | screened out |
| Qwen3.8-Flash-Next NVFP4 | 3 | 63/63 | 2,954.3 s | 291% | screened out |
| DeepSeek V4.1 Flash EXL3 | 4 | 84/84 | 3,155.9 s | 311% | screened out |
| Qwen3.8-Flash-Next NVFP4, vLLM 0.30 | 1 | 21/21 | 3,157.4 s | 312% | screened out |
| Qwen3.8-Flash-Next FP8 | 1 | 21/21 | 3,823.1 s | 377% | screened out |
| MiMo-V2.6-Flash, thinking on | 1 | 11/19 | 7,891.4 s | 779% | screened out |
| Ling-3.0-flash FP8 | 1 | 3/4 | — | — | stopped early |

### Hard set: 7 tasks, 5 with hidden tests

| stack | runs | passed | sum of medians | % of v1.3 @`978b225` | hidden tests passed | screen |
|---|---|---|---|---|---|---|
| **GLM TensorFold, MiaAI v1.5** @`1576746` (the pick) | 3 | 61/63 | **3,044.3 s** | 106% | 853/1,170 (72.9%) | keep |
| GLM TensorFold, MiaAI v1.3 @`978b225` | 1 | 21/21 | 2,871.6 s | 100% | 283/390 (72.6%) | keep |
| GLM TensorFold, MiaAI v1.4 @`cf28cc4` | 2 | 40/42 | 2,945.4 s | 103% | 576/780 (73.8%) | keep |
| GLM TensorFold, MiaAI v1.3.2 @`92bf731` | 2 | 40/42 | 3,036.8 s | 106% | 557/780 (71.4%) | keep |
| GLM TensorFold, jayleaton | 3 | 61/63 | 3,464.6 s | 121% | 746/1,084 (68.8%) | keep |
| GLM vLLM NVFP4, kindlingai | 3 | 57/63 | 3,570.5 s | 124% | 873/1,170 (74.6%) | keep |
| GLM vLLM @`94ae731`, output cap 65,536 | 1 | 20/21 | 4,177.3 s | 145% | 296/390 (75.9%) | keep |
| GLM vLLM @`943912c` | 2 | 39/42 | 5,009.3 s | 174% | 210/292 (71.9%) | cut |
| GLM vLLM @`0f49cfd` | 2 | 40/42 | 5,338.0 s | 186% | 497/682 (72.9%) | cut |
| Qwen3.8-Flash-Next hibrid48 | 1 | 20/21 | 5,827.8 s | 203% | 277/361 (76.7%) | screened out |
| Qwen3.8-Flash-Next NVFP4 | 2 | 40/42 | 7,077.0 s | 246% | 556/734 (75.7%) | screened out |
| DeepSeek-V4-Flash-Vision-Exp | 2 | 40/42 | 8,085.6 s | 282% | 493/682 (72.3%) | screened out |
| DeepSeek V4.1 Flash EXL3 | 2 | 28/34 valid | 8,124.9 s | 283% | 418/494, 3 trials with no result | screened out |
| GLM TensorFold, jayleaton, effort `high` | 1 | 15/21 | 5,849.0 s | 204% | 190/216, 5 trials with no result | screened out |

**Hidden tests do not separate the stacks.** Every stack with full counts lands
between 68.8% and 76.7%. The Qwen arms sit 3–5 points above the GLM arms. Tests
in one trial are not independent, so that gap is inside the noise. No stack
passes the hidden suite of a whole task often: 0–4 of 21 trials per run. The
two highest rates, DSV4.1 and effort `high`, come from trials that timed out
and dropped out of the count, so they do not rank.

DSV4.1 has 4 hard-set rows excluded for answer exposure ([#54][i54]): the agent
opened the directory of earlier trials' solutions.

### Standard set: 10 excision tasks

| stack | runs | passed | sum of medians | screen |
|---|---|---|---|---|
| **GLM vLLM** @`0f49cfd` | 2 | 60/60 | **446.9 s** | keep |
| GLM vLLM, first arm (client context 65,536) | 1 | 30/30 | 514.8 s | keep |
| DeepSeek V4.1 Flash EXL3 | 3 | 90/90 | 734.1 s | keep |
| DeepSeek-V4-Flash-Vision-Exp | 4 | 100/100 | 763.5 s | keep |
| Qwen3.8-Flash-Next NVFP4, no flags | 1 | 30/30 | 780.9 s | keep |
| Qwen3.8-Flash-Next FP8 | 1 | 30/30 | 812.2 s | cut |
| Qwen3.8-Flash-Next NVFP4, two-node flags | 2 | 60/60 | 851.6 s | cut |
| MiMo-V2.6-Flash, thinking off | 1 | 23/28 | 2,150.2 s | screened out |
| MiMo-V2.6-Flash, thinking on | 2 | 60/60 | 2,598.1 s | screened out |

This set is saturated: every main stack passes every trial. It separates the
stacks on speed only. No TensorFold stack ran it.

### Screened out

Two [#762][i762] rules decide which stacks earn more runs.

- **After a run**, `screen_stacks.py` gives the verdict in the tables above. A
  stack needs 90% or more passed and a sum of medians under 200% of the
  leader's, or it is "screened out". It then keeps the fastest 3, plus any
  stack within 25% of the third, up to 5. The rest are "cut": fine, but not
  worth more runs.
- **During a run**, the early stop ends it when its failures reach 30% of the
  leader's pass count, scaled to the run, or its time reaches 200% of the
  leader's on the same trials.

The verdicts are against today's leader. Most replay and hard-set runs finished
before the pick existed, and their numbers stand. They simply no longer earn
more runs. These stacks were dropped for a reason beyond the screen:

| stack | why | issue |
|---|---|---|
| MiMo-V2.6-Flash | 11 of 19 on replay: 6 timeouts and 2 collection errors. Slowest on every standard task. | [#717][i717] |
| Ling-3.0-flash FP8 | 507% of GLM vLLM's time on the first 4 replay tasks; stopped at 3 of 4 passed. | [#752][i752] |
| Qwen3.8-Flash-Next FP8 | 377% of the pick's replay time. NVFP4 is the same model and took 77% of FP8's replay time. | [#717][i717] |
| TensorFold v0.3.5 on a GLM 4-bit MLX build | The largest context that fit was about 36k tokens, too small for 19 of 63 replay trials. | [#798][i798] |
| GLM TensorFold, effort `high` | 15 of 21 on the hard set. Span tasks 0 of 6. | [#840][i840] |

## 4. What ten days of data show

- **The engine matters more than the quantization.** The same TR3 weights took
  57% of the time on TensorFold that they took on vLLM. No change of
  quantization came close: Qwen NVFP4 took 77% of the FP8 build's replay time.
- **Decode speed does not predict agent time.** The pick generates output at
  42–43 s per 1,000 tokens. DSV4-Vision does it in 27–30 s and jayleaton's
  TensorFold in 29–31 s, yet the pick finishes the tasks first. Agent wall time
  is mostly re-reading context: prefill and prefix caching. The full table is
  in [`docs/results.md`](../../docs/results.md#cortex-x925-128gb-gb10-x2).
- **Reasoning effort `low` beats `high`.** On jayleaton's TensorFold stack,
  `high` took 171% of `low`'s time on the 12 non-span tasks (4,165.8 s against
  2,441.0 s). It passed 0 of 6 span trials against 6 of 6
  ([#840][i840], [#894][i894]). GLM's chat template knows only `low`, `high`
  and `max`, and falls back to `max` when nothing is set. Set `low`
  explicitly.
- **Newer recipes helped GLM on vLLM.** Its hard-set time fell from 5,338.0 s
  (@`0f49cfd`) to 4,177.3 s (@`94ae731`, with OpenCode 1.18.33 and a larger
  output cap; one run). Newer vLLM did not help Qwen NVFP4:
  3,157.4 s on vLLM 0.30 against 2,954.3 s before.
- **Two task traps cost some trials on every stack:**
  - gmail-api-sources builds a source from `tests/fixtures/simple.mbox`, which
    the target repository's `.gitignore` excludes. An agent that reads the
    file early fails one visible test.
  - OpenCode's per-step output cap cut a few span-task steps at 16,384 tokens.
    The pick's recipe sets 32,768, and vLLM GLM ran at 65,536 with no step
    near the cap.
- **The early stop was off for every 14-task launch** until 2026-10-01
  ([#900][i900], fixed in [#902][pr902]). No row is wrong; some screening ran
  longer than it had to.

## 5. How to run the pick

On the head node, as the user that owns Docker:

1. Clone the recipe at its latest commit:

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
   MODEL_ID=brandonmusic/GLM-5.3-Flash-tr3-4bpw
   MODEL_REVISION=a5fee929cf4888b1824323e33e8a19b60129e025
   ```

   Set `MODEL_ID` and `MODEL_REVISION`. Without them the recipe downloads its
   own default, the TensorFold quant, which is not the pick (#912).
3. Run `./scripts/prepare.sh`. It downloads the weights named in `local.sh` and
   the drafter on both nodes (about 176 GB each), builds the image, and checks
   that both nodes hold the same image.
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
8.7–10.6 GiB. v1.3.2 holds about 1.5 GiB more than v1.3, for its larger
prompt-state cache. Keep earlyoom at 1.0 / 0.5 GiB on both nodes.

**The fallback** is the same weights on the [MiaAI-Lab vLLM recipe][r-glm-vllm]:
set `GLM53_DEFAULT_REASONING_EFFORT=low` and `MAX_MODEL_LEN=262144`, keep the
recipe's other defaults, and run `./start.sh`. Gotcha 23 in
[`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md#23-a-large-models-default-reasoning-effort-eats-the-whole-token-budget--hit)
explains why the effort setting matters.

## 6. Tool versions, checked 2026-10-01

| tool | used for these results | latest on 2026-10-01 | status |
|---|---|---|---|
| OpenCode | 1.18.31 → 1.18.34 | [1.18.34][opencode-rel] | current |
| uv, in the client image | 0.12.13 → 0.12.21 | [0.12.22][uv-rel], released 2026-10-01T20:20-0400 | **behind**; the client gate refuses a batch until the image moves |
| CPython, in the client image | 3.14.4 → 3.14.7 | [3.14.8][py-rel] | **behind**; same gate |
| vLLM | dev builds in the recipe images; 0.30.0 in two | [0.30.0][vllm-rel] | current where the recipe allows |
| TensorFold | v0.6.0 (pick), v0.3.4 (jayleaton) | [v0.6.5][tf-rel], released 2026-10-03 (checked 2026-10-03) | the pick's recipe pins v0.6.0 |
| MiaAI TensorFold recipe | @`978b225` → `1576746` (v1.5) | @`a152824` (checked 2026-10-03; CHANGELOG only since v1.5) | current |
| MiaAI vLLM GLM recipe | @`94ae731` at its last run | @`6278ecb` | behind; not re-run |
| MiaAI Qwen dual recipe | @`d23790b` at its last run | @`cd839d0` | behind; not re-run |
| DGX OS | OTA 7.6.0 | 7.6.0 | current |
| NVIDIA driver | 580.178.04 | 580.178.04 in the 580 branch | current; see below |
| Docker / Container Toolkit | 29.6.2 / 1.20.1 | same | current |
| Ubuntu userland | — | 22 updates pending on each node (Mesa, GStreamer, gvfs, OpenVPN, sosreport) | **to apply** |

Driver branches 590, 595 and 610 (610.57.04) are in the Ubuntu archive. DGX OS
7.6.0 ships the 580 branch, and every image here was built and measured on it.
We did not move to a newer branch. That would need a reboot of both nodes and a
re-run of the pick.

## 7. Open, and what would change this page

- **[#896][i896]:** GLM-5.3-Flash on NVIDIA's own NVFP4 weights, served by vLLM
  nightly with [kindlingai's launcher][r-kindling]. Measured 2026-10-02 →
  10-03 over 3 runs: 120 of 126 trials passed (95.2%, 95% CI 90.0–97.8%), in
  4,872.1 s against the pick's 3,885.1 s (125%). The screen keeps it at rank 4,
  4.8% behind jayleaton's TensorFold stack. It does not change the pick. Its
  hidden-test rate on the hard set, 74.6%, is level with the other stacks.
- **[#897][i897]:** MiaAI-Lab's announced two-Spark TensorFold recipe for
  Qwen3.8-Flash-Next. If TensorFold does for Qwen what it did for GLM, Qwen's
  slightly higher hidden-test rate could make it the pick. Approved
  2026-10-01; it runs once the recipe is published.
- **TensorFold v0.6.5** is out (2026-10-03), five releases past the v0.6.0
  the recipe pins. The pick moves when its recipe does. The recipe's 70
  patches do not carry over: applied in order to v0.6.5, 28 fail and its build
  stops at patch 0002. Only one (0058) is already upstream. We wait for the
  recipe to rebase rather than port the patches ourselves (operator,
  2026-10-03).
- **[#904][i904]:** rows should record the serving engine's version.
- **Not measured:** several clients at once, and code quality beyond the tests.

## References

**Recipes:**
[MiaAI-Lab TensorFold GLM][r-tf-mia] ·
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
[#894][i894] effort `high` · [#900][i900] early stop ·
[#896][i896] · [#897][i897] · [#904][i904]

[r-tf-mia]: https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold
[r-glm-vllm]: https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks
[r-tf-jay]: https://github.com/jayleaton/glm53-tensorfold-spark
[r-hibrid]: https://github.com/myllmbox/qwen38-flash-next-cluster-recipe
[r-qwen]: https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Dual-DGX-Sparks
[r-dsv4v]: https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark
[r-dsv41]: https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-EXL3-2x-DGX-Sparks
[r-mimo]: https://github.com/MiaAI-Lab/MiMo-V2.6-Flash-2x-DGX-Sparks
[r-kindling]: https://github.com/kindlingai/glm-5.3-flash-gx10
[w-tr3]: https://huggingface.co/brandonmusic/GLM-5.3-Flash-tr3-4bpw
[w-dflash2]: https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2
[w-neko]: https://huggingface.co/neko-legends/GLM-5.3-Flash-Uncensored-EXL3
[w-glm-nvfp4]: https://huggingface.co/nvidia/GLM-5.3-Flash-NVFP4
[w-hibrid]: https://huggingface.co/myllmbox/Qwen3.8-Flash-Next-hibrid48
[w-nvfp4]: https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4
[w-fp8]: https://huggingface.co/Qwen/Qwen3.8-Flash-Next-FP8
[w-dsv4v]: https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp
[w-dsv41]: https://huggingface.co/Mia-AiLab/DeepSeek-V4.1-Flash-EXL3-2.9bpw
[w-dsv41-base]: https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
[w-mimo]: https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL
[w-ling]: https://huggingface.co/inclusionAI/Ling-3.0-flash-FP8
[tensorfold]: https://github.com/ashhart/TensorFold
[tf-rel]: https://github.com/ashhart/TensorFold/releases
[vllm]: https://github.com/vllm-project/vllm
[vllm-rel]: https://github.com/vllm-project/vllm/releases
[sglang]: https://github.com/sgl-project/sglang
[opencode]: https://github.com/anomalyco/opencode
[opencode-rel]: https://github.com/anomalyco/opencode/releases
[uv-rel]: https://github.com/astral-sh/uv/releases
[py-rel]: https://www.python.org/downloads/
[gmail-archive]: https://github.com/evanwtf/gmail-archive
[i23]: https://github.com/evanwtf/local-llm/issues/23
[i54]: https://github.com/evanwtf/local-llm/issues/54
[i320]: https://github.com/evanwtf/local-llm/issues/320
[i562]: https://github.com/evanwtf/local-llm/issues/562
[i649]: https://github.com/evanwtf/local-llm/issues/649
[i683]: https://github.com/evanwtf/local-llm/issues/683
[i820]: https://github.com/evanwtf/local-llm/issues/820
[i700]: https://github.com/evanwtf/local-llm/issues/700
[i711]: https://github.com/evanwtf/local-llm/issues/711
[i714]: https://github.com/evanwtf/local-llm/issues/714
[i717]: https://github.com/evanwtf/local-llm/issues/717
[i726]: https://github.com/evanwtf/local-llm/issues/726
[i752]: https://github.com/evanwtf/local-llm/issues/752
[i762]: https://github.com/evanwtf/local-llm/issues/762
[i798]: https://github.com/evanwtf/local-llm/issues/798
[i840]: https://github.com/evanwtf/local-llm/issues/840
[i892]: https://github.com/evanwtf/local-llm/issues/892
[i894]: https://github.com/evanwtf/local-llm/pull/894
[i896]: https://github.com/evanwtf/local-llm/issues/896
[i897]: https://github.com/evanwtf/local-llm/issues/897
[i900]: https://github.com/evanwtf/local-llm/issues/900
[pr902]: https://github.com/evanwtf/local-llm/pull/902
[i904]: https://github.com/evanwtf/local-llm/issues/904
[i968]: https://github.com/evanwtf/local-llm/issues/968
