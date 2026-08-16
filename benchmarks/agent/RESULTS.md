# Agent benchmark — seven local backends driving Claude Code

Run 2026-08-15 to 2026-08-16. MacBook Pro M5 Max, 128 GiB, macOS 26.5.
5 tasks × 3 trials per backend, **106 trials**.
Methodology in [`METHODOLOGY.md`](METHODOLOGY.md). Raw rows in `results.jsonl`.

---

## Executive summary

**What was measured.** Whether a local model, driving Claude Code, can restore a
function deleted from a real 4,599-line Python repository — and how long it
takes. The repository's own 166 tests are the oracle: pass or fail, no rubric,
no judging model.

**Correctness barely separates them.** Six of seven backends scored 100%. Only
`ornith:35b` ever failed, twice, both on the same task. Choose on latency and
predictability, not accuracy — at least until the tasks get harder (issue #4).

| backend | pass | median wall | tokens | gen t/s | spread | resident |
|---|---|---|---|---|---|---|
| `ornith:35b` | 13/15 | **82.3 s** | 2,857 | **92.5** | **30.4×** | 21 GB |
| **`ds4` (synced)** | **15/15** | 140.9 s | **2,120** | 40.6 | 2.6× | 90.9 GiB |
| `ds4` (pre-sync) | 15/15 | 164.4 s | 2,130 | 36.8 | **1.9×** | 90.9 GiB |
| `qwen3.6:27b-coding-mxfp8` | 15/15 | 213.5 s | 2,219 | 17.9 | 3.4× | 31 GB |
| `qwen3.6:27b-mlx` | 15/15 | 248.4 s | 3,725 | 29.3 | 4.0× | 19 GB |
| `qwen3.8:27b-mlx` | 15/15 | 272.2 s | 6,237 | 57.1 | 10.1× | 18 GB |
| `gemma4:31b-mxfp8` | 16/16 | 355.4 s | 2,600 | 13.2 | 3.0× | 45 GB |

`spread` is the slowest run divided by the fastest. `tokens` and `wall` are
medians.

### The findings

1. **Wall time ≈ output tokens ÷ generation rate, plus per-turn overhead.**
   Neither term predicts on its own (r = +0.35 and −0.67); their ratio predicts
   almost perfectly (**r = +0.96**). Benchmarking a model's tokens/sec tells you
   little about how long it will take to finish a job.

2. **Newer is not better.** Within the Qwen family the ordering inverts the
   version numbers: 3.6-coding (213.5 s) beats 3.6 (248.4 s) beats 3.8
   (272.2 s). Qwen3.8 generates fastest of the Qwens and finishes last, because
   it emits 2.8× the tokens.

3. **The fastest median is not the best agent.** Ornith leads on median by 1.7×
   and is the only backend that has failed — twice on the *easiest* task — with
   a 30.4× spread and one run of 20.4 minutes.

4. **`storage-blob-put` is a Qwen-family weakness.** All nine Qwen runs on that
   task, across three builds, are slower than every ds4 run. It is unremarkable
   for Gemma, which is evidence the weakness is lineage-specific.

5. **Syncing ds4 with upstream bought 14.3%** — median 164.4 s → 140.9 s — with
   output tokens essentially unchanged (2,130 → 2,120). A clean engine-only
   improvement.

### Recommendation

For agentic coding on a 128 GiB Mac: **`ds4` (synced)**. It is the only backend
that is simultaneously perfect on correctness, second-fastest, and predictable
(worst case 2.6× its median). The cost is 90.9 GiB resident — most of the
machine.

If that footprint is unacceptable, **`qwen3.6:27b-coding-mxfp8`** is the value
pick: 31 GB, 15/15, 1.5× ds4's median.

**`ornith:35b`** is the speed pick with a real caveat — fastest by a distance,
but it is the only model that has silently produced wrong code, and its tail is
30×.

### Read these caveats before quoting anything

- **Quality is unmeasured, not equal.** A binary oracle cannot rank six
  backends that all pass. See "What this does not say".
- **Three trials detects large effects only.** Differences under ~20% on a
  single task are noise.
- **Ornith is served through llama.cpp, not MLX**, so its advantage confounds
  model with engine. Most of its win is generation rate, not conciseness.
- **Absolute times include an environment tax** — a fresh worktree has no
  `.venv`, so part of every number is the agent working out how to run pytest.
  Symmetric across backends, so comparisons hold.

---

## Backends

| backend | model | quant | size | gen t/s | context |
|---|---|---|---|---|---|
| `ds4` | DeepSeek V4 Flash 0731, via `ds4-server` | mixed q2/q4 | 90.9 GiB | 36.8 → 40.6 | 100,000 |
| `qwen` | `qwen3.8:27b-mlx` | 4-bit affine | 18 GB | 57.1 | 262,144 |
| `qwen36` | `qwen3.6:27b-mlx` | nvfp4 | 19 GB | 29.3 | 262,144 |
| `qwen36coding` | `qwen3.6:27b-coding-mxfp8` | mxfp8 | 31 GB | 17.9 | 262,144 |
| `ornith` | `ornith:35b`, agentic tune | Q4_K_M GGUF | 21 GB | 92.5 | 262,144 |
| `gemma4` | `gemma4:31b-mxfp8` | mxfp8 | 32 GB | 13.2 | 262,144 |

All Ollama backends served by 0.32.14-rc0. ds4 measured on two builds:
`5be6b6c` (pre-sync) and `fdcf3aa` (post-sync).

### Corrections to earlier revisions

Both are kept rather than quietly edited away.

1. **ds4 generation was listed as 34.4 t/s.** That came from
   `bench-0731/speed_q2q4_0731.csv`, measured 2026-08-08 against an older build
   — not the binary that ran these trials. Re-measured: **36.8 t/s** pre-sync.
   Prefill is deliberately not restated; the fresh sweep used a larger prefill
   chunk, so the two are not comparable.

2. **"Tokens per second is close to irrelevant" was too strong.** It held across
   the first four backends only because their generation rates spanned 3.2×
   while token counts spanned 2.9×. Adding Gemma (13.2 t/s) and measuring Ornith
   (92.5 t/s) widened the rate range to 7× and falsified it. See "What actually
   predicts wall time".

---


## Per task

Median wall seconds over 3 trials. Best per row in bold.

| task | broken | ornith | ds4 sync | ds4 pre | 3.6-cod | 3.6 | 3.8 | gemma4 |
|---|---|---|---|---|---|---|---|---|
| `mbox-strip-envelope` | 3 | **46.8**† | 115.0 | 127.2 | 156.2 | 149.6 | 123.6 | 210.2 |
| `mbox-scan` | 13 | 194.9 | **144.4** | 163.5 | 217.7 | 331.1 | 173.3 | 399.8 |
| `storage-blob-put` | 14 | **82.3** | 138.0 | 170.4 | 233.7 | 414.8 | 501.8 | 294.4 |
| `parser-mbox-quoting` | 34 | **102.3** | 194.1 | 211.6 | 239.4 | 254.2 | 272.2 | 459.4 |
| `parser-date` | 49 | **68.7** | 140.9 | 164.4 | 173.6 | 198.0 | 280.6 | 385.8 |

† Ornith passed only **1 of 3** attempts at `mbox-strip-envelope`; that median
is not comparable to the others. Every other cell in the table is 3/3.

`ds4 sync` wins the one task Ornith cannot do reliably, and is second everywhere
else. `gemma4` is last on four of five.

---

## The `storage-blob-put` result — a Qwen-family weakness

`BlobStore.put` writes a blob durably: temp file, `fsync`, atomic rename,
sha256 verification. It breaks 14 tests, fewer than two other tasks.

| trial | ds4 (pre) | qwen3.8 | qwen3.6 | 3.6-coding | ornith | gemma4 |
|---|---|---|---|---|---|---|
| 1 | 170.4 s | 853.6 s | 257.1 s | 210.1 s | 91.2 s | 270.0 s |
| 2 | 216.5 s | 501.8 s | 456.9 s | 233.7 s | 66.4 s | 294.4 s |
| 3 | 140.4 s | 386.5 s | 414.8 s | 371.7 s | 82.3 s | 431.5 s |
| **median** | **170.4 s** | 501.8 s | 414.8 s | 233.7 s | **82.3 s** | 294.4 s |

**All nine Qwen runs on this task, across three builds, are slower than every
pre-sync ds4 run.** Nine for nine, no overlap at all. Post-sync ds4 is faster
still, at a 138.0 s median.

The two non-Qwen backends behave completely differently. Ornith is *fastest*
here — 82.3 s median, its second-best task. Gemma is slow but this is not
its worst task; it is last on four of five and merely mid-table on this one.

That asymmetry is the evidence that the weakness is lineage-specific rather
than a property of durable-write code being intrinsically hard.

This is the strongest single-task result in the series, and it survived two
attempts to explain it away:

1. Qwen3.6 trial 1 (257.1 s) suggested the problem was specific to 3.8.
   Trials 2 and 3 refuted it — that was the fast tail of a wide distribution.
2. 3.6-coding trials 1 and 2 (210.1 s, 233.7 s) suggested the coding tune
   fixed it. Trial 3 came in at 371.7 s, 2.2× ds4's median.

The coding tune **halves the damage** but does not remove it. Every Qwen
variant eventually throws a long run here.

Durability semantics are a plausible trap: `fsync` ordering and atomic-rename
behaviour is exactly the sort of thing a model can keep re-verifying when the
tests do not pin it down.

The clearest look at what goes wrong is Qwen3.8 trial 1:

| | wall | turns | output tokens |
|---|---|---|---|
| ds4 (median) | 170.4 s | 8–11 | 1,742–3,865 |
| qwen3.8 trial 1 | 853.6 s | **35** | **24,970** |

3× the turns and 7× the tokens for the same passing result. That is thrashing,
not slow generation, and far too large to be the empty-virtualenv tax described
in the methodology.

---

## Variance

Slowest run divided by fastest, per task.

| task | ornith | ds4 sync | ds4 pre | 3.6-cod | 3.6 | 3.8 | gemma4 |
|---|---|---|---|---|---|---|---|
| `mbox-strip-envelope` | 3.4× | 1.6× | 1.1× | 1.5× | 1.2× | 1.7× | 1.7× |
| `mbox-scan` | **7.0×** | 1.7× | 1.3× | 1.6× | 2.5× | 3.3× | 1.2× |
| `storage-blob-put` | 1.4× | 1.8× | 1.5× | 1.8× | 1.8× | 2.2× | 1.6× |
| `parser-mbox-quoting` | 1.5× | 1.2× | 1.2× | 1.1× | 2.2× | 1.5× | 1.4× |
| `parser-date` | 1.3× | 1.0× | 1.5× | 1.3× | 1.5× | 1.1× | 1.5× |
| **overall** | **30.4×** | 2.6× | **1.9×** | 3.4× | 4.0× | 10.1× | 3.0× |

Per-task spreads understate the problem: a model can be consistent within a task
and wildly inconsistent across them. Ornith's per-task figures look reasonable
apart from `mbox-scan`, yet its overall range is 40.3 s to 1,226.0 s.

**ds4 is the most predictable backend**, pre- or post-sync. Gemma is
mid-field — slow, but not erratic. Qwen3.8 is the least predictable of the
non-Ornith backends at 10.1×.

The behaviour is bimodal rather than noisy: most runs are direct, then one
wanders badly. Qwen3.6's `parser-mbox-quoting` went 248.4 s, 254.2 s — near
identical — and then 537.2 s. **A model that looks stable over two trials is
not necessarily stable.** This caught me twice during the run.

---

## What this does not say

- **Not a quality ranking.** Six of seven backends scored 100%. This measures
  completion and latency, not craftsmanship. A passing solution may still be
  ugly, slow or insecure. Quality is **unmeasured, not equal** — see issue #4.
- **Not a general claim.** Five single-function tasks in one Python repository,
  all backends run on one machine over two days. Nothing here tests multi-file
  refactors, ambiguity, or long-context recall.
- **Three trials detects large effects only.** The `storage-blob-put` gap
  (nine Qwen runs, none overlapping ds4) and the output-token ordering are
  large and consistent enough to believe. Differences under ~20% on a single
  task are not — `mbox-strip-envelope` at 127.2 s vs 123.6 s is a tie, not a
  Qwen3.8 win.
- **Absolute times carry an environment tax.** A fresh worktree has no `.venv`,
  so part of every number is the agent working out how to run pytest. This is
  symmetric across backends, so the comparison holds, but the absolute figures
  are inflated. See METHODOLOGY §9.

---

## Practical recommendation

See the [Executive summary](#executive-summary). In short: **ds4 (synced)** for
correctness and predictability, **`qwen3.6:27b-coding-mxfp8`** if 90.9 GiB is
too much machine to give up, **`ornith:35b`** only if you can tolerate a model
that is occasionally confidently wrong.

Correctness did not separate them. Choose on latency, predictability and memory.

## Cost of the run

Roughly 7 hours of wall time for 106 trials, phased so only one model was
resident at a time — ds4 at 90.9 GiB, freed before each Ollama backend, and an
explicit unload between backends. **No model was measured while paged out.**

Per backend: ds4 42.1 min (pre-sync) and 37.0 min (post-sync), 3.6-coding
52.2 min, qwen3.6 67.3 min, qwen3.8 72.6 min, ornith ~46 min, gemma4 ~89 min.

---

## Provenance

- Target: `gmail-archive` @ `56e55cc`, 4,599 lines of Python, 166 tests.
- Raw rows: `results.jsonl` — **tracked in git**. Every row carries its own
  environment capture (Claude Code version, Ollama version, model digest, ds4
  commit, machine, OS), so old rows stay interpretable.
- Logs: `matrix.log`, `matrix36.log`, `ornith.log`, `gemma4.log`,
  `rerun_postsync.log` — also tracked.
- Engine speed sweeps for the sync: `../ds4/sync/`.

### One cell was re-run, and why

The `mbox-scan` / qwen / trial 3 cell is recorded **twice**. The first attempt
is retained in `results.jsonl` with `"excluded": true` and a reason; it is
skipped by `summarize.py` and excluded from every number above.

What happened: `run_matrix.sh` (the shell wrapper) exited while its child
`run.py` was still working. A liveness check on the wrapper therefore reported
the matrix as finished when it was not, and a second `mbox-scan` / qwen trial
was started by hand. The two competed for the same Ollama instance for 19
minutes, and the original hit its 2400 s timeout as a direct result.

**That timeout measures process contention, not the model.** It is retired
rather than deleted, because deleting it would hide a real event from the
record — the same rule applied to logs elsewhere in this repo. The replacement
ran with the machine otherwise idle and passed in 281.4 s.

Two harness fixes came out of it:

- `run.py` now removes a stale worktree left by an aborted run, instead of
  failing every later attempt at that cell with `fatal: ... already exists`.
- `summarize.py` honours an `excluded` key, so a retired row can stay in the
  data without contaminating the statistics.

---

## Series 2: the upstream sync (2026-08-16)

`ds4` was synced with `antirez/ds4` upstream — 32 commits, merged clean, rebuilt
as `fdcf3aa`. The haul included a large M5-specific decode optimization campaign
and two fixes that land on paths this benchmark uses: `metal: fix long-context
prefill and decode correctness` and `server: recover truncated DSML tool calls`.

Engine speed, identical sweep settings on both builds, minutes apart:

| ctx | prefill pre → post | generation pre → post |
|---|---|---|
| 8192 | 679.2 → 661.1 (−2.7%) | 37.3 → **40.3 (+7.9%)** |
| 12288 | 640.2 → 632.9 (−1.1%) | 36.8 → **40.6 (+10.4%)** |
| 16384 | 614.8 → 599.6 (−2.5%) | 36.4 → **39.9 (+9.6%)** |

Agent benchmark, 15 trials each side:

| task | pre | post | delta |
|---|---|---|---|
| `mbox-strip-envelope` | 127.2 s | 115.0 s | −9.6% |
| `parser-mbox-quoting` | 211.6 s | 194.1 s | −8.3% |
| `storage-blob-put` | 170.4 s | 138.0 s | **−19.0%** |
| `parser-date` | 164.4 s | 140.9 s | −14.3% |
| `mbox-scan` | 163.5 s | 144.4 s | −11.7% |
| **overall median** | **164.4 s** | **140.9 s** | **−14.3%** |

### A controlled test of the generation-rate term

**Median output tokens barely moved: 2,130 → 2,120.** Turns went 8 → 9. The
model did the same amount of work; only the rate changed.

That isolates one term of `wall ≈ tokens ÷ rate + overhead`. Token count is held
constant by construction, generation rose 8–10%, and wall time fell 14.3%.

Across backends both terms move at once, which is what made the original
tokens-only reading look sufficient. Here only the denominator moves, and it
moves wall time — direct evidence that generation rate is not the irrelevant
variable this report once called it.

Sync was worth it on both counts: correctness fixes on the tool-call path, and
ds4's lead over the next-best backend widens from 1.30× to 1.52×.

---

## Ornith: fastest median, only failures

`ornith:35b` is a 34.7B MoE in 21 GB, MIT licensed, with an agentic system
prompt baked into the model: *"Think step by step in a reasoning block, then
act. Use the provided tools when they help. Be concise, correct, and direct."*

It is the fastest backend measured, and the only one that has ever failed.

| | value |
|---|---|
| median | **82.3 s** — 1.7× faster than synced ds4 |
| fastest run | **40.3 s** — fastest single run in the project |
| slowest run | **1,226.0 s** — slowest single run in the project |
| spread | **30.4×** |
| pass rate | **13/15** |

### The failures are not random

Both failures hit **`mbox-strip-envelope`**, the *easiest* task in the set
(3 broken tests), and both produced the identical result: `3 failed, 13 passed`.
Neither touched the tests.

| trial | result | wall | turns | output tokens |
|---|---|---|---|---|
| 1 | PASS | 40.3 s | 12 | 1,636 |
| 2 | **FAIL** | 46.8 s | 13 | 1,940 |
| 3 | **FAIL** | 138.2 s | 33 | 4,973 |

So it fails **2 of 3 attempts at the simplest task** while passing every harder
one. Trial 3 rules out haste as the explanation: 33 turns and 4,973 tokens — its
most effortful run anywhere — and it still got the same three tests wrong. This
is a blind spot, not carelessness.

### And it is capable of getting stuck

`mbox-scan` ran 176.3 s, then **1,226.0 s** — 20.4 minutes, 8.5× synced ds4's
median on that task.

### Reading

Ornith holds the fastest run, the only failures, and the longest run, all within
15 trials. That is one coherent behaviour rather than three quirks: a model tuned
hard for terseness commits early. Usually that is right and very fast. When it is
wrong, it either ships a wrong answer quickly or cannot deliberate its way back.

**Caveat that does not go away:** Ornith is a Q4_K_M GGUF, served through
Ollama's llama.cpp path rather than MLX. Some of the speed advantage may be
engine, not model. A 1.7× median gap is larger than that plausibly explains on
its own, but the comparison is not clean and should not be presented as one.

### It also answered an open question

Before Ornith, all 75 trials across five backends passed, and this report said
the task set could not discriminate on correctness. It can. Ornith found a wall
the others walked past — which raises the value of issue #4 (harder tasks)
rather than lowering it.


---

## What actually predicts wall time

Median values per backend, all 106 trials.

| backend | tokens | gen t/s | tokens ÷ rate | wall | overhead |
|---|---|---|---|---|---|
| ornith | 2,857 | **92.5** | 30.9 s | **82.3 s** | 51.4 s |
| qwen3.8 | **6,237** | 57.1 | 109.2 s | 272.2 s | **163.0 s** |
| ds4 (synced) | 2,120 | 40.6 | 52.2 s | 140.9 s | 88.7 s |
| ds4 (pre-sync) | 2,130 | 36.8 | 57.9 s | 164.4 s | 106.5 s |
| qwen3.6 | 3,725 | 29.3 | 127.1 s | 248.4 s | 121.3 s |
| qwen3.6-coding | 2,219 | 17.9 | 124.0 s | 213.5 s | 89.5 s |
| gemma4 | 2,600 | **13.2** | 196.9 s | **355.4 s** | 158.5 s |

```
correlation with wall time
  output tokens alone     r = +0.35
  generation rate alone   r = -0.67
  tokens / rate           r = +0.96
```

**`gemma4` is the case that forced this.** It emits *fewer* tokens than qwen3.8
(2,600 vs 6,237) and uses the fewest turns of any backend (7), yet is the
slowest overall — because it generates at 13.2 t/s.

`overhead` is wall time minus generation time: tool calls, prefill, and the
agent's own round trips. It is where thrashing shows up. Qwen3.8's 163 s is the
worst in the set and matches its behaviour on `storage-blob-put`.

### This also revises the Ornith story

Ornith generates at **92.5 t/s**, the fastest measured here — and it emits
*more* tokens than ds4 (2,857 vs 2,120). Its win is therefore substantially
**engine and architecture**, not the terseness its system prompt advertises.

It is a 34.7B MoE served through Ollama's llama.cpp path rather than MLX, so
both sparsity and runtime favour it. The GGUF caveat attached to this backend is
not a formality; it accounts for most of the gap.

---

## Gemma 4: slow, but well behaved

`gemma4:31b-mxfp8` is the only non-Qwen, non-DeepSeek model tested, and the
reason it was run (issue #1) was to check whether these findings are a property
of agent loops or of one model family.

| | value |
|---|---|
| pass rate | **16/16** |
| median | 355.4 s — slowest of any backend |
| spread | 3.0× — mid-field, no wild tails |
| turns | **7** — fewest of any backend |
| tokens | 2,600 — second fewest |
| generation | 13.2 t/s — slowest of any backend |

It is last on **every** task, consistently 2–3× synced ds4, while never failing
and never producing an outlier run. Uniformly slow rather than erratic.

One structural detail worth recording: for every Qwen build, `storage-blob-put`
was the standout worst task. For gemma4 it is unremarkable — just another slow
task. That is a further piece of evidence that the durability weakness is
Qwen-specific rather than universal.

Resident footprint was **45 GB** — 32 GB of weights plus ~13 GB of KV at the
262,144-token context. The largest of any Ollama backend tested.

*(16 rows rather than 15: a single-trial smoke test was run first to confirm
tool calling worked before committing to the full matrix, and it is retained.)*
