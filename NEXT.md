# Where to pick up

Updated 2026-08-28, after #23, #24, #26, #34 step 1, and the build half of #4. Work the issues in the
order below. Each issue is self-contained; this file only sets priority and
records machine state that is not in git.

## Order

| # | issue | why this position |
|---|---|---|
| 1 | **#35** Re-examine the "too big" tier against streaming | **#34 turned that tier from a verdict into a queue.** GLM-5.2 (211 GB, #17), Kimi K3 and MiniMax M3 were all rejected on size. All three are MoE, so they read at the block size this NVMe is good at, and a 60% resident cut moves them from impossible to slow. Cheapest real gain left. |
| 2 | **#28** Engine comparison -- reopened by its own result | The residual **1.9x at matched sampling** (thinking-mode template handling is the hypothesis), and re-checking the MTPLX "17% faster on 68% fewer tokens" claim, which is the same shape and was attributed to the stack. |
| 3 | **#4** Harder tasks | 18/18. Premise revised: difficulty along these axes buys time, not discrimination. Needs *common* defects, not a 2-error signal in one task. |
| 4 | **#27** Retire the ds4 fork | Blocked, re-checked 2026-08-28: antirez/ds4#885 and #886 both open. |

## Done since the last update

- **#34 closed. The cost curve exists.** MoE expert streaming: **91.0 -> 36.7 GiB
  (-60%) for +76% suite wall time**, 16/16, no correctness cost across 31 trials.
  Memory is *bounded* (36.7 GiB after one request, 37.1 after ten trials), and
  startup drops 16-30s to **2s**. The PLE offload (#33) by contrast saved
  **nothing** and cost 28%. **Streaming does not make a fitting model faster; it
  makes a non-fitting model possible** -- which reopens the "too big" tier.
  Independently lands within 1% of the 37 GB @EyalToledano reported for the same
  technique on a different model.
- **Trap:** `ds4-up` hardcoded `--warm-weights`, which touches every page and
  contradicts `--ssd-streaming`. Together they report **90.9 GiB -- full
  residency, streaming apparently doing nothing**, with no warning. `WARM` is now
  overridable; both launchers take `EXTRA_FLAGS`.

- **#28 answered, and the headline is an artifact -- do not quote "+66%".** First
  fixed-model engine comparison here, using the identical GGUF out of Ollama's
  blob store. Suite: Ollama 523.1s vs llama.cpp 870.8s. **The entire gap is one
  task** -- minus `parser-date` it is +9%, inside the noise. **Throughput is
  identical**: 14.1 vs 15.0 s per 1k output tokens. llama.cpp was slower because
  it emitted **29,906 tokens against 7,449** on that task, because `llamacpp-up`
  hardcoded `--temp 1.0` while Ollama's modelfile sets nothing. Matching the
  sampler halved both tokens and clock (422s -> 212s) and closed **half** the
  gap; the residual 1.9x is unexplained. **`storage-blob-put` went 3/3 at t=1.0
  and 0/3 at t=0.8** -- sampler settings move pass rate, not just wall time.
- **#33 closed: the PLE offload does not pay.** 4-bit `-M64` is **+28% slower**
  than 3-bit on an identical stack, 16/16 vs 15/15. The memory saving was never
  available: `-M64` changes no tensors (1224 both, 3 shards vs 33), and `vmmap`
  shows mmap already makes every weight page evictable -- physical footprint
  **~5 GB against ~92 GiB RSS**, with or without pinning the table to CPU.
- **Infrastructure moved to latest**, and it broke something within minutes:
  Codex 0.150.1 sends `instructions` **and** a `role=developer` item, which
  llama-server turns into two system messages and the Qwen template rejects.
  `fold_developer()` in the shim fixes it; all llama.cpp codex profiles now go
  through the shim. **PR #27742 merged upstream** -- `~/git/llama.cpp` is on
  mainline `d7bd3bfca`, old build tagged `benchmark-pr27742-2026-08-26`.

- **#4 measured: 18/18 pass.** Three new tasks x ds4 x {Claude Code, Codex} x 3.
  **The ceiling is not an artifact of easy tasks.** Per-task median rose
  194.6 -> 270.6 s (**+39%**) with **no** additional failures. Suites 813.4 s vs
  701.1 s, a 16% gap that is inside #23's +/-12.9% band -- **no difference
  measured**. `restored_verbatim` **0/18**, 18 distinct solutions: nothing is
  recalled, and with `unquote_mbox`'s docstring removed the model re-derived the
  mboxrd reasoning from scratch. One real defect, in 5 of 6 trials on the
  multi-file task and reproducible across both clients: a callback annotated
  `re.Match` instead of `re.Match[bytes]`, which adds 2 `mypy --strict` errors
  while all 71 tests pass. First "passes but is worse" result recorded here.
- **A latent harness defect, found by running unattended.** `agent_env()` never
  set `CODEX_API_KEY`, so every Codex row ever recorded depended on the operator
  having exported it in the launching shell. Unattended, Codex dies at config in
  0.7 s and the row looks exactly like the model giving up. Fixed and tested.
  The 4 rows it produced are marked excluded; **the historical record is
  unaffected** -- all 140 Codex trials audited, all 3 failures genuine, none
  under 10 s.
- **#23** closed. **Three trials is a screening run, not a measurement.** Pass
  rate: an unbroken run's Wilson bound is `n/(n+z^2)`, so >90% needs **35**
  consecutive passes, >95% needs 73. One failure costs ~20 trials. Wall time,
  bootstrapped over 198 observations: n=3 pins a task median to **+/-27.9%** and
  a 5-task suite to **+/-12.9%**, so suites separate only above a ~26% gap. Every
  published speed claim was re-checked against that -- all survive, but Q3-vs-Q2
  (#31) clears by a hair and rests on winning all five tasks separately.
  `sizing.py` is re-runnable. The rule is in AGENTS.md.
- **#34 step 1** done: the NVMe is measured for the first time
  (`benchmarks/disk/RESULTS.md`). Sequential **9.45 GiB/s**; random 1 MiB
  **198 us / 6.32 GiB/s**; random 4 KiB **61 us / 0.10 GiB/s**. **Block size is
  what costs, not randomness** -- 1 MiB random reads reach 67% of sequential,
  4 KiB reads reach 1.1%. Streaming MoE expert blocks is arithmetically viable
  (~2 ms per fully-cold token, a 500 tok/s ceiling); the n-gram PLE table is the
  hard case and its cost depends on lookups per token, which is **unmeasured**.
- **#4** build half done and merged. `run.py` had deleted every worktree in a
  `finally`, so **398 trials of produced code were thrown away**; solutions are
  now saved and hashed, ruff and mypy run as deltas against the excised tree, and
  `restored_verbatim` checks the authorship contamination METHODOLOGY has warned
  about since day one. Three new tasks, each moving one variable.
- **The empty-virtualenv confound is withdrawn -- it was never real.** The
  control has run `uv run pytest` before the agent since the first commit, and
  all 482 rows carry a control result. The new tasks **do not start a new
  series**.
- **#26** answered and its hypothesis refuted: not the KV cache, not warm-up
  (first trial of a batch is 0.98x the rest over 92 batches). Wall time tracks
  output tokens at r=0.98. The server samples at **temperature 1.0 with a fresh
  seed per request**, which #23 has now turned into a trial-count rule.
- **#24** published verdicts corrected, after two live reader bugs -- a timeout
  writes no `passed` key, and `summarize.py` still hand-rolled its exclusion
  filter over fourteen `confound` rows. **Do not test `row["passed"]` directly.**
- **#30/#31/#32/#22/#25/#16**: Metal ceiling raised to 112.00 GiB; Qwen3.8-Flash-Next
  is best at `UD-Q3_K_XL` (15/15); GLM-5.3-Flash works (15/15) and is the fifth
  lineage. Details in RESULTS.md and RECOMMENDATIONS.md -- all have landed.

## The open question all of this serves

*What is the most useful model + engine + harness for local coding if hosted
providers are unavailable?*

**Answer: `ds4`, with either Claude Code or Codex.** Not "Codex only" -- that
claim stood here until 2026-08-28 and the clean data refutes it.

| combination | pass | 95% CI | suite |
|---|---|---|---|
| `ds4 x claude` | 46/46 | **92-100%** | 858.2s |
| `ds4anthropic x codex` | 36/36 | **90-100%** | 975.3s |
| `ds4anthropic x claude` | 29/30 | 83-99% | 1120.6s |
| `ornith15 x codex` | 40/42 | 84-99% | **597.0s** |
| `qwen38fnq3 x codex` | 15/15 | 80-100% | 895.8s |
| `glm53 x codex` | 15/15 | 80-100% | 1362.1s |
| `qwen38fnq2 x claude` | 13/16 | 57-93% | 5235.7s |

Pooling the two ds4 wire protocols -- same weights, same server -- gives
**Claude Code 75/76 (982s) and Codex 36/36 (975s)**. Two clients, seven seconds
apart, intervals almost entirely overlapping. Nothing here separates them.

**Two combinations now clear 90% at 95% confidence, and they are the same model
under different clients.** `ornith15 x codex` is 1.6x faster and still cannot be
distinguished from either. The three at 15/15 need ~35 consecutive passes to
clear 90%; they are promising, not proven.

Note what this table cannot tell you: **which writes better code.** Nearly
everything passes. That is #4, and it is why this table has stopped being
informative.

## Machine state

**Not persisted, and a reboot reverts it:**

```sh
sudo sysctl iogpu.wired_limit_mb=114688     # currently applied, verify: 112.00 GiB
```

Verify with the Metal probe in #30, not with `sysctl` -- the sysctl reads `0`
whether or not a limit is in force. **`glm53` will not load without this**
(100.6 GiB resident against a 107.52 GiB default).

**Check before every batch:** `uv run python benchmarks/agent/preflight.py`.

**Servers:** the GLM `llama-server` and its shim were stopped 2026-08-28.
`ds4-server` was started for the #4 run and may still be up on :8000 holding
~91 GiB. Ollama is stopped. Restart ds4 with:

```sh
cd ~/git/ds4 && ./ds4-server -m gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf \
    --warm-weights --ctx 100000 --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192
```

**Versions, 2026-08-28.** Everything is on the newest release; see the policy in
AGENTS.md. `preflight.py` reports drift before every batch.

| tool | version | note |
|---|---|---|
| Claude Code | 2.1.251 | current |
| Codex | **0.150.1** | was 0.148.0 -- **every earlier Codex row is 0.148.x** |
| OpenCode | **1.18.25** | was 1.18.18 |
| Ollama | 0.33.1 | **0.33.2 available; it is `/Applications/Ollama.app`, update from the app** |
| llama.cpp | **mainline `d7bd3bfca`** | PR #27742 **merged upstream 2026-08-27** |

**These upgrades start a new series. Do not pool Codex rows across 0.148/0.150.**

**Three llama.cpp worktrees, do not confuse them:**

| path | commit | purpose |
|---|---|---|
| `~/git/llama.cpp` | **`d7bd3bfca` (mainline master)** | qwen4exp, now merged upstream. The old pinned build is tagged **`benchmark-pr27742-2026-08-26`** -- the PR was squash-merged, so its commits are NOT in mainline history and the tag is the only way back to the exact build every earlier `qwen38fnq2`/`q3` row used. |
| `~/git/llama.cpp-glm52pr` | `8a8d0bcc4` (PR #27752) | serves `glm53`. Clean, unpatched. |
| `~/git/llama.cpp-glm53` | `9370c82db` (PR #27773) | the failed attempt, **166 lines of uncommitted patches**. Two are independently upstream-worthy (#25). Do not build GLM here. |

**Weights on disk:** `~/models/Qwen3.8-Flash-Next-GGUF` (157 GB, Q2 + Q3),
`~/models/GLM-5.3-Flash-GGUF` (101 GB, Unsloth Q2),
`~/git/ds4/gguf/GLM-5.3-Flash-Q2.gguf` (90 GB, antirez -- **unusable**, no engine
loads it; keep per the archive convention or delete deliberately).

**Disk, measured 2026-08-28** (`benchmarks/disk/RESULTS.md`): sequential
9.45 GiB/s, random 1 MiB 198 us, random 4 KiB **61 us**. A 100-byte lookup costs
one 4 KiB block -- there is no smaller unit.

**Codex profiles** in `~/.codex/*.config.toml` are not in git. All need
`wire_api = "responses"`; 0.148.0 removed `"chat"`.

## Traps worth not rediscovering

**Write results through `results.py`.** Never hand-roll an exclusion filter --
five different keys have meant "untrustworthy row", and an analysis that checked
one silently counted fifteen bad rows as good data (#29).

**`/health` answers before the model is loaded.** GLM answered at 4 s and did not
finish loading until 33 s. A request in that window returns no `choices` and
looks exactly like a broken model.

**Coherence-check at temperature 0 before every benchmark.** A model can load,
serve, and report plausible token counts while emitting noise -- that is #25, and
it cost hours.

**A timeout is a failure, not an absence.** It writes `error` and no `passed`
key. Read verdicts with `results.verdict()` and denominators with
`results.trials()`; `if "passed" in row` silently shrinks the denominator and
turned a 13/16 backend into a published 13/13.

**Do not poll `pgrep -f 'benchmarks/agent/run.py'` from a shell that waits on
it.** The waiter's own command line matches, so the loop never exits.

**Do not run anything else during a timing batch.** A 96 GB download overlapped
one and produced an hour of chasing a regression that did not exist. The same
mistake hides better when it is a *server*: weights stay resident whether or not
anyone is using them. Run `uv run python benchmarks/agent/preflight.py` before
every batch -- it names any server holding memory that this run does not want.
`run.py` warns too, but by then the second server is already started.

**Nothing may feed into `results.verdict()` except the oracle.** Gates, hashes
and the verbatim check ride alongside a verdict and never into it; there is a
test asserting a filthy solution and a clean one get the same verdict. The
moment a quality signal decides a pass, the harness is judging, and its whole
claim is that it does not.

**A 3-trial median is not a speed measurement -- it carries +/-28%.** Measured,
not estimated (#23): three trials pin one task's median to +/-27.9% and a
five-task suite total to +/-12.9%. So two task medians need to differ by ~56%,
and two suites by ~26%, before the difference is real. The cause is #26: the
server samples at temperature 1.0 with a random seed, and the model sometimes
writes 7x the tokens for the same task. **Below 26% at n=3, write "no difference
measured", never "X is faster than Y."** Ranking on speed needs 10 trials, and
20 to separate backends within 10% of each other.
