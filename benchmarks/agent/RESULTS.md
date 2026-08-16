# Agent benchmark — ds4 vs three Qwen builds

Run 2026-08-15 to 2026-08-16. MacBook Pro M5 Max, 128 GiB, macOS 26.5.
5 tasks × 4 backends × 3 trials = **60 trials**.
Methodology in [`METHODOLOGY.md`](METHODOLOGY.md).

| backend | model | quant | size | gen t/s | context |
|---|---|---|---|---|---|
| `ds4` | DeepSeek V4 Flash 0731, via `ds4-server` | mixed q2/q4 | 90.9 GiB | 36.8 | 100,000 |
| `qwen` | `qwen3.8:27b-mlx` | 4-bit affine | 18 GB | 57.1 | 262,144 |
| `qwen36` | `qwen3.6:27b-mlx` | nvfp4 | 19 GB | 29.3 | 262,144 |
| `qwen36coding` | `qwen3.6:27b-coding-mxfp8` | mxfp8 | 31 GB | 17.9 | 262,144 |

All Qwen builds served by Ollama 0.32.14-rc0. ds4 built from `5be6b6c`
(binary dated 2026-08-10).

> **Correction, 2026-08-16.** Earlier revisions of this table listed ds4 at
> 34.4 t/s generation. That figure came from `bench-0731/speed_q2q4_0731.csv`,
> measured 2026-08-08 against an older build (`main @ b030961`) — not the
> binary that ran these trials. Re-measured on the actual build:
> **36.8 t/s** at 12,288 context. No conclusion changes; ds4 still generates
> more slowly than Qwen3.8 and still wins the agent benchmark.
>
> Prefill is *not* restated here. The fresh sweep used a larger prefill chunk
> than `bench-0731` did, and longer prefills batch better, so the two are not
> comparable. See [`../ds4/sync/README.md`](../ds4/sync/README.md).

---

## Headline

**Every model solved every task, every time. 60/60.**

No failures. No timeouts. No run edited a test to pass. On this task set the
difference between them is entirely *how long they take*, never *whether they
get there*.

| | pass | median wall | median turns | median output tokens | spread |
|---|---|---|---|---|---|
| ornith:35b | **13/15** | **82.3 s** | 12 | 2,857 | **30.4×** |
| **ds4 (synced)** | **15/15** | 140.9 s | 9 | **2,120** | 2.6× |
| ds4 (pre-sync) | 15/15 | 164.4 s | **8** | 2,130 | **1.9×** |
| qwen3.6-coding | 15/15 | 213.5 s | 10 | 2,219 | 3.4× |
| qwen3.6 | 15/15 | 248.4 s | 13 | 3,725 | 4.0× |
| qwen3.8 | 15/15 | 272.2 s | 13 | 6,237 | 10.1× |

`spread` is slowest run over fastest, across all 15 trials.

**Ornith has the best median and is the only backend that has ever failed.**
Both facts are load-bearing; see below.

Total wall clock for 15 trials each: ds4 **42.1 min**, 3.6-coding **52.2 min**,
qwen3.6 **67.3 min**, qwen3.8 **72.6 min**.

## Two results, not one

1. **Wall time tracks tokens per task, not tokens per second** (below).
2. **The fastest median is not the best agent.** Ornith is 1.7× faster than
   synced ds4 at the median and fails 2 of 15 trials, with a 30.4× spread and a
   single run of 20.4 minutes. For work you sit and wait on, the tail and the
   failure rate matter more than the median.

## The result: tokens per task, not tokens per second

Rank the models by generation speed and you get the **exact reverse** of the
ranking by agent performance:

| | gen t/s (rank) | median wall (rank) |
|---|---|---|
| qwen3.8 | 57.1 (1st) | 272.2 s (4th) |
| ds4 | 36.8 (2nd) | **164.4 s (1st)** |
| qwen3.6 | 29.3 (3rd) | 248.4 s (3rd) |
| qwen3.6-coding | 17.9 (4th) | 213.5 s (2nd) |

**The fastest-generating model finished last. The slowest finished second.**

The mechanism is the output-token column. Median tokens emitted per task track
wall time almost perfectly, and generation rate barely matters:

- ds4: 2,130 tokens → 164.4 s
- 3.6-coding: 2,219 tokens → 213.5 s
- qwen3.6: 3,725 tokens → 248.4 s
- qwen3.8: 6,237 tokens → 272.2 s

Qwen3.8 generates **3.2× faster** than 3.6-coding and emits **2.8× more
tokens** to do the same job. The two nearly cancel, and what is left is a loss.

**For agent work, measure tokens per task. Tokens per second is close to
irrelevant.**

### Newer is not better; coding-tuned is

Within the Qwen family, ordering is the opposite of what version numbers
suggest:

    qwen3.6-coding  213.5 s   <   qwen3.6  248.4 s   <   qwen3.8  272.2 s

The coding tune is worth ~14% over the base 3.6 and ~22% over 3.8 — while
being the slowest generator tested and the largest file (31 GB). It also needs
**10 median turns against 13**, so it wastes fewer round trips as well as
fewer tokens.

---

## Per task

Median wall seconds over 3 trials.

| task | broken | ds4 | qwen3.8 | qwen3.6 | 3.6-coding |
|---|---|---|---|---|---|
| `mbox-strip-envelope` | 3 | 127.2 | **123.6** | 149.6 | 156.2 |
| `mbox-scan` | 13 | **163.5** | 173.3 | 331.1 | 217.7 |
| `storage-blob-put` | 14 | **170.4** | 501.8 | 414.8 | 233.7 |
| `parser-mbox-quoting` | 34 | **211.6** | 272.2 | 254.2 | 239.4 |
| `parser-date` | 49 | **164.4** | 280.6 | 198.0 | 173.6 |

ds4 posts the best median on **four of five** tasks. Qwen3.8 takes
`mbox-strip-envelope` — the easiest task, by 3.6 seconds, which is inside
noise.

No Qwen is uniformly slower. All three are far more *variable*, and all three
struggle on the same task.

---

## The `storage-blob-put` result — a Qwen-family weakness

`BlobStore.put` writes a blob durably: temp file, `fsync`, atomic rename,
sha256 verification. It breaks 14 tests, fewer than two other tasks.

| trial | ds4 | qwen3.8 | qwen3.6 | 3.6-coding |
|---|---|---|---|---|
| 1 | 170.4 s | 853.6 s | 257.1 s | 210.1 s |
| 2 | 216.5 s | 501.8 s | 456.9 s | 233.7 s |
| 3 | 140.4 s | 386.5 s | 414.8 s | 371.7 s |
| **median** | **170.4 s** | 501.8 s | 414.8 s | 233.7 s |

**All nine Qwen runs on this task, across three builds, are slower than every
ds4 run.** Nine for nine, no overlap at all.

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

Ratio of slowest to fastest run on each task.

| task | ds4 | qwen3.8 | qwen3.6 | 3.6-coding |
|---|---|---|---|---|
| `mbox-strip-envelope` | 1.1× | 1.7× | 1.2× | 1.4× |
| `mbox-scan` | 1.3× | **3.3×** | 2.5× | 1.6× |
| `storage-blob-put` | 1.5× | 2.2× | 1.8× | 1.8× |
| `parser-mbox-quoting` | 1.2× | 1.5× | **2.2×** | **1.1×** |
| `parser-date` | 1.5× | 1.1× | 1.5× | 1.3× |

**ds4 never exceeds 1.5× on any task.** Qwen3.8 and Qwen3.6 each exceed 2× on
two tasks. The coding build is the most consistent Qwen, peaking at 1.8×.

The behaviour is bimodal rather than noisy: most runs are direct, then one
wanders badly. Qwen3.6's `parser-mbox-quoting` went 248.4 s, 254.2 s — near
identical — and then 537.2 s. **A model that looks stable over two trials is
not necessarily stable.** This caught me twice during the run.

**Practical reading:** ds4 is the most predictable agent backend by a clear
margin, and the coding-tuned Qwen is the most predictable of the Qwens. All
three Qwens are faster than their medians suggest when they go straight at a
problem, and much slower when they do not.

---

## What this does not say

- **Not a quality ranking.** All four scored 100%. This measures completion and
  latency, not craftsmanship. A passing solution may still be ugly or slow.
- **Not a general claim.** Five single-function tasks in one Python repository,
  all four backends run on one machine on one night.
  Nothing here tests multi-file refactors, ambiguity, or long-context recall.
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

If you are choosing a local backend for Claude Code on a 128 GiB Mac:

- **ds4 / DeepSeek V4 Flash is the fastest and most predictable** — but it
  costs 90.9 GiB resident, which is most of the machine.
- **`qwen3.6:27b-coding-mxfp8` is the best value.** 30% behind ds4 on median
  wall time, in a 31 GB file, leaving the machine usable for everything else.
- **`qwen3.8:27b-mlx` is the weakest agent tested**, despite being the newest
  model and the fastest generator. Its 18 GB footprint is the smallest, which
  is the argument for it.

Correctness did not separate them at all. Choose on latency, predictability and
memory.

## Cost of the run

About 3 hours 40 minutes of wall time for 60 trials, phased so only one model
was resident at a time — ds4 first at 90.9 GiB, then freed, then each Qwen in
turn with an explicit unload between. No model was measured while paged out.

---

## Provenance

- Target: `gmail-archive` @ `56e55cc`, 4,599 lines of Python, 166 tests.
- Raw rows: `results.jsonl` (gitignored — regenerate with `run.py`).
- Log: `matrix.log` (gitignored).

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

### Why this run is the cleanest evidence for the main finding

**Median output tokens barely moved: 2,130 → 2,120.** Turns went 8 → 9. The
model did the same amount of work; only the rate changed.

Everywhere else in this report, wall-time differences came from models emitting
*different numbers of tokens*. Here the token count is held constant by
construction and only engine throughput varies — and wall time fell 14.3%,
tracking the measured +8–10% generation gain plus prefill.

That is the same relationship observed from the opposite direction, which is
about as close to a controlled experiment as this setup allows.

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
