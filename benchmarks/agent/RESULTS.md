# Agent benchmark — ds4 vs three Qwen builds

Run 2026-08-15 to 2026-08-16. MacBook Pro M5 Max, 128 GiB, macOS 26.5.
5 tasks × 4 backends × 3 trials = **60 trials**.
Methodology in [`METHODOLOGY.md`](METHODOLOGY.md).

| backend | model | quant | size | gen t/s | context |
|---|---|---|---|---|---|
| `ds4` | DeepSeek V4 Flash 0731, via `ds4-server` | mixed q2/q4 | 90.9 GiB | 34.4 | 100,000 |
| `qwen` | `qwen3.8:27b-mlx` | 4-bit affine | 18 GB | 57.1 | 262,144 |
| `qwen36` | `qwen3.6:27b-mlx` | nvfp4 | 19 GB | 29.3 | 262,144 |
| `qwen36coding` | `qwen3.6:27b-coding-mxfp8` | mxfp8 | 31 GB | 17.9 | 262,144 |

All Qwen builds served by Ollama 0.32.14-rc0.

---

## Headline

**Every model solved every task, every time. 60/60.**

No failures. No timeouts. No run edited a test to pass. On this task set the
difference between them is entirely *how long they take*, never *whether they
get there*.

| | pass | median wall | median turns | median output tokens | worst spread |
|---|---|---|---|---|---|
| **ds4** | 15/15 | **164.4 s** | **8** | **2,130** | **1.5×** |
| qwen3.6-coding | 15/15 | 213.5 s | 10 | 2,219 | 1.8× |
| qwen3.6 | 15/15 | 248.4 s | 13 | 3,725 | 2.5× |
| qwen3.8 | 15/15 | 272.2 s | 13 | 6,237 | 3.3× |

Total wall clock for 15 trials each: ds4 **42.1 min**, 3.6-coding **52.2 min**,
qwen3.6 **67.3 min**, qwen3.8 **72.6 min**.

## The result: tokens per task, not tokens per second

Rank the models by generation speed and you get the **exact reverse** of the
ranking by agent performance:

| | gen t/s (rank) | median wall (rank) |
|---|---|---|
| qwen3.8 | 57.1 (1st) | 272.2 s (4th) |
| ds4 | 34.4 (2nd) | **164.4 s (1st)** |
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
