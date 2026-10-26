# What to run on the dual DGX Spark cluster

> **Ledger read 2026-10-08, 00:30 EDT.** 2,298 rows in
> [`results.jsonl`](results.jsonl), from 2026-09-22T04:13-0400 to
> 2026-10-07T23:34-0400: 16 days of testing, 29 stack configurations. This page
> was rewritten from scratch on 2026-10-08. The previous version (2026-10-07)
> is in git history. **Updated 2026-10-08, 19:40 EDT** with [#997][i997]: the
> pick on recipe v1.10. **Audited 2026-10-10** against the ledger: 2,429 rows,
> to 2026-10-08T18:34-0400. The 131 rows since the 00:30 read are #997's 126
> and 5 from [#999][i999] (section 4, "Dropped"). **Updated 2026-10-10,
> 20:00 EDT** with [#1018][i1018]: the pick on recipe `main` @`b5808ac`, 126
> rows, to 2026-10-10T19:52-0400 (2,555 rows in all). **Updated 2026-10-13**
> with 70 rows to 2026-10-13T01:11-0400 (2,625 in all): [#865][i865] arm 1 on
> vLLM 0.31.0 and on the vLLM main nightly (14 rows each), and [#919][i919],
> DeepSeek V4.1 on TensorFold Zig (42 rows). The pick does not change. Every pass count, sum of
> medians, percentage, hidden-test count and screen verdict in sections 2–4
> reproduces from the commands in section 1 and, for the per-run figures,
> `scripts/series_readout.py` on the saved row files of #970, #997 and #1018.
>
> **Updated 2026-10-19 with the operator's decision of that day.** The pick
> moves from GLM-5.3-Flash to DeepSeek-V4.1-Flash-1017 on vLLM
> ([#1160][i1160]). GLM stays as the fast option. Ledger read 2026-10-19:
> 2,893 rows, the last one finished at 2026-10-19T18:37:35-0400. The 268 rows
> from 2026-10-18T14:39-0400 on ran OpenCode 2.0.32, and they compare only with
> each other (sections 2 and 3). The figures from before this update reproduce
> when the section 3 command gets `--before 2026-10-18T00:00:00-0400`.
>
> **Audited 2026-10-24.** Ledger read: 3,059 rows, the last one finished at
> 2026-10-24T12:54:55-0400. Every figure carried from 2026-10-19 reproduces.
> The 166 rows since then add six stack configurations, and none of them moves
> the pick or the fast option:
> - [#1128][i1128], GLM FP4-Local on vLLM: replay + hard on OpenCode 2.1.2.
> - Five standard-set runs on OpenCode 2.1.4: the fast option again, GLM on
>   TensorFold's native engine ([#1095][i1095]), Nemotron-3.5-Super-VL
>   ([#1231][i1231]), MiMo-V2.6-Flash-RL ([#1032][i1032]) and
>   DeepSeek-V4-Flash-Vision-Exp ([#1097][i1097]).
>
> OpenCode 2.1.x did not move the leader: GLM's standard set took 262.4 s on
> 2.1.4 and 266.6 s on 2.0.32. So the 2.1.x rows rank with the 2.0.32 rows
> (sections 2 and 3). Section 7 lists what moved upstream since the
> measurements.
>
> **Updated 2026-10-25.** Ledger read: 3,236 rows, the last one finished at
> 2026-10-25T22:23:33-0400. Three more DeepSeek-1017 runs on OpenCode 2.1.4,
> and none moves the pick (section 2):
> - [#1255][i1255], the pick's second run, on its measured v1.1 build: 40 of
>   40 visible, 361 of 370 hidden tests, and 85.7% of run 1's replay + hard
>   time. The pick reproduces. The operator ruled the same day that an older
>   build that wins stays the pick, so its recipe is pinned at `cc59c36`; the
>   client stays the latest.
> - [#1244][i1244], the pick's stack at its v2.0 release with sudoingx's
>   2.8 bpw weights: equal on the visible suite, behind on the hidden re-grade
>   and on time.
> - [#1246][i1246], #1168's stack again on one recipe commit: 281 of 281
>   hidden tests again, but 35 of 39 visible and 143.1% of the pick's time.
>
> **The ledger has two halves that do not compare.** Until 2026-10-06 09:14 EDT,
> OpenCode's `grep` and `glob` tools failed on every call in the client
> ([#968][i968]): 1,993 rows. After the fix: 632 rows. A transcript audit of
> every post-fix trial on the client ([#969][i969], 2026-10-13) read 632
> trials and found no harness fault. Section 3 ranks the
> stacks measured after the fix; section 4 keeps the earlier results, compared
> only with each other.

## The short answer

| you want | run | evidence |
|---|---|---|
| **A coding agent (the pick)** | **DeepSeek-V4.1-Flash-1017 EXL3 2.7 bpw ([0xSero][w-sero1017]) on vLLM + ExLlamaV3**, [sudoingX stack][r-sudoingx] @`cc59c360` with the [sybil-solutions recipe][r-sybil] ([#1160][i1160]) | The best correctness with no model fault and no server fault. Replay + hard: 41 of 41 counted (1 excluded), sum of medians 4,782.4 s. Hidden re-grade ([#726][i726]): 17 of 20 whole tasks, 369 of 380 tests (97.1%). Standard set: 30 of 30, sum of medians 504.4 s. One run of each set, on OpenCode 2.0.32. |
| The fast option | **GLM-5.3-Flash EXL3 4bpw TensorFold quant on TensorFold v0.6.0**, [MiaAI-Lab recipe][r-tf-mia] (the pick until 2026-10-19; [#1095][i1095], [#1018][i1018]) | On the same client: standard set 30 of 30 in 266.6 s, 52.9% of the pick's time. A second standard run on OpenCode 2.1.4 took 262.4 s. Replay + hard 42 of 42 in 4,358.7 s, 91.1% of the pick's time. **But it misses held-out tests:** 1 of 21 whole hidden tasks, 284 of 390 hidden tests (72.8%). Use it when the visible test suite covers the whole job. |
| The most hidden tests passed | DeepSeek-V4.1-Flash-1017 EXL3-EAQ-v1 2.9 bpw on TensorFold, [MiaAI-Lab recipe][r-dsv41-1017-mia] ([#1168][i1168]) | Hidden: 19 of 21 whole tasks, 361 of 361 tests (100.0%), 1 trial with no result. Visible: 38 of 42. Two trials stopped on the first turn with no tool call, one timed out, and one edited a test file. Replay + hard 6,226.4 s, 130.2% of the pick's time. The standard set hit the early time stop after round 1: 10 of 10, 660.4 s. A second run on one recipe commit ([#1246][i1246], 2026-10-25) re-graded 281 of 281 hidden tests, with 35 of 39 visible and one first-turn stop in 42. |
| Image input, or more than 262k tokens of context | Images: DeepSeek-V4-Flash-Vision-Exp on vLLM, [MiaAI-Lab DSpark recipe][r-dsv4v]. Text only: DeepSeek-1017 on TensorFold ([#1142][i1142] or [#1168][i1168]) | Vision-Exp: 1M context, vision. Standard set on OpenCode 2.1.4 ([#1097][i1097]): 10 of 10 in 871.2 s, 332.0% of GLM's 262.4 s on the same client, stopped early after round 1. On the broken client, 63 of 63 replay trials, at 272% of TensorFold's replay time (section 4). It has no replay + hard run on the fixed client. The pick on vLLM serves 262,144 tokens. On TensorFold, #1142's profile serves 1,048,576 tokens, and #1168's recipe has a 1M-token pool with 512K a stream as its stated practical limit. Our client sends at most 262,144, so long context is not measured. DeepSeek-1017 does not take images. |
| One Spark, not two | See the [single-Spark picks](../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md) | Qwen3.8-Flash-Next NVFP4 fits one node. |

**TR3 4bpw is off this table.** It was "the measured alternative" until
2026-10-19: the GLM pick's recipe with another checkpoint, 122 of 126 and
4,271.0 s on v1.8. The fast option now fills that place. Its figures stay in
sections 2 and 3.

**Why the pick moved.** The operator's rule: "Pure correctness has to win, but
saving ~50% on time is also very valuable." On the visible suites, the stacks
cannot be told apart: every 95% interval overlaps. The hidden re-grade does
separate them. The three DeepSeek-1017 arms pass 97.1% to 100.0% of the
held-out tests, and GLM passes 72.8%. Among the 1017 arms, #1160 is the one
with no model fault, no server fault and one excluded row. Each 1017 arm has
one run, so the order among them is not firm (section 2). One caveat: #1160
and #1168 wrote back 43.0% and 43.5% of the replay files verbatim, against
1.0% for GLM. Part of the hidden-test lead may be recall, not new code
(section 1, "Recall").

**Not recommended for coding on this cluster: Qwen3.8-Flash-Next.** Every
Qwen stack passes nearly every trial, but none finishes the set in less than
188% of GLM's time on v1.8 (202% on `main`). The time goes to reasoning: a
median of 14,000 to 16,800 reasoning tokens per trial, against GLM's 2,800
to 3,500 (section 3). RedHat's NVFP4 build ([#865][i865]) does not load on two
Sparks with vLLM main: three launches ran out of memory.

**What "best" means here.** Each stack runs [OpenCode][opencode] as a coding
agent on real tasks, and must pass the task's test suite. We rank on
correctness first: the visible verdict and the hidden re-grade ([#726][i726]).
Then we rank on wall-clock time. Code quality beyond "passes the tests" is not
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
| 2026-10-06, 09:14 → 2026-10-13, 01:11 | Intel Core i9-13900H laptop | **the #968 fix:** `local-llm-client:1.18.34`, then `:1.18.35` from 16:54: OpenCode 1.18.34 → 1.18.35, uv 0.12.23 → 0.13.0, CPython 3.14.8, ripgrep 15.2.0. #919's second run (2026-10-12/13) used OpenCode 1.19.0 |
| **2026-10-18, 14:39 →** | **Intel Core i9-13900H laptop** | **OpenCode 2.0.32**, uv 0.13.1 (0.13.2 for #1168's standard set), CPython 3.14.8, ripgrep 15.2.0 |
| 2026-10-21 | Intel Core i9-13900H laptop | OpenCode 2.1.2 (#1128 only) |
| **2026-10-24 →** | **Intel Core i9-13900H laptop** | **OpenCode 2.1.4**. The fast option's standard set took 262.4 s here and 266.6 s on 2.0.32 (98.4%), so the 2.1.x rows rank with the 2.0.32 rows |

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
- **95% CI** is the Wilson interval of the pass rate ([#866][i866]). Where two
  stacks' intervals overlap, the pass rates do not rank them.
- **Hidden tests** are counted per test, over the trials whose task holds tests
  out. Tests in one trial are not independent, and a trial with no result drops
  out of the count.
- **Recall.** [gmail-archive][gmail-archive] is public, so a model may have
  seen the commits a replay task rebuilds. "Replay files restored verbatim"
  counts the reverted files the agent wrote back byte-for-byte, over every
  trial. It stays between 0.6% and 8.1% for every GLM and Qwen stack that ran
  all 14 tasks: the passes are new code, not recalled files. The exception is
  DeepSeek. On EXL3, V4.1 restored every file of a task verbatim in 6 trials,
  and on TensorFold Zig ([#919][i919]) it restored 81 of 268 files (30.2%).
  DeepSeek-1017 restored 131 of 305 (43.0%) on vLLM ([#1160][i1160]) and 127
  of 292 (43.5%) on TensorFold ([#1168][i1168]).
- **The screen ([#762][i762]).** A stack needs 90% or more passed and a sum of
  medians under 200% of the leader's, or it is "screened out". Then the fastest
  3 are kept, plus any within 25% of the third, up to 5. The rest are "cut":
  fine, but not worth more runs. During a run, an early stop ends it when its
  time reaches 200% of the leader's on the same trials.
- Every number on this page comes from committed scripts:

  ```sh
  L=hardware/Cortex-X925-128GB-GB10-x2/results.jsonl
  CUT=2026-10-06T09:14:00-0400                                       # the #968 fix
  OC2=2026-10-18T00:00:00-0400                                       # OpenCode 2.0.32
  uv run python scripts/screen_stacks.py --results $L --since $CUT --before $OC2  # section 3, 1.18.x
  uv run python scripts/screen_stacks.py --results $L --since $OC2   # sections 2 and 3, 2.0.32
  uv run python scripts/screen_stacks.py --results $L --since $OC2 --standard
  uv run python scripts/screen_stacks.py --results $L --before $CUT  # section 4
  uv run python scripts/screen_stacks.py --results $L --before $CUT --standard
  uv run python scripts/report.py --results $L --backend <name> \
      --since 2026-10-06T13:14:00+0000                               # per task, hidden tests
  ```

  `report.py` compares its bounds as strings, so give them in UTC (`+0000`),
  the form the rows use ([#992][i992]). For GLM's 2.0.32 rows, use
  `--since 2026-10-19T09:00:00+0000`, and for its 1.18.35 rows,
  `--until 2026-10-19T09:00:00+0000`. Without a bound, the screen pools GLM's
  two clients (168 of 168, 3,847.9 s). Do not quote that pooled figure. On
  the standard set, `--since $OC2` pools GLM's 2.0.32 and 2.1.4 runs (60 of
  60, 262.6 s); add `--before 2026-10-20T00:00:00-0400` or
  `--since 2026-10-24T00:00:00-0400` to read one client. The
  2026-10-19 hidden-test figures come from `report.py` on the client machine,
  at harness `c65086b`.

## 2. The pick: DeepSeek-V4.1-Flash-1017 on vLLM, and the GLM fast option

### Four stacks on OpenCode 2.0.32

Every row from 2026-10-18T14:39-0400 on ran OpenCode 2.0.32 on the i9 client.
These rows compare with each other, not with the 1.18.x rows. Each stack ran
one replay + hard run and one standard run, with the early stop on. All three
DeepSeek-1017 arms serve the same checkpoint,
`deepseek-ai/DeepSeek-V4.1-Flash-1017` @`1bae0e10`, with its native Engram
shards 47 and 48. The quants differ.

| | **DeepSeek-1017 on vLLM** ([#1160][i1160]), the pick | DeepSeek-1017 EAQ-v1 on TensorFold ([#1168][i1168]) | DeepSeek-1017 on TensorFold Zig ([#1142][i1142]) | GLM on TensorFold ([#1095][i1095]), the fast option |
|---|---|---|---|---|
| backend | `dsv41f1017exl3serodual2xrc` | `dsv41f1017eaqtfmiaaidual2xrc` | `dsv41f1017tfzigjaydual2xrc` | `glm53fexl3tfqmiaai20261010dual2xrc` |
| weights | [0xSero EXL3 2.7 bpw][w-sero1017] @`e34ef55bda` | [Mia-AiLab EXL3-EAQ-v1 2.9 bpw][w-eaq1017] @`6d54be4fca` | jayleaton EXL3 2.8 bpw @`9e7c02ec06` | [Mia-AiLab 4bpw TensorFold quant][w-tfq] @`76c0b517` |
| replay + hard, passed by verdict | **41/41** (1 excluded) | 38/42 | 34/35 (7 excluded) | 42/42 |
| 95% CI (Wilson) | 91.4–100% | 77.9–96.2% | 85.5–99.5% | 91.6–100% |
| replay + hard, sum of medians | 4,782.4 s | 6,226.4 s | no 14-task sum: every `gmail-api-sources-hidden` trial was excluded | **4,358.7 s** |
| hidden tests: whole tasks | 17/20 | **19/21**, 1 no result | 12/14, 1 no result | 1/21 |
| hidden tests: tests | 369/380 (97.1%) | **361/361 (100.0%)** | 270/274 (98.5%) | 284/390 (72.8%) |
| replay files restored verbatim | 131/305 (43.0%) | 127/292 (43.5%) | not in the screen | 3/309 (1.0%) |
| standard set, passed | 30/30 | 10/10, time-stopped after round 1 | 28/28 | 30/30 |
| standard set, sum of medians | 504.4 s | 660.4 s (one trial a task) | 463.8 s | **266.6 s** |

**How the rule ranks them.** Correctness first, then time.

- **Visible verdicts do not rank them.** Every interval overlaps the others.
- **The hidden re-grade does.** The three 1017 arms pass 97.1% to 100.0% of
  the held-out tests. GLM passes 72.8%, and 1 of 21 whole tasks. The gap is not
  new to this client: GLM's 126 rows on OpenCode 1.18.35 ([#1018][i1018])
  re-grade to 3 of 63 whole tasks and 864 of 1,170 tests (73.8%). That is four
  GLM runs that agree.
- **#1160 over #1168.** #1168 has the best hidden result, but 4 visible
  failures:
  - `replay-relative-dates` #2 (7.3 s) and `replay-defang` #2 (12.4 s): the
    model answered the first request with `finish=stop` and no tool call. The
    operator counts these as FAILs ([#1177][i1177]).
  - `replay-span-web-ui-no-test-names` #2 timed out at 1,800 s.
  - `replay-span-web-ui-no-test-names` #1 passed its suite in 1,213.6 s, but
    the agent edited a test file, so the verdict is FAIL.

  Its standard set ran on a newer recipe (`e65785c`) than its replay set
  (`2b906fd`). The early stop ended it after round 1: 660.4 s against GLM's
  274.4 s on the same trials (240.7%, limit 200%).
- **#1160 over #1142.** #1142's server crashed with `PoolExhausted` after 5 h
  7 min, and 7 of its 42 replay + hard rows were excluded (6 for answer
  exposure, 1 for a dead server). #1160 ran with no server fault and 1
  exclusion (answer exposure).
- **Time.** GLM took 52.9% of the pick's time on the standard set (266.6 s
  against 504.4 s), and 91.1% on replay + hard (4,358.7 s against 4,782.4 s).
  The large saving is on short tasks. On the long tasks the two are close.

**Keep the claim small.**

- **Two runs of the pick, one of each other 1017 arm.** We ask for three
  runs before a firm claim. The GLM gap rests on four runs. The pick has two
  that agree ([#1255][i1255]); the other 1017 arms have one each, and #1168's
  stack two.
- **Recall.** #1160 and #1168 wrote back 43% of the replay files verbatim,
  against 1.0% for GLM (section 1). The commits are public, so part of the
  hidden-test lead may be recall.
- **Quant, not only engine.** The three 1017 arms use 2.7, 2.8 and 2.9 bpw
  quants by three authors. They separate the engines only up to the quant.

**A fifth stack, measured since: [#1128][i1128], GLM-5.3-Flash FP4-Local on
vLLM** (`glm53ffp4localdual2xrc`, `nvidia/GLM-5.3-Flash-FP4-Local`, OpenCode
2.1.2). It does not change the order. It is a GLM build, and it re-grades
like one, but it is slower than the fast option:

| | #1128 | the fast option |
|---|---|---|
| replay + hard, passed | 41/42 (95% CI 87.7–99.6%) | 42/42 |
| replay + hard, sum of medians | 6,704.7 s (153.8%) | 4,358.7 s |
| hidden tests: whole tasks | 3/21 | 1/21 |
| hidden tests: tests | 283/390 (72.6%) | 284/390 (72.8%) |
| replay files restored verbatim | 4/309 (1.3%) | 3/309 (1.0%) |
| standard set | 30/30, 337.0 s (126.4% of 266.6 s) | 30/30, 266.6 s |

Its one visible failure is `replay-span-web-ui` trial 1. The screen keeps it on
the replay + hard set, at rank 4 of 4, but one run cannot separate it from
#1168 at rank 3. Neither earns more runs.

**Two more 1017 runs, 2026-10-25, on OpenCode 2.1.4.** Neither changes the
order. The rule for #1244 was fixed before it ran: it replaces #1160 if it is
no worse on the visible verdict and on the hidden re-grade, and its replay +
hard sum is at most 5,021.5 s (105% of #1160's).

| | #1160, the pick | [#1244][i1244]: v2.0 | [#1246][i1246]: #1168 again |
|---|---|---|---|
| stack | sudoingX @`cc59c360`, 0xSero 2.7 bpw @`e34ef55bda` | sudoingX v2.0 @`f8f20b37` (`dsv41-serve:v5`), sudoingx 2.8 bpw @`aba08ea002` | MiaAI recipe @`27741e1`, EAQ-v1 2.9 bpw (revision `45ee5d0f`, the same blobs as `6d54be4fca`) |
| backend | `dsv41f1017exl3serodual2xrc` | `dsv41f1017exl3sudov2dual2xrc` | `dsv41f1017eaqtfmiaaidual2xrc` |
| replay + hard, passed | 41/41 (1 excluded) | 41/41 (1 excluded) | 35/39 (3 excluded) |
| 95% CI (Wilson) | 91.4–100% | 91.4–100% | 76.4–95.9% |
| replay + hard, sum of medians | **4,782.4 s** | 5,348.7 s (111.8%) | 6,843.9 s (143.1%) |
| hidden tests: whole tasks | **17/20** | 14/20 | 15/18, 1 invalid, 2 no result |
| hidden tests: tests | 369/380 (97.1%) | 345/380 (90.8%) | **281/281 (100.0%)** |
| standard set | 30/30, 504.4 s | 29/29 (1 excluded), **438.7 s** | 10/10, time-stopped after round 1: 574.9 s, 211.5% of GLM's 271.8 s on the same trials |

- **#1244 fails two of the three tests.** Its visible verdict equals the
  pick's. Its hidden re-grade is lower, and its time is 111.8%. Most of the
  hidden gap is one task: `replay-span-web-ui-no-test-names` passed its visible
  suite 3 of 3 but restored 61 of 87 hidden tests, against 87 of 87, in a
  median of 1,459.5 s against 464.2 s. It is the faster stack on the standard
  set (87.0% of the pick's time). It had no timeout, no first-turn stop and a
  clean transcript audit.
- **#1246 repeats #1168's pattern.** The hidden re-grade is perfect again.
  The visible failures are three 1,800 s timeouts, on tasks that passed in
  their other two trials, and one first-turn stop (`finish=stop`, no tool
  call), against two in #1168. The transcript audit excluded the batch for that
  stop, and the rows were reinstated under the 2026-10-19 ruling
  ([#1177][i1177]).

### #1160: the pick

The weights are [`0xSero/DeepSeek-V4.1-Flash-1017-EXL3-2.7bpw`][w-sero1017]
@`e34ef55bda` (184.9 GiB): routed experts at 2.70 bpw on average, and
attention, shared expert and LM head at EXL3 K4. The Engram shards 47 and 48
come from the DeepSeek checkpoint. The engine is vLLM `6e517b15` for sm_121
with exllamav3 1.6.0, the `vllm_exl3_moe` plugin and the patch series of
[`sudoingX/dgx-spark-deepseek-v41`][r-sudoingx] @`cc59c360`. Its `install.sh`
built the image `dsv41-serve:v4` locally. The server runs TP=2 over RoCE, a
262,144-token window, 2 sequences, DSpark speculative decoding and reasoning
effort `low` set on the server. The 1017 template defaults to high.

- **Failures.** None in 41 counted trials. The hidden re-grade misses are
  `replay-defang-hidden` (1 of 2 whole, 17/20 tests),
  `replay-search-operators-hidden` (2 of 3, 40/42) and `replay-span-web-ui`
  (2 of 3, 81/87).
- **Second run ([#1255][i1255], 2026-10-25, OpenCode 2.1.4).** Replay + hard
  40 of 40 counted (two passing trials excluded for answer exposure), sum of
  medians 4,098.8 s against run 1's 4,782.4 s. Hidden 361/370 (97.6%), 17 of
  19 whole, against 369/380 (97.1%), 17 of 20. The misses moved:
  `replay-relative-dates-hidden` (2 of 3, 47/51) and
  `replay-search-operators-hidden` (2 of 3, 37/42). The standard set hit the
  early time stop after round 1: 11 of 11, sum of medians 536.5 s, 209.0% of
  GLM's time on the same trials.
- **Pinned.** The recipe has moved to v2.0, which re-graded lower and ran
  slower ([#1244][i1244]). The backend pins the recipe at `cc59c36`
  (`recipe_pin`, [PR #1256][pr1256]), and preflight reads it as `pinned`. Its
  `install.sh` still builds `dsv41-serve:v4` from pinned sources.
- **Memory.** MemAvailable held at 4.3–6.5 GiB on the head and 6.2–7.3 GiB on
  the worker. The GPUs peaked at 85 °C with no throttle reasons (#1160).
- **Text only.** The launcher stubs out the vision tower by default.

### GLM-5.3-Flash on TensorFold, MiaAI-Lab recipe: the fast option

GLM-5.3-Flash on this recipe was the pick until 2026-10-19.
[`MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold`][r-tf-mia] runs
[TensorFold][tensorfold] v0.6.0, TensorFold's Python engine, plus the recipe's
107 patches at `main` @`b5808ac` (v1.10 @`549fdc5` had 96, v1.8 @`33b50fd` 82). The image tag is
`tensorfold-glm53:v0.6.0-e66c657a433d` (v1.10: `v0.6.0-a1897d591f70`, v1.8: `v0.6.0-31557ed1cef6`), which carries the hash of the patches;
it runs in NVIDIA's PyTorch container (`nvcr.io/nvidia/pytorch:26.07-py3`). We
built it locally (`PULL=0`). It serves the TensorFold quant,
[`Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold`][w-tfq] @`76c0b517`: the
recipe's own default download. The recipe pins @`078455ff`, which holds the
same weights; the later commits change only the model card. The drafter is
DFlash2 ([`incoai/GLM-5.3-Flash-DFlash2`][w-dflash2] @`bf582e4`). The server
runs an FP8 KV cache shared by 4 requests and a 1,048,576-token window.

#### #970: the quant against TR3, on the fixed client

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

#### #997: recipe v1.10

[#997][i997] ran the pick, unchanged weights, on recipe v1.10. v1.9 turned on
`TENSORFOLD_NUCLEUS_UNION=1` by default: a fix for sampled decoding, which
OpenCode uses because it sends this model no temperature. Three runs, the same
client host and image as #970. Bar fixed before the first run: one run counts
as faster below 3,900 s; between that and 4,154 s it is no change.

| | v1.8 ([#970][i970]) | v1.10 ([#997][i997]) |
|---|---|---|
| passed, by verdict | 124/126 (42, 41, 41) | 123/126 (42, 41, 40) |
| 95% CI (Wilson) | 94.4–99.6% | 93.2–99.2% |
| sum of medians, 14 tasks × 9 trials | 4,111.1 s | 3,951.8 s (96.1%) |
| sum of medians, per run | 4,154.1 · 4,123.0 · 4,062.5 s | 3,882.8 · 3,914.7 · 4,124.4 s |
| hidden tests passed | 878/1,170 (75.0%) | 841/1,170 (71.9%) |
| median reasoning tokens a trial | 2,815.5 | 3,454.0 |

**No change.** One v1.10 run is under the bar, by 17.2 s; the other two are in
the no-change band. The pass-rate intervals overlap. v1.10 became the pick's
recipe then because the preflight runs the latest recipe, not because it
measured faster.

- **Failures.** `replay-gmail-api-sources-hidden` trial 1 failed in runs 2 and
  3, on 1 of 16 tests each time. In run 3, `replay-span-web-ui` trial 2 passed
  its suite, but the agent edited a test file.
- **Spread.** The v1.10 runs spread 6.2%, against v1.8's 2.3%.

#### #1018: recipe `main` @`b5808ac`

[#1018][i1018] ran the pick, unchanged weights, on the recipe's 2026-10-10
`main`: 53 commits past v1.10, all under "Unreleased" in its changelog. They add
`FILL_ROWS=2048` and patches 0100–0103 (draft pre-launch, a prompt-matmul tile,
decode matmuls without clusters), each stated to give the same bits. The
preflight refused v1.10 as behind. Three runs, the i9 client, the client image
with uv 0.13.0 (v1.10 ran on 0.12.23). Bar fixed before the first run: faster
if the three-run sum is below 3,688 s; between that and 4,124 s it is no
change.

| | v1.10 ([#997][i997]) | `main` ([#1018][i1018]) |
|---|---|---|
| passed, by verdict | 123/126 (42, 41, 40) | **126/126 (42, 42, 42)** |
| 95% CI (Wilson) | 93.2–99.2% | 97.0–100% |
| sum of medians, 14 tasks × 9 trials | 3,951.8 s | **3,825.7 s** (96.8%) |
| sum of medians, per run | 3,882.8 · 3,914.7 · 4,124.4 s | 3,738.2 · 3,538.0 · 3,861.2 s |
| median trial | 216.5 s | 183.6 s |
| hidden tests passed | 841/1,170 (71.9%) | 864/1,170 (73.8%) |
| trials that passed every hidden test of their task | 6/63 | 3/63 |
| median reasoning tokens a trial | 3,454.0 | 2,829.5 |

**No change.** One run is under the bar (3,538.0 s); the three-run sum,
3,825.7 s, is in the no-change band. The pass-rate intervals overlap. `main`
became the pick's recipe because the preflight runs the latest recipe.

- **Failures.** None in 126 trials.
- **Spread.** The runs spread 9.1%, against v1.10's 6.2%.
  `replay-span-web-ui-no-test-names` took 696.9–1,655.3 s across its 9 trials.
- **The rows record the build.** From #1018 every row carries `tensorfold`
  (0.6.0), `tensorfold_image`, `server_argv` and `parallelism` (`{"tensor": 2}`),
  read from the container ([#1017][pr1017]).

#### #1095: OpenCode 2.0.32

[#1095][i1095] ran the same backend, weights and image on OpenCode 2.0.32, on
2026-10-19. The recipe moved to `3fd60df`, a README change only, so every
default stayed the same. One standard run and one replay + hard run.

- **Standard set:** 30 of 30, sum of medians 266.6 s. This is the stack's
  first standard-set run.
- **Replay + hard:** 42 of 42, sum of medians 4,358.7 s. That is 113.9% of the
  #1018 sum on OpenCode 1.18.35 (3,825.7 s). The client changed, so this does
  not rank the two.
- **Hidden re-grade:** 1 of 21 whole tasks, 284 of 390 tests (72.8%). Only
  `replay-gmail-api-sources-hidden` passed one whole trial.

**TR3's original repository has left the Hub.** Download it from its author,
[`brandonmusic/GLM-5.3-Flash-tr3-4bpw`][w-tr3] @`a5fee929`, which is
byte-identical by sha256 to `9eaebb7` (#937). Section 6 shows the override.

**What to keep in mind:**

- **The engine is new and the recipe carries it.** TensorFold has few users,
  and the recipe's 107 patches (`main` @`b5808ac`) sit on v0.6.0. The
  recipe's patches did not apply to v0.6.5 (28 of 70 failed), so the stack
  moves when its recipe does.
- **TensorFold's native engine is slower here.** Its 1.1.3 release binary
  serves the same weights on two Sparks with no recipe
  (`glm53fexl3tfnative113dual2x`, [#1095][i1095], 2026-10-24). Standard set:
  30 of 30 in 425.4 s, 162.1% of the recipe stack's 262.4 s on the same
  client. TensorFold 1.1.4 shipped later the same day and is not measured.
- **Rows before #1018 do not record the engine build**
  (`tensorfold_version=unknown`, [#320][i320]). For those, the recipe commit
  and the image tag are the record.
- **The visible suite is not the whole answer.** GLM passes the tests it can
  see and misses about a quarter of the held-out ones, on every run.

## 3. Every stack on the fixed client

The fixed client has two series. They do not compare: OpenCode 2.0.32 alone
moved GLM's replay + hard sum from 3,825.7 s to 4,358.7 s (section 2).

### OpenCode 2.0.32 and 2.1.x, from 2026-10-18

Five stack configurations ran replay + hard, one run each. The order is this
page's: correctness first, with the hidden re-grade, then time. The screen's
own order is by time. Percentages are of GLM's sum on OpenCode 2.0.32,
4,358.7 s:

| stack (backend) | runs | passed | 95% CI | sum of medians | % of GLM | median reasoning tokens a trial | hidden: whole tasks | hidden: tests | replay files restored verbatim | screen |
|---|---|---|---|---|---|---|---|---|---|---|
| **DeepSeek-1017 EXL3 2.7 bpw (0xSero) on vLLM + ExLlamaV3** (`dsv41f1017exl3serodual2xrc`), the pick ([#1160][i1160]) | 1, 1 row excluded | 41/41 | 91.4–100% | 4,782.4 s | 109.7% | 7,157 | 17/20 | 369/380 (97.1%) | 131/305 (43.0%) | keep |
| DeepSeek-1017 EXL3-EAQ-v1 2.9 bpw (MiaAI) on TensorFold (`dsv41f1017eaqtfmiaaidual2xrc`) ([#1168][i1168]) | 1 | 38/42 | 77.9–96.2% | 6,226.4 s | 142.8% | 9,698 | 19/21, 1 no result | 361/361 (100.0%) | 127/292 (43.5%) | keep |
| DeepSeek-1017 EXL3 2.8 bpw (jayleaton) on TensorFold Zig (`dsv41f1017tfzigjaydual2xrc`) ([#1142][i1142]) | 1, 7 rows excluded | 34/35 | 85.5–99.5% | no 14-task sum | — | — | 12/14, 1 no result | 270/274 (98.5%) | — | not screened: no counted `gmail-api-sources-hidden` trial |
| GLM TensorFold quant, MiaAI (`glm53fexl3tfqmiaai20261010dual2xrc`), the fast option ([#1095][i1095]) | 1 | 42/42 | 91.6–100% | **4,358.7 s** | 100% | 3,938 | 1/21 | 284/390 (72.8%) | 3/309 (1.0%) | keep |
| GLM-5.3-Flash FP4-Local (nvidia) on vLLM (`glm53ffp4localdual2xrc`) ([#1128][i1128]), OpenCode 2.1.2 | 1 | 41/42 | 87.7–99.6% | 6,704.7 s | 153.8% | 3,654 | 3/21 | 283/390 (72.6%) | 4/309 (1.3%) | keep (rank 4; [#1128][i1128] closed it as cut) |

On the standard set (`--standard`), GLM leads at 266.6 s on 2.0.32 and 262.4 s
on 2.1.4. Against the run on the same client:

| stack (backend) | client | passed | sum of medians | % of GLM | screen |
|---|---|---|---|---|---|
| GLM FP4-Local on vLLM (`glm53ffp4localdual2xrc`, [#1128][i1128]) | 2.1.2 | 30/30 | 337.0 s | 126.4% of 266.6 s | keep |
| GLM on TensorFold native 1.1.3 (`glm53fexl3tfnative113dual2x`, [#1095][i1095]) | 2.1.4 | 30/30 | 425.4 s | 162.1% | keep |
| DeepSeek-1017 on TensorFold Zig ([#1142][i1142]) | 2.0.32 | 28/28 | 463.8 s | 174.0% | keep |
| DeepSeek-1017 on vLLM, the pick ([#1160][i1160]) | 2.0.32 | 30/30 | 504.4 s | 189.2% | keep |
| Nemotron-3.5-Super-VL NVFP4 on vLLM v0.32.0, DFlash, reasoning off (`nemotron35supervldflashnothinkdual2xv032`, [#1231][i1231]) | 2.1.4 | 14/14 | 549.5 s | 209.4% | screened out |
| DeepSeek-1017 EAQ-v1 on TensorFold ([#1168][i1168]) | 2.0.32 | 10/10 | 660.4 s | 247.7% | screened out |
| DeepSeek-V4-Flash-Vision-Exp, DSpark (`dsv4flashvisiondspark2xrc`, [#1097][i1097]) | 2.1.4 | 10/10 | 871.2 s | 332.0% | screened out |
| MiMo-V2.6-Flash-RL EXL3 3.0 bpw on TensorFold, thinking on (`mimov26flashrlexl3tfdflashdual2x`, [#1032][i1032]) | 2.1.4 | 10/10 | 1,256.0 s | 478.7% | screened out |

The early stop ended the four screened-out runs, so they have one or two trials
a task. None of them ran replay + hard.

### OpenCode 1.18.x and 1.19.0, 2026-10-06 to 2026-10-13

Ten stack configurations ran the 14 replay + hard tasks. Percentages are of GLM
on v1.8, the reference for the rows below it:

| stack (backend) | runs | passed | 95% CI | sum of medians | % of GLM v1.8 | median reasoning tokens a trial | hidden tests | replay files restored verbatim | screen |
|---|---|---|---|---|---|---|---|---|---|
| **GLM TensorFold quant, MiaAI `main` @`b5808ac`** (`glm53fexl3tfqmiaai20261010dual2xrc`), the pick until 2026-10-19 ([#1018][i1018]) | 3 | 126/126 | 97.0–100% | **3,825.7 s** | 93% | 2,829.5 | 864/1,170 (73.8%) | 15/927 (1.6%) | keep |
| GLM TensorFold quant, MiaAI v1.10 (`glm53fexl3tfqmiaaiv110dual2xrc`), the pick until #1018 ([#997][i997]) | 3 | 123/126 | 93.2–99.2% | **3,951.8 s** | 96% | 3,454.0 | 841/1,170 (71.9%) | 15/927 (1.6%) | keep |
| GLM TensorFold quant, MiaAI v1.8 (`glm53fexl3tfqmiaaiv18dual2xrc`), the pick until #997 | 3 | 124/126 | 94.4–99.6% | **4,111.1 s** | 100% | 2,815.5 | 878/1,170 (75.0%) | 12/927 (1.3%) | keep |
| GLM TensorFold TR3, MiaAI v1.8 (`glm53fexl3tfmiaaiv18dual2xrc`) | 3 | 122/126 | 92.1–98.8% | 4,271.0 s | 104% | 3,319.5 | 851/1,170 (72.7%) | 15/927 (1.6%) | keep |
| DeepSeek V4.1 Flash EXL3 2.8 bpw on TensorFold Zig, jayleaton ([#919][i919]) (`dsv41ftfzigjaydual2xrc`) | 2 (one after an engine crash) | 39/40 | 87.1–99.6% | 6,262.9 s | 152% | 0 recorded | 299/315 (94.9%) | 81/268 (30.2%) | cut |
| Qwen3.8-Flash-Next NVFP4 (nvidia), MTP, vLLM 0.31.0 ([#865][i865]) (`qwen38fnnvfp4nv2xspec`) | 1, stopped at 14 of 42 | 13/14 | 68.5–98.7% | 9,205.3 s | 224% | 15,466 | 86/101 (85.1%) | 2/86 (2.3%) | screened out |
| Qwen3.8-Flash-Next NVFP4 (nvidia), MTP, vLLM main nightly ([#865][i865]) (`qwen38fnnvfp4nv2xspecmain`) | 1, stopped at 14 of 42 | 13/14 | 68.5–98.7% | 9,526.9 s | 232% | 16,806 | 79/101 (78.2%) | 7/86 (8.1%) | screened out |
| Qwen3.8-Flash-Next NVFP4 on TensorFold Zig, MiaAI ([#897][i897]) (`qwen38fnnvfp4tfmiaaidual2xrc`) | 1, stopped at 25 of 42 | 25/25 | 86.7–100% | 7,746.8 s | 188% | 13,991 | 160/194 (82.5%) | 6/167 (3.6%) | screened out |
| Qwen3.8-Flash-Next hibrid48, thinking on, vLLM v5.2 ([#977][i977]) (`qwen38fnhibrid48v52dual2xrc`) | 1, stopped at 14 of 42 | 13/14 | 68.5–98.7% | 8,138.6 s | 198% | 15,570 | 87/101 (86.1%) | 6/86 (7.0%) | screened out |
| Qwen3.8-Flash-Next hibrid48, thinking off, vLLM v5.2 ([#977][i977]) (`qwen38fnhibrid48v52nothinkdual2xrc`) | 1, stopped at 14 of 42 | 12/14 | 60.1–96.0% | 10,889.4 s | 265% | 0 | 59/78 (75.6%) | 2/77 (2.6%) | screened out |

A timeout counts at the 1,800 s limit in every sum. A run stopped early has
fewer trials behind each median.

**Every pass-rate interval overlaps GLM `main`'s.** On pass rate, no stack here
can be told apart from it; the ranking rests on time. The Qwen runs have
too few trials for their intervals to say more.

`screen_stacks.py` now screens out the two early-stopped Qwen runs: against
its leader in this series, GLM on `main` (3,825.7 s), they are at 202.5% and 212.7%,
over its 200% limit. (Before #1018 they ranked 4th and 5th and were marked
"cut"; before #997, "keep".) They do not earn more runs. The run-time early
stop ended each one at over 200% of its leader on the same trials. That leader
was the broken-client run of 2026-10-01 (3,953.4 s), not the fixed-client GLM
run ([#992][i992]). Against GLM on v1.8 they are at 188% and 198%: no verdict
changes.

**What the Qwen runs show:**

- **Qwen passes, but slowly.** 25 of 25 and 13 of 14 visible verdicts, and the
  #897 run passed `replay-span-web-ui-no-test-names` (1,321.3 s), which
  hibrid48 timed out on. But every trial carries about 5 times GLM's
  reasoning tokens.
- **Thinking off is slower, not faster.** Without reasoning, hibrid48 took about
  three times as many steps (the #977 read-out), 134% of the thinking-on time,
  and lost two trials to timeouts.
- **The engine is not the gap.** On the same NVFP4 weights and the 7 replay
  tasks, the Zig engine's sum of medians was 2,359.6 s, against 3,157.4 s on
  vLLM 0.30 (`qwen38fnnvfp4dual2xrcv030`): 75%. That compares across the
  client fix, so it is indicative only. Qwen's sums are still 188% to 265%
  of GLM's.
- **Qwen's hidden-test rate is above GLM's**, 82.5% and 86.1%, against
  72.7–75.0% for GLM. DeepSeek-1017 is higher still (section 2). But that is 11 and 7 trials, and tests in one trial are
  not independent. It is a reason to look again with a faster Qwen stack, not
  a result.

**What the DeepSeek V4.1 run shows ([#919][i919]):**

- **Second-fastest family, still well behind.** 6,262.9 s is 164% of GLM
  on `main` and 52.3% behind the third-ranked stack, so the screen marks
  it "cut": it passes, but it does not earn more runs. The engine crashed once
  on a 201,049-token prompt (`PoolExhausted`), and the cell finished on a
  restart. It served prompts up to 219,326 tokens before that.
- **A high hidden-test rate, with a caveat.** 299 of 315 hidden
  tests (94.9%), against 72.7–75.0% for GLM. But it also restored 30.2% of the
  replay files verbatim, against 1.6% for GLM. Part of that hidden-test
  rate may be recall, not new code.
- **No reasoning count.** Every scored row records 0 reasoning tokens, at
  server reasoning effort `low`. Whether the model did not reason or the engine
  does not report it is not checked, so the column does not compare with the
  others.
- 40 of 42 trials are scored. Two are excluded for answer exposure (#54): the
  agent worked in the harness checkout, which could disclose an earlier
  trial's patch.

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

Every pass-rate interval in this table overlaps the leader's (TR3 on v1.3,
42/42, 91.6–100%), so pass rates do not rank these stacks; time does. Replay
files restored verbatim range from 2/309 (0.6%) to 29/927 (3.1%).

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
| GLM-5.3-Flash NVFP4 on Enntity's Atlas fork (`glm53fnvfp4sparkglmdual2xrc`), fixed client | 3 of 5 replay trials passed. Atlas drops tool calls over about 1,024 tokens at the recipe's settings, so OpenCode cannot use it. | [#999][i999] |
| TensorFold v0.3.5 on a GLM 4-bit MLX build | The largest context that fit was about 36k tokens, too small for 19 of 63 replay trials. | [#798][i798] |
| GLM TensorFold, effort `high` | 15 of 21 on the hard set; span tasks 0 of 6. | [#840][i840] |
| NVIDIA Flare with GLM-5.3-Flash FP4-Local, 2026-10-22 | Did not start on two Sparks at three upstream commits: a tuning race at `5f37bd1`, then the worker's weight load failing at `947a5a6d` and `df8c2ab6`. Not run. | [#1207][i1207] |

## 5. What four weeks of data show

- **The engine matters more than the quantization.** The same TR3 weights took
  57–65% of vLLM's time on TensorFold. No change of quantization came close.
- **Reasoning volume decides the Qwen–GLM gap.** Qwen3.8-Flash-Next takes
  about twice as long a task as GLM, and reasons about 5 times as many
  tokens (13,991 against 2,815.5 a trial). Turning thinking off made it slower
  still: it took about three times as many steps.
- **Agent time is mostly re-reading context.** Prefill and prefix caching
  dominate a trial, not tokens per second. The decode table is in
  [`docs/results.md`](../../docs/results.md#cortex-x925-128gb-gb10-x2).
- **Set reasoning effort `low` explicitly.** On jayleaton's TensorFold stack,
  `high` took 171% of `low`'s time on the 12 non-span tasks and passed 0 of 6
  span trials ([#840][i840], [#894][i894]). GLM's template falls back to `max`
  when nothing is set; Qwen3.8-Flash-Next's falls back to `xhigh`.
- **Hidden tests separate the model families, not the stacks within one.**
  Every GLM stack at effort `low` lands between 68.3% and 75.9%. The one GLM
  stack at `high` (jayleaton) reached 88.0%, 190/216, but 5 of its trials gave
  no result. Qwen sits higher (section 3), on few trials. DeepSeek-1017 lands
  at 97.1–100.0% on three quants and two engines (section 2). The visible
  suites do not show this gap.
- **Two task traps cost some trials on every stack:**
  - gmail-api-sources builds a source from `tests/fixtures/simple.mbox`, which
    the target repository's `.gitignore` excludes. An agent that reads the
    file early fails one visible test.
  - OpenCode's per-step output cap cut a few span-task steps at 16,384 tokens.
    The GLM recipe runs at 32,768, and the pick's client provider at 16,384.

## 6. How to run the pick and the fast option

All four stacks of "The short answer", step by step from two clean Sparks,
are in [`howto.md`](howto.md). This section keeps the short form.

### The pick: DeepSeek-1017 on vLLM

The [stack's README][r-sudoingx] has the full quickstart. These are the steps
#1160 ran, with the 1017 weights in place of the stack's V4.1 weights:

1. Clone the stack on both Sparks. The scripts expect `~/dsv41_recipe`:

   ```sh
   git clone https://github.com/sudoingX/dgx-spark-deepseek-v41 ~/dsv41_recipe
   cd ~/dsv41_recipe && git checkout cc59c360
   ```

2. Build the images locally, on one Spark, with no other heavy work on either.
   `install.sh` refuses while any GPU container runs, monitoring containers
   included:

   ```sh
   PEER=<worker> ./install.sh
   ```

3. Download the weights on both Sparks, at the same absolute path. The Engram
   tables stay in DeepSeek's own shards 47 and 48:

   ```sh
   hf download 0xSero/DeepSeek-V4.1-Flash-1017-EXL3-2.7bpw --revision e34ef55bda7c37d17d8dcc86a8a64e7cb875481e \
     --local-dir ~/models/dsv41-1017-exl3
   hf download deepseek-ai/DeepSeek-V4.1-Flash-1017 \
     model-00047-of-00048.safetensors model-00048-of-00048.safetensors \
     --revision 1bae0e1088993b9b82de08f59de5be5a34e42170 --local-dir ~/models/dsv41-1017-exl3
   ```

4. Start both ranks from the head. Set reasoning effort `low` on the server:
   the 1017 template defaults to high.

   ```sh
   HEAD=<head> WORKER=<worker> PORT=8888 SERVED=DeepSeek-V4.1-Flash-1017-EXL3 \
     MODEL_DIR=$HOME/models/dsv41-1017-exl3 \
     EXTRA='--default-chat-template-kwargs {"reasoning_effort":"low"}' \
     ./scripts/launch/dsv41_tp2.sh up
   ```

5. Stop it with `./scripts/launch/dsv41_tp2.sh down`.

The first boot in #1160 was ready after 537 s. On the client, use the provider
block below with `"baseURL": "http://<head-node>:8888/v1"`, the model name
`DeepSeek-V4.1-Flash-1017-EXL3`, and `"limit": { "context": 262144, "output":
16384 }`. Leave out `options`: the server sets the effort.

### The fast option: GLM on TensorFold

On the head node, as the user that owns Docker:

1. Clone the recipe at its latest commit. GLM was last measured at `main`
   @`3fd60df` (#1095), with the same defaults as @`b5808ac` (#1018):

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

   Do not set `MODEL_ID`: the recipe's default checkpoint is the GLM quant.
   `MODEL_REVISION` selects the exact snapshot #970, #997 and #1018 measured; it is optional.
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

## 7. Tool versions

### The pick and the fast option, checked 2026-10-24

| component | measured | latest | what changed |
|---|---|---|---|
| sudoingX DeepSeek V4.1 stack (the pick) | @`cc59c360` | v2.0 @`f8f20b37`, 2026-10-22 | new default quant (2.8 bpw) and engine (v5); measured as [#1244][i1244], which does not replace the pick (section 2) |
| 0xSero 1017 EXL3 2.7 bpw (the pick) | @`e34ef55bda` | @`e34ef55bda` | none |
| vLLM | `6e517b15` in the pick's image; v0.32.0 for #1231 | [v0.32.0][vllm-rel], 2026-10-18 | current |
| MiaAI TensorFold GLM recipe (the fast option) | `main` @`3fd60df` (#1095) | @`b0435796`, 2026-10-24 | docs, credits, a `prepare.sh` warning and the image-token budget; no text-serving default changed |
| Mia-AiLab GLM 4bpw TensorFold quant | @`76c0b517` | @`76c0b517` | none |
| TensorFold | v0.6.0 + 107 patches (fast option); native 1.1.3 (#1095) | [v1.1.4][tf-rel], 2026-10-24 | 1.1.4 lists Qwen3.8-27B, Gemma 4 and Flash Next work, not GLM; not measured |
| MiaAI DeepSeek-1017 TensorFold recipe (#1168) | `2b906fd` / `e65785c` | @`27741e17`, 2026-10-24 | measured as [#1246][i1246] (section 2) |
| OpenCode | 2.0.32, 2.1.2, 2.1.4 | 2.1.4 in use | 2.1.4 did not move the fast option (section 1) |
| uv, in the client image | 0.13.1 → 0.13.2; 0.13.5 for #1244 and #1246 | [0.13.5][uv-rel], 2026-10-22 | current |

### Everything else, checked 2026-10-08

The older stacks were not checked again.

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

- **A third run of the pick.** #1160 and [#1255][i1255] agree. A third on
  @`cc59c360` would meet the three-run bar. #1168's stack has two runs
  ([#1246][i1246]); its first-turn stops repeat, once in 42 trials.
- **Pinned winners ([#1257][i1257]).** The pin covers the recipe checkout
  only. Rows, reports and the daily preflight do not show it yet.
- **[#1177][i1177]:** reopened 2026-10-25. By the operator's decision of
  2026-10-19, a quick exit that the model answered with `finish=stop` and no
  tool call counts as a model FAIL, not a harness fault. The transcript audit
  applies it since [PR #1254][pr1254]: such a trial is a model stop, and the
  batch is not excluded. Before that, #1246's rows were reinstated by hand.
- **[#986][i986]:** closed. TensorFold's native engine now serves GLM on two
  Sparks with no recipe: 1.1.3 was measured, at 162.1% of the recipe stack's
  standard-set time (section 2). 1.1.4 (2026-10-24) is not measured.
- **A faster Qwen stack.** Qwen's hidden-test rate is above GLM's, on few trials. The
  #897 recipe's INT4-AutoRound quant (5 of 10 experts, about 32% faster decode
  per the recipe) is not approved. Faster decode alone would not close a 2x gap
  that comes from reasoning volume.
- **[#992][i992]:** the early stop's leader should be the fixed-client pick.
- **[#865][i865]:** RedHat's Qwen3.8-Flash-Next NVFP4 build against nvidia's.
  Arm 1 (nvidia, section 3) is screened out. Arm 2 (RedHat) does not fit in
  memory on two Sparks with vLLM main, and waits on the operator's decision.
- **[#904][i904]:** done (closed 2026-10-13). Rows now record the serving
  image's vLLM build. All 14 rows of #865 arm 1 on main read
  `0.31.1rc1.dev296+gfe4df3df0`. Older vLLM rows keep the head's `0.29.0`.
- **[#626][i626]:** done (closed 2026-10-13). The Qwen NVFP4 + MTP stack on
  vLLM main hit the prefix-cache ceiling on 54 of 54 replays, so prefix caching
  works there, as section 5 assumes.
- **Not measured:** several clients at once, and code quality beyond the tests.

## References

**Recipes:**
[sudoingX DeepSeek V4.1 vLLM][r-sudoingx] ·
[sybil-solutions DeepSeek-1017 EXL3][r-sybil] ·
[MiaAI-Lab DeepSeek-1017 TensorFold][r-dsv41-1017-mia] ·
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
[#986][i986] · [#992][i992] · [#904][i904] ·
[#865][i865] RedHat Qwen NVFP4 · [#919][i919] DeepSeek V4.1 on TensorFold Zig ·
[#626][i626] prefix cache · [#969][i969] transcript audit ·
[#1095][i1095] GLM on OpenCode 2.0.32 · [#1142][i1142] DeepSeek-1017 on TensorFold Zig ·
[#1160][i1160] DeepSeek-1017 on vLLM · [#1168][i1168] DeepSeek-1017 EAQ-v1 on TensorFold ·
[#1177][i1177] first-turn stops ·
[#1128][i1128] GLM FP4-Local on vLLM · [#1231][i1231] Nemotron-3.5-Super-VL ·
[#1032][i1032] MiMo-V2.6-Flash-RL · [#1097][i1097] DeepSeek-V4-Flash-Vision-Exp

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
[r-sudoingx]: https://github.com/sudoingX/dgx-spark-deepseek-v41
[r-sybil]: https://github.com/sybil-solutions/deepseek-v4.1-flash-1017-exl3-spark
[r-dsv41-1017-mia]: https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks
[w-sero1017]: https://huggingface.co/0xSero/DeepSeek-V4.1-Flash-1017-EXL3-2.7bpw
[w-eaq1017]: https://huggingface.co/Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw
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
[i866]: https://github.com/evanwtf/local-llm/issues/866
[i892]: https://github.com/evanwtf/local-llm/issues/892
[i894]: https://github.com/evanwtf/local-llm/pull/894
[i897]: https://github.com/evanwtf/local-llm/issues/897
[i904]: https://github.com/evanwtf/local-llm/issues/904
[i919]: https://github.com/evanwtf/local-llm/issues/919
[i626]: https://github.com/evanwtf/local-llm/issues/626
[i940]: https://github.com/evanwtf/local-llm/issues/940
[i968]: https://github.com/evanwtf/local-llm/issues/968
[i969]: https://github.com/evanwtf/local-llm/issues/969
[i970]: https://github.com/evanwtf/local-llm/issues/970
[i997]: https://github.com/evanwtf/local-llm/issues/997
[i1018]: https://github.com/evanwtf/local-llm/issues/1018
[pr1017]: https://github.com/evanwtf/local-llm/pull/1017
[i999]: https://github.com/evanwtf/local-llm/issues/999
[i977]: https://github.com/evanwtf/local-llm/issues/977
[i986]: https://github.com/evanwtf/local-llm/issues/986
[i992]: https://github.com/evanwtf/local-llm/issues/992
[i1095]: https://github.com/evanwtf/local-llm/issues/1095
[i1142]: https://github.com/evanwtf/local-llm/issues/1142
[i1160]: https://github.com/evanwtf/local-llm/issues/1160
[i1168]: https://github.com/evanwtf/local-llm/issues/1168
[i1177]: https://github.com/evanwtf/local-llm/issues/1177
[i1244]: https://github.com/evanwtf/local-llm/issues/1244
[i1246]: https://github.com/evanwtf/local-llm/issues/1246
[i1255]: https://github.com/evanwtf/local-llm/issues/1255
[i1257]: https://github.com/evanwtf/local-llm/issues/1257
[pr1254]: https://github.com/evanwtf/local-llm/pull/1254
[pr1256]: https://github.com/evanwtf/local-llm/pull/1256
[i1032]: https://github.com/evanwtf/local-llm/issues/1032
[i1097]: https://github.com/evanwtf/local-llm/issues/1097
[i1128]: https://github.com/evanwtf/local-llm/issues/1128
[i1231]: https://github.com/evanwtf/local-llm/issues/1231
[i1207]: https://github.com/evanwtf/local-llm/issues/1207
