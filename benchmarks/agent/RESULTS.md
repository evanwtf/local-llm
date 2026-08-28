# Agent benchmark — local backends driving a coding agent

MacBook Pro M5 Max, 128 GiB, macOS 26.5. Methodology in
[`METHODOLOGY.md`](METHODOLOGY.md). Raw rows in `results.jsonl`.

This file is written in layers: the first sections report the original run of
**8 backends × Claude Code, 243 trials, 2026-08-15 to 08-17**, and later dated
sections append clients and backends to it. The running total is now **398
trials, 13 backends, 3 clients**, plus a hosted reference.

**Read "Corrections to earlier revisions" before quoting any figure from the
older sections.** Two reader bugs found on 2026-08-28 mean some numbers below
are known-wrong and are marked there rather than silently edited. For current
verdicts see [`../../RECOMMENDATIONS.md`](../../RECOMMENDATIONS.md); regenerate
any table with `uv run benchmarks/agent/summarize.py`.

---

## Executive summary

**What was measured.** Whether a local model, driving Claude Code, can restore a
function deleted from a real 4,599-line Python repository — and how long it
takes. The repository's own 166 tests are the oracle: pass or fail, no rubric,
no judging model.

**Correctness barely separates them.** Seven of eight backends scored 100%. Only
`ornith:35b` ever failed, twice, both on the same task. Choose on latency and
predictability, not accuracy — at least until the tasks get harder (issue #4).

| backend | pass | median wall | tokens | gen t/s | spread | resident |
|---|---|---|---|---|---|---|
| `ornith:35b` | 13/15 | **82.3 s** | 2,857 | **92.5** | **30.4×** | 21 GB |
| **`ds4` (synced, `fdcf3aa`)** | **15/15** | 140.9 s | **2,120** | 40.6 | 2.6× | 90.9 GiB |
| `ds4` (pre-sync, `5be6b6c`) | 15/15 | 164.4 s | 2,130 | 36.8 | **1.9×** | 90.9 GiB |
| `qwen3.6:27b-coding-mxfp8` | 15/15 | 213.5 s | 2,219 | 17.9 | 3.4× | 31 GB |
| `Qwen3.8-27B-MTPLX-Optimized-Speed` | 16/16 | 226.4 s | **2,026** | n/m | 3.5× | 26.8 GiB |
| `qwen3.6:27b-mlx` | 15/15 | 248.4 s | 3,725 | 29.3 | 4.0× | 19 GB |
| `qwen3.8:27b-mlx` | 15/15 | 272.2 s | 6,237 | 57.1 | 10.1× | 18 GB |
| `gemma4:31b-mxfp8` | 16/16 | 355.4 s | 2,600 | 13.2 | 3.0× | 45 GB |

`spread` is the slowest run divided by the fastest. `tokens` and `wall` are
medians. `n/m` = not measured: no standalone decode benchmark was run for
MTPLX, and its trials show an unexplained downward trend across rounds, so its
placement is provisional. See [MTPLX](#mtplx-the-same-weights-on-a-different-engine-2026-08-17).

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

5. **The engine matters as much as the model.** The same Qwen3.8-27B weights
   run 17% faster and emit 68% fewer tokens under MTPLX than under Ollama
   (226.4 s / 2,026 tokens vs 272.2 s / 6,237). The model card's "2-3× faster"
   claim measures decode throughput; on a whole agent loop the gain is 17%.

6. **The agent client matters more than the backend.** Driving the same ds4
   model, Claude Code passed 14/15 and OpenCode 6/15. All nine OpenCode
   failures returned the test suite *exactly* as the excision left it — the
   loop stopped believing it was done. Wall time differed by only 15%. Every
   backend ranking above is therefore a statement about *backend plus Claude
   Code*, not about the backend alone.

   > **Superseded 2026-08-28.** The 6/15 counted rows already marked
   > `confound`. At full sample OpenCode on ds4 is **13/29**. The finding
   > holds; the figure does not. Correction 3.

7. **Codex matched Claude Code exactly and was 12% faster.** 15/15 for both,
   with Codex the more consistent (2.9× spread vs 4.7×) — while running
   *without* metadata for the model it was driving. Combined with OpenCode's
   6/15, this shows client quality varies enormously between third-party
   harnesses; "not Claude Code" explains nothing.

   > **Superseded 2026-08-28.** The 12% came from 15 trials per cell. At 76 and
   > 36 trials the two clients are **7 seconds apart over a five-task suite**
   > (982 s / 75-of-76 against 975 s / 36-of-36) with overlapping intervals —
   > indistinguishable on ds4. What survives is that *OpenCode* differs from
   > both, not that Codex beats Claude Code.

8. **No harness is best everywhere — the pairing is the unit.** Codex beat
   Claude Code by 12% on ds4 and lost to it by 63% on Qwen/Ollama, same day,
   same tasks. Claude Code was consistent on both. Pick the client for the
   backend, not in the abstract.

   > **Revised 2026-08-28, and the conclusion got stronger.** The ds4 leg of
   > this is void — the clients are indistinguishable there. But llama.cpp,
   > measured later, is a 4.2× gap the other way, with Claude Code timing out.
   > The spread across engines is now 41% one way and 4.2× the other.

9. **Local takes ~5x the wall time of hosted.** Opus 5 through the same
   harness finished the suite in 203 s against 1,116 s for the best local
   pairing — **18% of the time**, consistent across all five tasks (14-23%).
   Read for scale only: Opus wrote this repo, so its pass rate is
   contaminated. That gap is the price of the hedge.

10. **Syncing the ds4 engine with upstream bought 14.3%** — median 164.4 s →
   140.9 s — with output tokens essentially unchanged (2,130 → 2,120). Same
   weights, same tasks, same machine; only the engine binary changed. A clean
   engine-only improvement, and the cleanest test of the rate term above.

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

All Ollama backends served by Ollama 0.32.14-rc0.

### "pre-sync" and "synced" — what those mean

`ds4` is a fork ([`evanwtf/ds4`](https://github.com/evanwtf/ds4)) of
[`antirez/ds4`](https://github.com/antirez/ds4), an open-source DeepSeek V4
Flash inference engine. It was measured on **two builds of the engine**, before
and after merging upstream changes. The model weights, the tasks, the machine
and the harness are identical across both; only the engine binary differs.

| | **pre-sync** | **synced** (a.k.a. post-sync) |
|---|---|---|
| fork commit | `5be6b6c` | `fdcf3aa` |
| commit date | 2026-08-15 | 2026-08-16 |
| binary built | 2026-08-10 | 2026-08-16 06:53 |
| trials run | 2026-08-15 | 2026-08-16 |
| generation | 36.8 t/s | **40.6 t/s** |
| median wall | 164.4 s | **140.9 s** |

`fdcf3aa` is the merge commit that brought in **32 commits from upstream
`antirez/ds4`**, up to `84cc882`. The merge was clean, with no conflicts. The
haul contained a large M5-specific decode optimization campaign plus two fixes
on paths this benchmark exercises: `metal: fix long-context prefill and decode
correctness`, and `server: recover truncated DSML tool calls`.

Both builds appear in the tables because the sync landed mid-project, and
replacing the earlier numbers would have discarded a controlled before/after on
an engine change. See [Series 2](#series-2-the-upstream-sync-2026-08-16).

Every row in `results.jsonl` records which build produced it, as
`env.ds4_head`, so the two are separable in the raw data.

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

   It was also too *weak* in one direction, established 2026-08-27:
   `Qwen3.8-Flash-Next` at `UD-Q3_K_XL` decodes **slower per token** than the
   2-bit quant and finishes the suite **28.4% faster**, on all five tasks. A
   tokens/sec reading does not merely under-predict here; it inverts the
   ranking.

3. **Every OpenCode number below this line was computed over confounded rows**
   (added 2026-08-28). `summarize.py` filtered exclusions with a hand-rolled
   `r.get("excluded")` and did not know the legacy `confound` and
   `contaminated` keys, so it counted **fourteen** rows already marked
   untrustworthy — thirteen of them `ds4 × opencode`. Recomputed through
   `results.py`, OpenCode on ds4 is **13/29 (44.8%, CI 28–62%)**, not the 6/15
   quoted in the sections that follow. The direction of the finding survives;
   the figures in "Claude Code vs OpenCode, controlled" do not. Fixed in
   e85ca07; see #29.

4. **A timeout was not being counted as a failure** (added 2026-08-28). A
   timed-out trial writes `error` and no `passed` key, and every reader that
   tested `"passed" in row` dropped it from the denominator rather than counting
   it. `qwen38fnq2 × claude` is **13/16 (81.2%)**, not 13/13. `results.py` now
   exposes `verdict()` and `trials()`; nothing should test `row["passed"]`
   directly again.

---


## What the tests actually are

Each trial deletes the **body** of one function from a real repository, leaving
its signature, type annotations and docstring in place, and replaces the body
with `raise NotImplementedError("removed for benchmark")`. The excision is
committed, so the original is not recoverable from the working tree.

The agent is given the repository and one instruction. This is the complete
prompt for `storage-blob-put`; the others differ only in the name and path:

> `BlobStore.put` in src/gmail_archive/storage.py has been removed and replaced
> with a NotImplementedError. Implement it so the existing test suite passes.
> Do not modify any test.

**The prompt deliberately does not say which tests cover it.** Finding them is
part of the task. Nothing else is provided: no hints, no examples, no
description of the algorithm beyond the docstring that was already there.

The agent then has full tool access — read, edit, run shell commands — in a
throwaway git worktree. When it stops, the repository's own test suite runs.
That is the only thing that decides pass or fail.

### The target repository

[`gmail-archive`](https://github.com/evanwtf/gmail-archive) — a real,
working project that ingests a Gmail Takeout mbox export into Postgres and blob
storage. 4,599 lines of Python, 166 tests that run in 4.7 s.

It was chosen because the tests are fast (they run twice per trial), passing
(so a failure is unambiguous), and written months before this benchmark existed
for reasons unrelated to it — so they encode a real contract rather than one
designed to be gameable.

### The five tasks

| task | function | what it must do | tests broken |
|---|---|---|---|
| `mbox-strip-envelope` | `mbox.strip_envelope` | Strip the `From_` envelope line from a raw mbox message, returning the RFC822 headers and body. Must handle a single-line message with no newline. | 3 |
| `mbox-scan` | `mbox.scan` | Memory-map an mbox file and return `(offset, length)` boundaries for every message in a single pass. Must handle a missing file and an empty file. | 13 |
| `storage-blob-put` | `storage.BlobStore.put` | Store bytes durably and return the sha256 digest — temp file, `fsync`, atomic rename, dedupe on an existing digest, and validate a caller-supplied digest. | 14 |
| `parser-mbox-quoting` | `parser.unquote_mbox` | Reverse mbox `From_` quoting, returning `(unquoted, ambiguous)`. Must round-trip with `requote_mbox`, and flag lines whose quoting is genuinely ambiguous. | 34 |
| `parser-date` | `parser._date` | Parse an RFC 2822 date header into a `datetime`, appending structured warnings for missing, unparseable, and out-of-range values rather than raising. | 49 |

They span deliberately different kinds of work:

- **`strip_envelope`** is a pure byte transform with one edge case — the
  simplest thing in the set, and the only task any model has failed.
- **`scan`** is file I/O with `mmap` and boundary detection.
- **`BlobStore.put`** is durability semantics: `fsync` ordering, atomic rename,
  content addressing. This is the task that separated the Qwen family.
- **`unquote_mbox`** carries an *invariant* — it must invert another function
  that the agent must find and read. The original docstring documents a real
  mboxrd-vs-mboxo ambiguity measured against a live export, so the contract is
  subtle rather than mechanical.
- **`_date`** is standard-library integration plus a warnings protocol; it
  breaks the most tests because the parser's main path depends on it.

"Tests broken" is how many of the repository's tests fail once the function is
removed — a rough proxy for blast radius, and the order the tables use.

### What the agent has to work out for itself

- Which test file covers the function, and how to run it. A fresh worktree has
  no `.venv`, so it must discover `uv run pytest` — see the environment-tax
  caveat.
- The contract, from the docstring, the callers and the tests.
- For `unquote_mbox`, that a *second* function exists which it must invert.
- When it is done. Nothing tells it the tests pass; it has to check.

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

- **Not a quality ranking.** Seven of eight backends scored 100%, and so did
  two of the three clients. This measures
  completion and latency, not craftsmanship. A passing solution may still be
  ugly, slow or insecure. Quality is **unmeasured, not equal** — see issue #4.
- **Not a general claim.** Five single-function tasks in one Python repository,
  all backends run on one machine over three days. Nothing here tests multi-file
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

Roughly 7 hours of wall time for the 106 trials of the backend series
(2026-08-15/16), phased so only one model was
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

Partway through the project, the `ds4` fork was synced with its upstream —
32 commits from `antirez/ds4` up to `84cc882`, merged clean into `fdcf3aa` on
2026-08-16 and rebuilt. Everything except the engine binary was held constant. The haul included a large M5-specific decode optimization campaign
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

Median values per backend, across the 106 trials of the backend series
(2026-08-15/16). The later client comparisons are excluded: they hold the
backend fixed and vary the client, so they do not belong in a per-backend table.

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

## MTPLX: the same weights on a different engine (2026-08-17)

`Youssofal/Qwen3.8-27B-MTPLX-Optimized-Speed` runs the **same Qwen3.8-27B
weights** as `qwen3.8:27b-mlx`, through a different inference engine. It is the
first backend here served by neither Ollama nor ds4-server.

Two things change together against the Ollama row, and they cannot be
separated:

- **The engine.** MTPLX drives the model's native multi-token-prediction head
  for self-speculative decoding — it drafts up to 3 tokens ahead and verifies
  them in one forward pass, with no separate draft model.
- **The quant.** Dynamic 4-bit: 4-bit at 32-weight groups for the bulk, 8-bit
  for embeddings, output head and final MLP blocks, 16-bit for norms and the
  entire MTP head. The author reports KL divergence 0.0220 to bf16 on coding,
  1.7× closer than their own flat-4-bit build.

| | MTPLX | `qwen3.8:27b-mlx` (Ollama) |
|---|---|---|
| pass rate | **16/16** | 15/15 |
| median wall | **226.4 s** | 272.2 s |
| tokens | **2,026** | 6,237 |
| turns | 10 | — |
| spread | 3.5× | 10.1× |
| resident | 26.8 GiB | 18 GB |

**Same weights, 17% faster, 68% fewer tokens.** The engine and quant together
are worth a real improvement — and the token collapse is the larger effect.
2,026 tokens is the **lowest median of any backend tested**, below synced ds4's
2,120.

The model card claims 2–3× speedup and 58.7 tok/s on an M5 Max. Measured
end-to-end on agent work the gain is **17%**, not 2–3×. Those are not
contradictory — the card measures decode throughput and this measures a whole
agent loop, where prefill and tool calls dominate — but the card's number is not
what a Claude Code user should expect.

Against the full field MTPLX places **fifth of eight rows**: behind ornith,
both ds4 builds and qwen3.6-coding, ahead of qwen3.6, qwen3.8-on-Ollama and
gemma4. It does not change the recommendation.

### Trials got faster as the run went on, and the cause is unknown

Round totals fell monotonically across the three rounds:

| round | total of 5 task medians |
|---|---|
| 1 | 1,780.5 s |
| 2 | 1,253.4 s |
| 3 | **1,021.3 s** |

Round 3 is **57% of round 1**. Per task:

| task | trial 1 → 2 → 3 |
|---|---|
| `parser-mbox-quoting` | 507.9 → 362.4 → 180.8 |
| `storage-blob-put` | 483.2 → 236.8 → 216.9 |
| `parser-date` | 267.5 → 235.8 → 193.0 |
| `mbox-scan` | 364.1 → 210.0 → **283.5** |
| `mbox-strip-envelope` | 162.9 → 208.4 → 147.1 |

Three of five fall monotonically; two do not. **Two candidate mechanisms were
proposed during the run and both were then disproved by the server's own
counters:**

- *SSD session cache warming.* MTPLX ships a session bank with an SSD cold tier
  that restores KV state by prefix. After 169 requests: `restore_hits: 0`,
  `restore_misses: 79`. **It never restored anything.** The cache cannot explain
  the trend because it never worked.
- *Background warm-up still running.* The startup ladder finishes during
  startup — all three steps `ok` before the first trial, 42.5 then 62.9 tok/s.
  It was already done.

So the trend is present in the data and **unexplained**. One untested candidate
remains: the turbo profile compiles specialised Metal verify kernels
(`MTPLX_COMPILED_VERIFY`, active at contexts ≤ 32,768), and MLX caches compiled
kernels — a compilation cost paid across early trials would produce this shape.
That is a hypothesis, not a finding.

**Consequence for the numbers above.** If the trend is real, the 226.4 s median
blends a cold regime and a warm one and describes neither. Round 3 alone gives
147.1 / 180.8 / 216.9 / 193.0 / 283.5 — a median near 193 s, which would place
MTPLX third. The median reported in the table is the honest whole-run figure;
the round-3 figure is not quoted as a result because the mechanism is unknown
and `mbox-scan` contradicts it.

Resolving this needs a dedicated run: restart the server and re-run round 1 on
a cold process. Until then, treat MTPLX's placement as provisional.

### Two things that are not like-for-like

**The runtime is pre-tuned by the model author.** `mtplx_runtime.json` ships
depth and draft settings, and the turbo profile sets ~40 environment knobs.
Every Ollama backend here ran at stock defaults with nobody tuning anything.
The asymmetry favours MTPLX and cannot be removed without deliberately
handicapping it.

**Reasoning is on by default** (`enable_thinking: true`). Thinking blocks are
returned as `type: "thinking"` content and counted in `output_tokens`. Whether
Ollama's inline `<think>` tags are counted the same way was not established, so
the 2,026-vs-6,237 token comparison may not measure the same quantity. The wall
times are unaffected.

The profile also documents that its 4-bit verify kernels are
"argmax- and sampler-distribution-validated, **not bit-exact** vs stock", while
prefill and non-speculative decode stay bit-identical. With 16/16 passes the
approximation cost nothing measurable at this difficulty.

*(16 rows rather than 15: a smoke test was run first to confirm tool calling,
and is retained.)*

## The client comparison, and why its first run proves less than it looks (2026-08-17)

**Do not quote the numbers in this section as a verdict on OpenCode.** They
were produced by a run that varied two things at once. They are kept because
deleting them would falsify the record, and because they do measure something
real — just not the thing the run was designed to measure.

### What was asked

Every result above this section was produced through **one client**, Claude
Code. Its system prompt, tool definitions and agent loop are a large share of
the tokens and turns in every row. So the rankings are properly read as
"backend *plus Claude Code*", and the obvious question is how much of the
difference belongs to the client.

The design was right: hold the backend fixed at `ds4` (synced, `fdcf3aa`) and
swap the client.

### What was actually run

| | claude+ds4 | opencode+ds4 |
|---|---|---|
| pass rate | **15/15** | 9/13 |
| median total (5 task medians) | 777 s | 1,243 s (**+60%**) |
| turns (median, per task) | 8–10 | 14–16 |
| output tokens (median, per task) | 1,633–3,957 | 3,291–6,034 |

Roughly double the turns and double the tokens, on every task, in the same
direction. By this report's own model — `wall ≈ tokens ÷ rate + per-turn
overhead` — that fully accounts for the +60%.

The run was aborted after 13 of 15 trials, once the confound below was
understood.

### The confound

**OpenCode reached ds4-server over `/v1/chat/completions`. Claude Code uses
`/v1/messages`.** Those are different code paths in ds4-server with different
tool-call serialization.

If tool calls parse less cleanly on the OpenAI path, the agent retries — which
appears as extra turns — and edits may silently fail to apply, which appears as
the failures. **One defect in a protocol adapter would produce both symptoms.**

So the measured gap belongs to the client, or to the protocol, or to both, and
this run cannot tell them apart. A protocol control (`ds4anthropic`, OpenCode
over `/v1/messages`) is the next run.

### What this run *can* say

It is a valid measurement of the configuration a user gets by following the
serving engine's own `connect` template, which emits an OpenAI-compatible
provider block. That is a real-world default and it performed materially worse
than Claude Code on the same weights. It is not evidence about OpenCode's agent
loop in isolation.

### The process failure

This was avoidable, and the evidence was in hand at the time.

Both endpoints were probed with `curl` before the run — **both returned 200** —
and the conclusion drawn was "ds4 speaks both protocols, so OpenCode can drive
it." That treated *it works* as the requirement. For a controlled comparison
the requirement is *it matches*. Finding two paths is precisely the moment to
notice a choice is being made.

The provider config was copied from `mtplx connect opencode --json`, which
emits an `@ai-sdk/openai-compatible` block. The template was adopted without
asking whether it matched the client being compared against.

[`AGENTS.md`](../../AGENTS.md) already required naming confounds in the backend
block at the moment it is added. Other caveats were written into that block;
this one was missed, hours after the rule was written.

**What would have caught it:** observing the wire call, not the status code.
One `--print-logs` run would have shown the endpoint immediately.

A logging proxy was then built to verify the endpoints properly and *that*
failed too — it buffered responses instead of streaming SSE, and hung until it
was killed. Verification needs a method suited to streaming.

## Claude Code vs OpenCode, controlled (2026-08-17)

**This is the run to cite.** Everything is held fixed except the client: same
weights, same server process, same wire protocol, same tasks, same excisions,
same oracle. 30 trials, 5 tasks x 3 rounds x 2 clients.

Two design choices make it trustworthy where the earlier attempt was not:

- **Interleaved.** The two clients run the same task back to back, so server
  state drift lands on both equally instead of on whichever ran second.
- **Same protocol.** Both reach ds4-server over `/v1/messages`. The first
  attempt had OpenCode on `/v1/chat/completions` and could not attribute its
  result; see the section above.

| task | claude | | opencode | | delta |
|---|---|---|---|---|---|
| `mbox-strip-envelope` | 135.8 s | 3/3 | 271.3 s | 1/3 | +100% |
| `parser-mbox-quoting` | 183.5 s | 3/3 | 242.9 s | 1/3 | +32% |
| `storage-blob-put` | 208.1 s | 3/3 | 158.2 s | 2/3 | **-24%** |
| `parser-date` | 347.7 s | 3/3 | 317.1 s | 2/3 | **-9%** |
| `mbox-scan` | 199.0 s | 2/3 | 245.7 s | 0/3 | +23% |
| **total of medians** | **1,074 s** | **14/15** | **1,235 s** | **6/15** | **+15%** |

| | claude | opencode |
|---|---|---|
| passed | **14/15** | **6/15** |
| median wall | 205.3 s | 242.9 s |
| spread | 3.0x | 3.4x |
| turns (median) | 10 | **17** |
| output tokens (median) | 3,947 | **5,546** |
| sandbox escapes | 0 | 0 |

### The speed gap is real but modest

**+15%** on the total of task medians. Per task it swings from -24% to +100%
and OpenCode wins two of five outright, so this is high variance around a small
penalty, not a uniform tax.

The round totals were remarkably stable -- claude 1,058.5 s then 1,047.1 s,
opencode 1,293.5 s then 1,274.5 s -- which is what interleaving bought. Cells
swung wildly and two flipped sign; the aggregate reproduced almost exactly.

**Earlier revisions of this report claimed +60%.** That figure came from the
confounded run and is wrong. The confound and the timing drift both pushed the
same way.

### The correctness gap is large, and it is a different kind of failure

**14/15 against 6/15.** OpenCode failed 9 of 15 trials, and every one of the
five tasks failed at least once, so this is not a task-specific weakness.

The signatures separate the two cleanly:

| client | failures | pytest result |
|---|---|---|
| claude | 1 | `1 failed, 15 passed` -- repaired 12 of 13 broken tests, missed one edge case |
| opencode | 9 | **exactly the control result, every time** |

Claude Code's single failure did the work and got one case wrong. All nine
OpenCode failures returned the test suite precisely as the excision left it:
`3 failed, 13 passed`, `34 failed, 21 passed`, `13 failed, 3 passed`. Nothing
changed at all.

That distinction matters more than the rate. A near-miss is a model capability
limit. An unchanged control result is a loop that terminated believing it had
finished. Only the second is a property of the client, and it is the same model
underneath in both columns.

The failures are not slow timeouts either -- one came back in 114.5 s against a
matched claude run of 272.3 s. Finishing early and reporting success is the
characteristic shape.

### What is still not known

**Why OpenCode stops — and it did not reproduce.** On 2026-08-18 the failing
cell (`mbox-strip-envelope`, 3/3 failures in the controlled run) was rebuilt by
hand and rerun **five times against a freshly restarted ds4-server. All five
passed**, writing a correct four-line implementation each time.

So the failure is conditional on something the manual reproduction did not
recreate. The most visible difference is server state: the matrix ran against a
ds4-server that had been up for hours with its KV cache full and evicting at
`hits=0` (441 evictions logged), while the reproduction ran six minutes after a
restart with a warm, uncontended cache.

**This weakens the claim that the failures are purely an OpenCode defect.** They
may be OpenCode-under-degraded-server, which is a different finding and a
different fix. The remaining candidates:

- the loop terminates early, and does so more readily when responses are slow;
- edits fail to apply under some condition not present in the reproduction;
- something about back-to-back interleaved trials that a standalone run lacks.

A reproduction needs to recreate the *conditions*, not just the command: run the
full interleaved matrix against a long-running server, rather than one trial
against a fresh one. The sandbox escape offered a tidy mechanism for the second
and is now closed -- there is no parent repo to write into -- so misapplied
edits would have to fail some other way. Reading a captured event stream from a
failing trial is the next step and has not been done.

**Whether this generalises past ds4.** One backend, one model. OpenCode drives
75+ providers and is presumably tuned against hosted frontier models, not a
local DeepSeek V4 Flash quant.

### Two asymmetries that remain

**Claude Code loads the operator's global `~/.claude/CLAUDE.md`** (~2 KB of
style and tooling rules) into every trial. OpenCode never sees it. The target
repo has neither `CLAUDE.md` nor `AGENTS.md`, so this is the only
instruction-level difference -- but it is a real one and it favours Claude Code.

**The protocol match is inferred, not observed.** `@ai-sdk/anthropic` can only
call `/v1/messages`; that is an SDK contract, not a measurement. Two attempts to
watch the traffic directly failed (a proxy that buffered instead of streaming
SSE, and a path-recorder that OpenCode retried past). The inference is sound but
it is an inference.

## Codex vs Claude Code, controlled (2026-08-17)

Same design as the OpenCode comparison: interleaved, one ds4-server process,
30 trials. Both clients reach the model over their **own native protocol** --
Claude Code on `/v1/messages`, Codex on `/v1/responses` -- because ds4-server
implements both. No translation layer sits in the measurement path.

| task | claude | | codex | | delta |
|---|---|---|---|---|---|
| `mbox-strip-envelope` | 145.1 s | 3/3 | 105.5 s | 3/3 | **-27%** |
| `parser-mbox-quoting` | 245.6 s | 3/3 | 171.0 s | 3/3 | **-30%** |
| `storage-blob-put` | 182.4 s | 3/3 | 238.1 s | 3/3 | +31% |
| `parser-date` | 329.5 s | 3/3 | 273.2 s | 3/3 | -17% |
| `mbox-scan` | 207.8 s | 3/3 | 190.7 s | 3/3 | -8% |
| **total of medians** | **1,110 s** | **15/15** | **978 s** | **15/15** | **-12%** |

| | claude | codex |
|---|---|---|
| passed | **15/15** | **15/15** |
| median wall | 214.1 s | **190.7 s** |
| spread | 4.7x | **2.9x** |
| output tokens (median) | 4,193 | 4,511 |
| sandbox escapes | 0 | 0 |

### Codex matched Claude Code on correctness and beat it on time

**15/15 for both.** Neither client failed a single trial. Codex was **12%
faster** on the total of medians, won four of five tasks, and had the
**tighter spread** -- 2.9x against 4.7x.

That last figure is the one worth noting. Across the whole day Claude Code was
the *predictable* client; here Codex was more predictable still.

### It did this without knowing what model it was talking to

Every Codex run emitted the same warning, 15 times in 15 trials:

```
Model metadata for `deepseek-v4-flash` not found. Defaulting to fallback
metadata; this can degrade performance and cause issues.
```

Codex has no entry for this model, so it is guessing at the context window and
capabilities. Claude Code had `deepseek-v4-flash` explicitly configured with a
100,000-token window. The handicap is real, it ran in Codex's disfavour, and
Codex won anyway.

### This reframes the OpenCode result

Three clients, one backend, one harness, the same tasks:

| client | passed | vs claude |
|---|---|---|
| Claude Code | 14/15, 15/15 | -- |
| **Codex** | **15/15** | **-12%** |
| OpenCode | 6/15 | +15% |

Two third-party clients, opposite outcomes. **"Not being Claude Code" does not
explain OpenCode's failures.** Codex is equally third-party, equally
unaffiliated with the model, and matched the reference client exactly on
correctness while beating it on speed and consistency.

Whatever goes wrong with OpenCode **on this backend** is specific to OpenCode.

**Do not read that as a general verdict on OpenCode.** It has only ever been
run against ds4. This report separately demonstrates that clients invert across
backends -- Codex was 12% faster than Claude Code on ds4 and 63% slower on
Ollama -- so the same inversion is available to OpenCode and untested. Combined
with the reproduction attempt above, which failed to trigger the failure on a
freshly restarted server, the honest scope is "OpenCode paired with this ds4
server, in these conditions". Issue #5 carries the coverage table and the run
that would settle it.

### `num_turns` is absent for Codex, on purpose

Codex emits one `turn.completed` per *exec*, not per model round trip, so
counting them gives 1 where Claude Code reports 10. They are not the same
quantity and a shared column would invite a false comparison. Codex's activity
is recorded as `tool_items` (`command_execution` + `file_change`), median 12
per trial. Do not compare that number to another client's turn count either.

### Caveats

**One backend, one model.** Everything here is ds4 / DeepSeek V4 Flash.

**Claude Code still loads the operator's global `~/.claude/CLAUDE.md`**
(~2 KB) that Codex never sees -- an asymmetry favouring Claude Code, which
lost anyway.

**Sandboxing differs.** Claude Code ran `--permission-mode bypassPermissions`,
Codex `--sandbox workspace-write`. Codex was the more constrained of the two.

## Does Codex's advantage generalise? No. (2026-08-17)

Codex beat Claude Code on ds4. This run asks whether that is a property of the
client or of the pairing, by repeating it on the second-place backend --
`qwen3.6:27b-coding-mxfp8` via Ollama. Same design: interleaved, 30 trials,
both clients over their own native protocol (Ollama implements Responses,
chat/completions and messages).

| task | claude | | codex | | delta |
|---|---|---|---|---|---|
| `mbox-strip-envelope` | 111.5 s | 3/3 | 115.5 s | 3/3 | +4% |
| `parser-mbox-quoting` | 216.9 s | 3/3 | 421.6 s | 3/3 | +94% |
| `storage-blob-put` | 322.7 s | 3/3 | 396.0 s | 2/3 | +23% |
| `parser-date` | 231.3 s | 3/3 | 433.1 s | 3/3 | +87% |
| `mbox-scan` | 158.5 s | 3/3 | 332.5 s | 3/3 | +110% |
| **total of medians** | **1,041 s** | **15/15** | **1,699 s** | **14/15** | **+63%** |

| | claude | codex |
|---|---|---|
| median wall | **216.9 s** | 397.9 s |
| spread | 6.4x | **10.3x** |
| output tokens (median) | **2,297** | 5,374 |

### The result inverts

| backend | claude | codex | winner |
|---|---|---|---|
| ds4 (ds4-server) | 1,110 s, 15/15, 4.7x | **978 s, 15/15, 2.9x** | **Codex, -12%** |
| qwen3.6-coding (Ollama) | **1,041 s, 15/15, 6.4x** | 1,699 s, 14/15, 10.3x | **Claude Code, -39%** |

Same two binaries, same day, same tasks, same harness. **Codex is not the
better harness generally.** It is better on ds4 and worse on Ollama, while
Claude Code performs consistently on both.

**The client-backend pairing is the unit that matters**, not the client.

### Why: Codex does more work per task here

Median output tokens: **5,374 for Codex against 2,297 for Claude Code** -- 2.3x.
Tool-call counts track it: the slow trials ran 39-41 `tool_items` where the fast
ones ran 8-11. This is the report's central finding again -- `wall ~ tokens /
rate + overhead` -- not a new mechanism.

Two intermediate readings were wrong and are recorded as such. The first Codex
trial (792.5 s) was called evidence the advantage "doesn't transfer"; it was
cold start, and the next trial on that cell ran 115.5 s. Later, a process
sampled in state `S` with Ollama at 0.0% CPU was called a stall; the token
counts show it was thrashing, and the snapshot fell between requests.

### A third failure signature

Codex's one failure returned `1 error in 0.10s` -- a pytest **collection**
error. It left `storage.py` unparseable.

| client | signature | reading |
|---|---|---|
| claude | `1 failed, 15 passed` | attempted, missed an edge case |
| opencode | exactly the control result | did not change anything |
| **codex** | **`1 error`** | **left the file broken** |

For unattended use the third is the worst: a broken import can take down code
that previously worked.

### Caveats

**A warm-up trend runs through the whole run.** Round totals fell for both
clients (claude 1,683 -> 1,021 s; codex 2,879 -> 1,537 s), so round 1
overstates the gap. Round 3 alone: claude 1,148 s, codex 2,075 s -- **+81%**,
so the gap does not close with warming.

**Codex ran without model metadata on both backends**, warning on every trial
that it is guessing at capabilities. Constant across both, so it cannot explain
an inversion, but it is a handicap it carried throughout.

## The hosted reference: what the local numbers are measured against (2026-08-18)

Every figure in this report is an absolute with no denominator. This run supplies
one: hosted **Claude Opus 5**, `--effort medium`, through the identical harness,
5 tasks x 1 trial.

**Read it for time only.** `gmail-archive` was itself written with Claude, so
asking Opus to restore a function it authored is closer to recall than to
problem-solving. Its 5/5 is evidence of authorship contamination, not of task
difficulty, and must not be cited against issue #4. See METHODOLOGY.md.

| task | opus5 | ds4 + Claude Code | ds4 + Codex | opus5 as % of ds4+cc |
|---|---|---|---|---|
| `mbox-strip-envelope` | 32.1 s | 137.7 s | 102.5 s | 23% |
| `parser-mbox-quoting` | 31.5 s | 229.8 s | 171.0 s | 14% |
| `storage-blob-put` | 35.6 s | 206.7 s | 238.1 s | 17% |
| `parser-date` | 59.6 s | 338.6 s | 273.2 s | 18% |
| `mbox-scan` | 44.6 s | 203.4 s | 190.7 s | 22% |
| **total** | **203.4 s** | **1,116.2 s** | **975.5 s** | **18%** |

**The hosted reference takes 18% of the time of the primary local pairing, and
21% of the time of the fastest local pairing.** Consistent across all five
tasks: the range is 14-23%, with no task where local comes close.

It is also more economical with tokens: median 1,629 output tokens over 7 turns,
against 2,120 and ~10 for synced ds4 — the lowest of anything measured here.

### What this means for the fallback

Nothing changes about the recommendation. The local stack was never a speed
play; it exists so that work is still possible when the hosted option is not.
But the size of the gap is worth stating plainly rather than leaving implied:

- A task that takes **3.5 minutes** hosted takes **18.6 minutes** on the best
  local pairing measured here.
- That is the price of the hedge, on this hardware, at this task difficulty.

### Caveats

**Single trial per task.** Enough for an order-of-magnitude denominator, not for
ranking. The local figures it is compared against are medians of three.

**Network and service variance are not controlled.** The local runs are
machine-local; this one crosses the internet to a shared service.

**Subscription, not API.** Run through the operator's normal Claude Code login —
the harness leaves ambient auth untouched for backends with no `base_url`, so no
API credit is involved. The cost is subscription usage.

---

## How many trials does a claim need? (2026-08-28, issue #23)

Computed by [`sizing.py`](sizing.py) over all 398 usable trials. Re-runnable.

### Pass rate

An unbroken run's Wilson lower bound is exactly `n / (n + z²)`, so the trial
count needed is a closed form and the cost is sharply non-linear:

| to be 95% confident the true rate exceeds | consecutive passes needed |
|---|---|
| 80% | 16 |
| **90%** | **35** |
| 95% | 73 |
| 99% | 381 |

**One failure costs about twenty trials.** `ds4 x claude` at 46/46 has a lower
bound of 0.923; at 46/47 it would fall below 0.90. That asymmetry is why a
single timeout is worth chasing down rather than averaging away.

Where the measured combinations actually stand:

| combination | result | lower bound | |
|---|---|---|---|
| `ds4 x claude` | 46/46 | **0.923** | clears 90% |
| `ds4anthropic x codex` | 36/36 | **0.904** | clears 90% |
| `qwen36coding x claude` | 30/30 | 0.886 | 5 more, if unbroken |
| `ornith15 x codex` | 40/42 | 0.842 | cannot reach 90% without a long clean run |
| `mtplx`, `gemma4`, `glm53`, `qwen38fnq3`, … | 15/15–16/16 | 0.796–0.806 | ~20 more each |

The 15/15 backends are not "as good as ds4 pending more data". They are
**unmeasured above 80%**, and closing that gap costs 20 trials each.

### Wall time — the number that changes how this project reports speed

Bootstrapped from the pooled, per-cell-normalised distribution of 198 wall times
from every cell with at least 6 trials:

| trials | one task's median | 5-task suite total |
|---|---|---|
| **3** | **± 27.9%** | **± 12.9%** |
| 5 | ± 21.6% | ± 9.2% |
| 10 | ± 13.5% | ± 5.4% |
| 20 | ± 7.3% | ± 3.4% |
| 35 | ± 4.9% | ± 2.2% |

**A 3-trial task median carries ±28%.** Two such medians cannot be distinguished
unless they differ by more than about 56%. This project has published per-task
comparisons far narrower than that.

Suite totals are much better behaved, because a total sums five independent
medians and the errors partly cancel: **±12.9% at n=3**, so two suites separate
only above roughly a 26% gap.

Applying that to what is already published:

| claim | gap | verdict at n=3 |
|---|---|---|
| Claude Code vs Codex on ds4 (982 s vs 975 s) | 0.7% | **far below resolution** — correctly called indistinguishable |
| Q3 vs Q2 on Qwen3.8-Flash-Next (#31) | 28.4% | **marginal** — clears ~26% by a hair, and it held on all five tasks individually, which is the stronger evidence |
| `ornith15` vs `ds4` suite (597 s vs 975 s) | 39% | clears |
| Codex vs Claude Code on llama.cpp (4.2x) | 320% | clears easily |

The Q3-vs-Q2 result survives, but not because the 28.4% headline is comfortable
— it is barely outside the noise. What carries it is that Q3 won **all five
tasks separately**; five independent coin-flips landing the same way is worth
more than the aggregate.

### The rule this produces

**Three trials is a screening run, not a measurement.** It answers "does this
backend work at all" and "is this difference enormous". For anything else:

- **Ranking two backends on speed:** 10 trials minimum (±5.4% on a suite), 20 to
  separate backends within 10% of each other.
- **Claiming >90% reliability:** 35 consecutive passes. There is no shortcut.
- **A difference under 26% at n=3 is not a finding.** Say "no difference
  measured", never "X is faster than Y".
