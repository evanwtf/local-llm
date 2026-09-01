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

---

## The harder tasks, measured (2026-08-28, issue #4)

18 trials: three new tasks × `ds4` under Claude Code and Codex × 3 trials.
**18/18 passed.** Every measurement below is secondary to that verdict and none
of it feeds into one.

| task | client | pass | median | ruff Δ | mypy Δ | verbatim | distinct |
|---|---|---|---|---|---|---|---|
| `parser-mbox-quoting-nodoc` | claude | 3/3 | 283.7 s | +0.0 | +0.0 | 0/3 | 3/3 |
| `parser-mbox-quoting-nodoc` | codex | 3/3 | 261.4 s | +0.0 | +0.7 | 0/3 | 3/3 |
| `mbox-quoting-both-halves` | claude | 3/3 | 243.0 s | +0.0 | **+1.3** | 0/3 | 3/3 |
| `mbox-quoting-both-halves` | codex | 3/3 | 208.7 s | +0.0 | **+2.0** | 0/3 | 3/3 |
| `storage-put-and-sweep` | claude | 3/3 | 286.7 s | **−0.7** | +0.0 | 0/3 | 3/3 |
| `storage-put-and-sweep` | codex | 3/3 | 231.0 s | **−0.3** | +0.0 | 0/3 | 3/3 |

Suite totals: **Claude Code 813.4 s, Codex 701.1 s** — a 16% gap, which at n=3
is inside the ±12.9% band from #23. **No difference measured.**

### The tasks are harder in time, not in correctness

Per-task median rose from **194.6 s to 270.6 s, +39%**, with no failures. Three
axes of difficulty were added — a withheld contract, cross-module coordination,
and a correctness property invisible to the oracle — and `ds4` cleared all three
under both clients.

**The ceiling is not an artifact of easy tasks.** That is the finding, and it is
not the one this issue expected.

### Nothing was recalled

**`restored_verbatim` is 0/18, and all 18 solutions are distinct.** The
authorship-contamination worry that METHODOLOGY has carried since day one does
not apply to these results.

The strongest single piece of evidence: with `unquote_mbox`'s docstring removed,
the model **re-derived the mboxrd reasoning from scratch** — that mboxrd adds
exactly one `>`, that stripping one is therefore the correct inverse, and that
`>>From ` is ambiguous against mboxo and must be flagged. That docstring was
chosen as the target *because* it gives the answer away. Two trials wrote it back
in different words and different helper names, with the same logic.

### One real defect, reproducible, that the oracle cannot see

`mbox-quoting-both-halves` adds **exactly 2 mypy errors in 5 of 6 trials**, both
clients:

```python
def _strip(match: re.Match) -> bytes:        # original: re.Match[bytes]
    return quotes[1:] + match.group(2)
```

```
src/gmail_archive/parser.py:124: error: Missing type arguments for generic type "Match"  [type-arg]
src/gmail_archive/parser.py:129: error: Returning Any from function declared to return "bytes"  [no-any-return]
```

All 71 tests pass. The bare `re.Match` silently disables checking at the
**bytes/str boundary**, which in a mail parser is exactly where the expensive
bugs live — and this is a repository whose own config is `mypy --strict`.

**This is the first "passes but is worse" result this project has recorded**, and
it exists only because solutions are now saved and gated. It is small. It is also
precisely the class of defect the issue predicted and the oracle was never going
to catch.

The sixth trial was clean by accident, not by discipline: it used a lambda and
needed no annotation at all.

### What this means for the issue

Difficulty along these three axes buys **time, not discrimination**. To separate
models on quality the suite needs defects that are *common*, not a 2-error
signal in one of three tasks — larger surfaces, more places to be sloppy, and a
gate stricter than "does the repo's own linter complain".

The tasks are worth keeping. They cost 39% more wall clock, produce a real
quality signal where the old five produce none, and they are honest about what
they cannot do.

---

## The PLE offload does not pay (2026-08-28, issues #33 / #34)

**New series.** llama.cpp mainline `d7bd3bfca`, Codex 0.150.1, both backends
reached through the shim. Do not pool with anything above this line: the engine,
the client and the request path all moved. That is why `qwen38fnq3` was re-run
rather than compared against its earlier 895.8 s.

| task | `qwen38fnq3` 3-bit | `qwen38fnq4m64` 4-bit | delta |
|---|---|---|---|
| `mbox-strip-envelope` | 111.0 s | 103.1 s | −7% |
| `parser-mbox-quoting` | 155.3 s | 196.2 s | +26% |
| `storage-blob-put` | 140.5 s | 189.3 s | +35% |
| `mbox-scan` | 150.5 s | 195.2 s | +30% |
| `parser-date` | 437.8 s | 592.3 s | +35% |
| **suite** | **995.1 s** | **1,276.0 s** | **+28%** |

Correctness: **15/15** and **16/16**. Neither clears 90% at 95% confidence
(#23); both need ~20 more consecutive passes.

**+28% clears the ~26% resolution floor for a 3-trial suite, but only just.**
What carries it is that four of five tasks move the same way, at +26% to +35%.
The fifth is −7%, inside the noise. That is the same standard applied to the
Q3-vs-Q2 result, and it lands the same way: real, and not comfortable.

### Why the memory saving never appeared

The premise of #33 is that the 51B n-gram PLE table is a lookup structure doing
no arithmetic, so paging it from SSD is a lossless ~29 GiB win. AtomicChat's
`-M64` build isolates it into a single 35.76 GiB shard for exactly that.

**It changes no tensors.** Against Unsloth's build, from the GGUF headers:

```
split.tensors.count      1224   ==   1224
qwen4exp.ple.*           identical
split.count                 3   vs     33
```

It is purely a re-sharding, so whether it saves anything is a question about
llama.cpp's mmap behaviour, not about the model.

**And mmap already does it.** `vmmap` on the running server:

| config | RSS | physical footprint |
|---|---|---|
| default `-ngl 999` | 92.2 GiB | **4.8 GB** |
| PLE pinned to CPU | 91.9 GiB | **5.0 GB** |

RSS counts clean file-backed pages the kernel can drop at any time. The number
that describes memory the process actually owns is the footprint, and it is ~5 GB
either way. **All 88 GiB of weights are already evictable, in both configs, with
no flag and no special build.** `--override-tensor per_layer_token_embd\.weight=CPU`
moved the tensor and changed nothing measurable — on unified memory a CPU buffer
and a Metal buffer are the same physical RAM.

### The cost side, which the source claim omits

The GGUF metadata answers the question #33 could not: `qwen4exp.ple.ngram_size
= 3`, **`heads_per_ngram = 8`**, with 16 head offsets defined. Not the one or
two lookups per token that would make this free.

Against the measured disk baseline (`benchmarks/disk/RESULTS.md`, random 4 KiB =
**61 µs**), a ~100-byte lookup cannot cost less than one block. As throughput
that is ~9,600 random reads/s at 600 tok/s prefill against 25,559 IOPS —
feasible, not free, and landing on prefill, which #14 established dominates
agent wall time here.

**"Lossless" is a claim about output, not about time.** It is almost certainly
true of the logits and says nothing about the clock.

### What to do

**Keep `UD-Q3_K_XL`.** The better quant costs 28% of the wall clock and buys no
measured correctness. The 4-bit build works, fits, and is worth keeping as
evidence that a 125B model at 4-bit runs on this machine at all — but it is not
the backend to run.

**#34 step 3 is still open and now better motivated.** Expert streaming is a
different proposition: an expert block is hundreds of KiB to a few MiB, which
lands in the 1 MiB row at 6.32 GiB/s rather than the 4 KiB row at 0.10 GiB/s.
Block size is what costs on this device, and ds4's `--ssd-streaming-*` is the
implementation that reads at the right size.

---

## llama.cpp vs Ollama on identical weights (2026-08-28, issue #28)

**The first fixed-model engine comparison in this project**, and it needed no
download. Ollama stores `ornith-1.5:35b` as a plain Q4_K_M GGUF in its blob
store, and llama.cpp reads that file directly:

```
~/.ollama/models/blobs/sha256-aaeb640f98a892980ef54876024293cc8d6987a86523aa1b947ffa9274ef800a
general.architecture = qwen35moe   general.name = Ornith-1.5-35B   20.22 GiB
```

Byte-identical weights, same quant, same client (Codex 0.150.1), same tasks.

| task | Ollama | llama.cpp | delta |
|---|---|---|---|
| `mbox-scan` | 155.0 s | 127.4 s | **−18%** |
| `parser-mbox-quoting` | 118.7 s | 133.6 s | +13% |
| `mbox-strip-envelope` | 42.8 s | 54.4 s | +27% |
| `storage-blob-put` | 94.8 s | 133.6 s | +41% |
| `parser-date` | 111.8 s | 421.8 s | **+277%** |
| **suite** | **523.1 s** | **870.8 s** | **+66%** |
| suite **minus `parser-date`** | 411.4 s | 449.0 s | **+9%** |

Correctness: **14/16** and **14/15**. No separation.

### The headline is an artifact. Do not quote the 66%.

**Throughput is identical.** Seconds per 1,000 output tokens, by task and engine:

| task | Ollama | llama.cpp |
|---|---|---|
| `parser-date` | 15.0 | 14.1 |
| `storage-blob-put` | 14.2 | 14.1 |
| `mbox-strip-envelope` | 16.8 | 18.5 |

llama.cpp is not slower per token. On `parser-date` it emitted **29,906 output
tokens against Ollama's 7,449** for the same task. The wall clock followed the
token count, as #26 already established it does (r = 0.98).

**The cause is configuration, not engine.** `llamacpp-up` hardcoded Qwen's
recommended sampler — `--temp 1.0 --top-p 0.95 --top-k 20` — while Ollama's
modelfile for this model sets no parameters at all and uses Ollama's own
defaults. The two halves were never sampled the same way.

### The control

llama.cpp re-run with Ollama's sampling (`temp 0.8, top-p 0.9, top-k 40`):

| config | task | median | output tokens | pass |
|---|---|---|---|---|
| llama.cpp t=1.0 | `parser-date` | 421.8 s | 29,906 | 3/3 |
| **llama.cpp t=0.8** | `parser-date` | **212.1 s** | **14,167** | 3/3 |
| Ollama (defaults) | `parser-date` | 111.8 s | 7,449 | 2/3 |
| llama.cpp t=1.0 | `storage-blob-put` | 133.6 s | 9,460 | **3/3** |
| **llama.cpp t=0.8** | `storage-blob-put` | 139.9 s | 8,310 | **0/3** |
| Ollama (defaults) | `storage-blob-put` | 94.8 s | 6,668 | 2/3 |

**Lowering the temperature halved both the tokens and the clock** on
`parser-date` — 29,906 → 14,167 and 421.8 s → 212.1 s. Sampling is confirmed as
the driver.

**It closes half the gap, not all of it.** At matched sampling llama.cpp still
emits ~1.9x Ollama's tokens and takes ~1.9x as long. The remainder is not
explained here. The most likely candidate is template handling of this model's
*thinking* mode — Ollama declares `thinking` as a capability for this model and
llama.cpp is driven with `--jinja` against the GGUF's own template — but that is
a hypothesis, not a measurement.

### An unexpected correctness result

`storage-blob-put` went **3/3 at temp 1.0 and 0/3 at temp 0.8**, three identical
near misses (`1 failed, 16 passed`). Lowering the temperature made the model
reproducibly *wrong* on that task. n = 3 each side, so this is a flag rather
than a finding — but it is the first evidence here that sampler settings move
pass rate and not only wall time.

### What this means for every engine comparison in this file

**"Which engine is faster" is the wrong question.** On identical weights the
engines decode at the same rate. What differs is how many tokens each *default
configuration* induces the model to emit, and that is portable — it is a flag,
not a property of the engine.

Read every earlier cross-engine claim here with that in mind. The MTPLX-vs-Ollama
result ("17% faster on 68% fewer tokens") is the same shape and was attributed to
the serving stack; on this evidence the token count is the finding and the stack
may not be. It has not been re-checked.

**A fair engine comparison must pin the sampler on both sides.** `llamacpp-up`
now takes `TEMP`, `TOP_P`, `TOP_K` and `MIN_P` from the environment so that is
possible; before today it was not.

### Resolved 2026-08-29: there is no engine difference

`repeat_penalty` was the missing parameter — Ollama defaults to **1.1**,
llama.cpp to **1.0**, and `llamacpp-up` never set it.

| config | `parser-date` | `storage-blob-put` |
|---|---|---|
| llama.cpp original | 421.8 s / 29,906 tok | 133.6 s / 9,460 tok |
| llama.cpp, three params matched | 212.1 s / 14,167 tok | 139.9 s / 8,310 tok |
| **llama.cpp + `repeat_penalty 1.1`** | **147.1 s / 10,363 tok** | **118.6 s / 8,772 tok** |
| **Ollama (its own defaults)** | **133.9 s / 7,694 tok** | **112.5 s / 7,526 tok** |

**+10% and +5% — inside the noise.** On byte-identical weights the two engines
decode at the same rate and, once sampled alike, take the same time. **The
entire +66% was four sampler defaults nobody chose**, in two launchers that each
inherited a different set.

**This puts the MTPLX result under suspicion.** "17% faster on 68% fewer tokens"
than Ollama on the same weights, attributed above to the serving stack, is
exactly this shape — a token-count difference read as an engine property, and it
was measured before any sampler was pinned. **Not re-checked. Treat it as
provisional.**

The transferable rule: **a fewer-tokens-therefore-faster result is a sampler
hypothesis until the samplers are shown to match.** Both instances of it in this
file turned out that way.

---

## What SSD offload costs: the two techniques measured (2026-08-28, issue #34)

The question #34 asked for: **memory saved against suite wall time, per
technique, on this machine.** Both techniques now have an answer, and they are
opposite.

| technique | memory | suite wall time | correctness |
|---|---|---|---|
| **MoE expert streaming** (ds4 `--ssd-streaming`) | **91.0 → 36.7 GiB (−60%)** | 992.8 → 1748.6 s (**+76%**) | 16/16, no cost |
| n-gram PLE offload (AtomicChat `-M64`) | **no saving** | 995.1 → 1276.0 s (+28%) | 16/16, no cost |

**Expert streaming is a real option. The PLE offload is not.** Both were filed
here as "SSD offload"; the disk baseline said they were different propositions
and they are.

### Expert streaming, measured

`ds4anthropic x codex`, five tasks, three trials each, identical stack.

| task | resident | streaming | cost |
|---|---|---|---|
| `mbox-scan` | 317.7 s | 330.8 s | **+4%** |
| `parser-date` | 246.9 s | 456.6 s | +85% |
| `mbox-strip-envelope` | 94.7 s | 184.8 s | +95% |
| `storage-blob-put` | 200.2 s | 422.6 s | +111% |
| `parser-mbox-quoting` | 133.3 s | 353.8 s | +165% |
| **suite** | **992.8 s** | **1748.6 s** | **+76%** |

Correctness **15/15 resident and 16/16 streaming.** Across 31 trials the
technique never changed an answer — it is lossless in output, as claimed.

Startup differs sharply too: **2 s to serving streamed, 16-30 s resident**,
because streaming loads nothing upfront. Irrelevant for a server left running;
not irrelevant for a cold-start fallback.

**Memory is bounded, not merely lower.** RSS settled at 36.7 GiB after the first
request and read 37.1 GiB after ten agent trials. A cache that crept upward
would erase the benefit over a long session; this one does not.

**The per-task cost is not uniform** — +4% to +165%. `mbox-scan` is nearly free
while `parser-mbox-quoting` costs 2.5x. The plausible mechanism is how much
*distinct* expert routing a task provokes rather than how long it runs, since the
cache serves repeats cheaply. That is a hypothesis; these rows cannot prove it.

### The trap that makes streaming look broken

`ds4-up` hardcoded `--warm-weights`, which touches every page at startup and
directly contradicts `--ssd-streaming`. With both set the server reported
**90.9 GiB — full residency, streaming apparently doing nothing**, and nothing
warns you. `WARM` is now overridable; a streaming run needs `WARM=''`.

Both launchers now also take `EXTRA_FLAGS`, so a per-experiment flag does not
require forking the script.

### Against the upstream claim

[@EyalToledano reported](https://x.com/EyalToledano/status/2093429897188299113)
Qwen3.8-Flash-Next with 60% of experts on disk at **37 GB and 40 tok/s** on an
M4 Max. Independently, ds4 with expert streaming lands at **36.7 GiB** on a
different model with the same technique — a striking convergence.

**What that post cannot say, and this can:** 40 tok/s is a decode figure, and
#14 established that re-prefill dominates agent wall time here. The measured
agent cost is **+76%**, which is the number that decides whether to use it.

### When to use it

**Only when memory is the binding constraint.** On this machine ds4 fits
resident with 21 GiB to spare, so streaming buys nothing and costs 76%.

It changes the answer where a model does not fit at all. GLM-5.2 was rejected on
size (#17, smallest quant 211 GB); Kimi K3 and MiniMax M3 on "nothing under
108 GiB". All three are MoE, all three read at the block size this device is good
at, and a 60% resident reduction moves them from impossible to slow. **That is
the finding: expert streaming does not make a fitting model faster, it makes a
non-fitting model possible.**

---

## GLM-5.2: possible, but not practical (2026-08-29, issue #35)

**A model this project ruled out on size, run.** #34 measured ds4 expert
streaming at −60% resident for +76% wall time, which reopened the "too big for
this machine" tier. GLM-5.2 was the only one of the three rejected models with a
path today — ds4 (DwarfStar) serves `glm-dsa` natively with dedicated GLM
kernels, so it needed no shim and no new build.

| | |
|---|---|
| file | `GLM-5.2-UD-IQ2_XXS_RoutedIQ2XXS_blk78Q2K.gguf`, **196.6 GiB** |
| Metal ceiling | 112.00 GiB — **impossible resident** |
| **resident, streamed** | **30.8 GiB (−84%)** |
| startup | 4 s |
| coherence at temp 0 | **passes** |
| warm decode | **~3.4–4.1 tok/s** |
| **agent task** (`mbox-strip-envelope`) | **PASS in 2,585.5 s** |

**It works.** It loads, it is coherent, and it completed a real multi-turn agent
task correctly. The premise of the "too big" tier — that the Metal ceiling is a
hard wall — is wrong.

**It is also not usable.** 2,585.5 s on the *easiest* task in the suite, against
184.8 s for ds4 under the same streaming mode: **14x as long**. Decode is ~4 tok/s
against ds4's 40.6. At that rate the tasks that need 2,000–9,000 output tokens
cost 8–37 minutes of pure decode each, so a five-task suite is multi-hour per
trial and cannot be measured at the three-trial standard this project uses.

**The cache cannot be enlarged to fix it.** `--ssd-streaming-cache-experts 80GB`
fails outright with `failed to create metal session`; ds4's own guard fires and
suggests a smaller `--ctx` or SSD streaming. That is the "GLM Metal caps lower"
note in `ds4_help.c` made concrete — 40GB is near the usable limit here, so the
81 GiB of unused headroom cannot be spent on speed.

### What this changes about the tier

**Streaming converts "impossible" into "possible but impractical", not into
"available".** That is a real distinction and it should be applied per model:

- The tier is no longer blocked on **memory**. It is blocked on **speed**.
- **Kimi K3 and MiniMax M3 remain out for a different reason entirely** — no
  engine here can serve them at all. That is engine support, not capacity, and
  streaming does not touch it.
- A model only becomes a candidate if its streamed decode rate is within a few
  times of the resident primary. GLM-5.2, at 10x as long, is not.

### A near-miss worth recording

The first load reported success while serving **DeepSeek V4 Flash**, because
`ds4-up` assigned `MODEL=` with a plain assignment and silently ignored the
exported override. It answered coherently, because it was a working model —
just not the one under test.

The only tell was a **byte-identical answer to the previous coherence check and
identical token usage (32/127/159)**. Without that coincidence this section would
have reported "GLM-5.2 runs in 36.7 GiB" about the wrong model entirely.

Both launchers now take `MODEL` and `EXTRA_FLAGS`. **Check the running process's
own command line rather than trusting a launcher's output**, and treat an
identical answer from a supposedly different model as a defect, not a
coincidence.

---

## A sampler parameter halves the pass rate (2026-08-29, issue #36)

30 trials, `ornith-1.5:35b` on llama.cpp, `storage-blob-put`, Codex. Everything
held fixed except the named parameter.

| cell | temp | top_p | top_k | pass |
|---|---|---|---|---|
| original | 1.0 | **0.95** | 20 | **6/6** |
| temperature alone | **0.8** | 0.95 | 20 | **6/6** |
| top_k alone | 1.0 | 0.95 | **40** | **5/6** |
| all three | 0.8 | **0.90** | 40 | 3/6 |
| **top_p alone** | 1.0 | **0.90** | 20 | **4/6** |

Pooled with the trials that raised it:

| | pass | rate | Wilson 95% lower |
|---|---|---|---|
| `top_p 0.95` | **20/21** | 95% | **0.79** |
| `top_p 0.90` | **7/15** | 47% | 0.25 |

**Non-overlapping intervals. One sampler parameter roughly halves the pass rate
on this task.** Temperature and top_k were each isolated and are innocent —
6/6 and 5/6.

**A narrower nucleus performs worse**, which is the opposite of the usual
intuition for code generation. With `top_k 20` already limiting candidates,
dropping `top_p` to 0.90 appears to cut tokens needed for a correct
continuation: the failures are overwhelmingly the near miss `1 failed, 16
passed` — working code with one behaviour wrong — not a collapse.

### Every pass rate in this file was measured at an unchosen top_p

- `llamacpp-up` hardcoded **0.95** for every model it served, Qwen-lineage or not.
- Ollama uses each model's modelfile. `ornith-1.5:35b` sets no parameters, so
  **Ollama's default 0.9** applied.
- `ds4-server` has its own defaults, and does not report them.

So `ornith15` (Ollama) and `ornith15llamacpp` were **never sampled alike**, and
most cross-engine comparisons above share that confound. #28 found the same
defect in wall time; this is it in **pass rate**.

**Treat cross-engine pass-rate comparisons in this file as provisional until
both sides are sampler-matched.**

### Scope

One task, one model, one client. `storage-blob-put` may be unusually sensitive,
and Ornith 1.5 is `qwen35moe` — Qwen's own recommendation is `top_p 0.95, top_k
20`, so the model may simply be tuned for it. **This does not establish that
`top_p 0.9` is worse in general.** It establishes that it is worse here,
decisively, and that a sampler default nobody chose was silently deciding
results.

### Resolved: it is an interaction with `repeat_penalty`

A sixth cell ran `top_p 0.9` **with** `repeat_penalty 1.1` — Ollama's paired
default — on llama.cpp, instead of inferring it across engines.

| configuration | pass | rate | Wilson 95% lower |
|---|---|---|---|
| `top_p 0.95`, no repetition penalty | **17/18** | 94% | 0.74 |
| `top_p 0.90`, **no** repetition penalty | **7/12** | 58% | 0.32 |
| **`top_p 0.90` + `repeat_penalty 1.1`** | **6/6** | **100%** | 0.61 |

**`top_p 0.90` is only harmful without a repetition penalty.** That explains why
`ornith15` on Ollama — which runs `top_p 0.9` *and* `repeat_penalty 1.1`, because
those are Ollama's paired defaults — scores 14/16 tonight and 40/42 lifetime
rather than the ~58% llama.cpp showed at 0.9 alone.

The failure shape fits: every failure in the affected cells is the near miss
`1 failed, 16 passed`, consistent with the model settling into a slightly-wrong
repeated formulation that a repetition penalty pushes it out of.

### The sampler each Ollama backend has actually been running

Recorded for the first time by `probe_ollama` (`/api/show`):

| backend | temp | top_p | top_k | rep pen |
|---|---|---|---|---|
| `qwen`, `qwen36`, `qwen38flashnext` | 1 | 0.95 | 20 | 1 |
| `qwen36coding` | **0.6** | 0.95 | 20 | 1 |
| `ornith` | **0.6** | 0.95 | 20 | unset |
| `gemma4` | 1 | 0.95 | **64** | unset |
| **`ornith15`** | — | — | — | **engine defaults (unrecorded)** |

**Every Ollama model that declares a sampler uses `top_p 0.95`.** `ornith15` is
the only backend in the project that falls back to Ollama's `0.9` — and it is
the model this was measured on.

**`ds4-server` reports nothing at all**, and it is the primary backend. That is
the largest remaining blind spot; issue #37.

### How it was nearly missed three times

The trials that raised this moved **three parameters at once**, and the first
reading blamed temperature. A four-cell sweep still could not attribute it,
because cells 1–3 all happened to hold `top_p 0.95` — the only cell that varied
it also varied everything else. A fifth cell, top_p alone, closed it.

**All three errors were the same error: varying more than one thing.** First
three parameters at once; then a four-cell sweep whose control cells all shared
one `top_p`; then a conclusion stated without noticing that `repeat_penalty`
differed too. A control which changes a "set" of related settings is not a
control — it only tells you the set matters.

---

## GLM-5.3-Flash on the supported stack (2026-08-29, issues #38 / #41)

**Everything previously recorded about GLM-5.3-Flash was measured on a stack
antirez does not support.** `backend.glm53` is Unsloth's `UD-Q2_K_XL` on
llama.cpp PR #27752 through the shim. He ships GLM for Mac through **ds4
(DwarfStar)** with his own GGUF layout, and ds4 is explicitly not a general GGUF
loader. The artifacts are not interchangeable:

```
antirez GGUF   general.architecture = glm5-next
Unsloth GGUF   general.architecture = glm5next
```

One hyphen. Neither engine reads the other's file — which is also the root cause
of #25, where a model "loaded and emitted gibberish".

### The supported stack is dramatically better, and still not usable

Same prompt, temperature 0:

| | llama.cpp + Unsloth | **ds4 + antirez** |
|---|---|---|
| response | ~76 s | **3.2 s** |
| completion tokens | 854 (mostly reasoning) | **47** |
| decode rate | 12.11 t/s | **27.8 t/s** |
| load time | slow | **10–14 s** |
| `mbox-strip-envelope` × Claude Code | **timeout at 3,600 s** | **PASS in 166.9 s** |

**18x fewer tokens, 2.3x the decode rate, and the timeout gone.** The reasoning
explosion is a property of the llama.cpp+Unsloth path, not of the model.

**But 166.9 s did not reproduce**, and chasing it found the real constraint:

```
prefill          361 t/s
context          39,903 tokens
per turn         ~110 s of re-prefill
KV cache         529 entries stored, hits = 0
decode           27.8 t/s  (2,544 tokens = 91 s -- never the problem)
```

**Every turn re-prefills the entire 40k context because the cache never hits.**
~110 s x 12+ turns is the whole 3,362 s wall clock. This is #14 in its purest
form, on ds4 rather than llama.cpp.

The chain is: GLM-5.3 is verbose → 40k contexts → 110 s re-prefill per turn →
~56 minutes per task. **DeepSeek on the same engine and client runs a five-task
suite in 860 s**, because its contexts are a fraction of the size.

### The KV budget is sized for DeepSeek, and #26's hypothesis is right here

```
DeepSeek KV entries   ~560 MiB
GLM-5.3 KV entries    6,012 - 8,061 MiB      (~12x)
ds4-up's budget       8,192 MiB              -> one entry, evicts every turn
```

436 evictions in a single trial. `KV_DISK_MB` is now overridable and was raised
to 64 GB — which did **not** help, because the cache achieves `hits=0` at any
size.

**Note the symmetry with #26.** That issue claimed "wall time swings 3x — KV disk
cache is at its cap" and was refuted for DeepSeek, where sampling was the real
cause. For GLM the original hypothesis is correct. Same engine, same flag,
opposite answer — the KV footprint differs 12x by architecture.

### Codex is blocked separately (#41)

GLM-5.3 on ds4 emits `"false"` as a JSON **string** where Codex's tool schema
declares a **boolean**: `failed to parse function arguments: invalid type:
string "false", expected a boolean`. **78 parse errors in one trial**, 53
minutes, never recovered. Claude Code tolerates the same output.

**So GLM-5.3-Flash is currently unusable with either client on the supported
stack** — Claude Code by re-prefill, Codex by tool-call parsing.

### A configuration trap worth not repeating

`WARM=''` without `--ssd-streaming` leaves weights **neither resident nor
streamed**. RSS sat at 3.1 GiB for an 89.9 GiB model and every forward pass
faulted from disk; a trial that should have decoded in 91 s took 2,470 s.
`WARM=''` is only correct *together with* streaming.

---

## The Swift repository: what a second target actually bought (2026-08-29, #44)

**45 trials, 44 correct.** Five backend × client pairs × three tasks × three
trials on `~/git/monitor` — 11,265 Swift lines, 215 tests, `swift test` in
0.705 s. New series: different repository, language and oracle. **Do not pool
with gmail-archive.**

| pair | pass | suite | median out_tok | s per 1k tok |
|---|---|---|---|---|
| **`ds4` × claude** | **9/9** | **522 s** | **3,835** | 47.6 |
| `ornith15` × codex | 8/9 | 844 s | 20,788 | **14.7** |
| `qwen38fnq3` × codex | 9/9 | 1,086 s | 5,932 | 61.5 |
| `ds4anthropic` × codex | 9/9 | 1,115 s | 9,082 | 39.6 |
| `qwen36coding` × claude | 9/9 | 1,393 s | 5,232 | 84.3 |

### It did not make the tasks harder to pass

**44 of 45.** Correctness is as saturated here as on gmail-archive, so the
hypothesis that a larger, less-familiar codebase would produce failures is
**not supported**. Whatever these models lack, it is not the ability to restore
a Swift function against a green test suite.

### It did separate the pairs, on speed and on why

The suite spread is **2.7x**, and it decomposes into two independent mechanisms
that a wall-clock total conflates:

- **`ornith15` spends 3.2x fewer seconds per 1k tokens than `ds4`** (14.7 vs 47.6 s/1k) and
  still finishes second, because it emits **5.4x the tokens**.
- **`qwen36coding` writes little** (5,232) and finishes last, because it is the
  slowest per token (84.3 s/1k).

Two pairs land within 25% of each other on the clock for opposite reasons.
**`ds4 × claude` is the only pair that wins on both terms** — fewest tokens and
a mid-pack rate — which is why it wins by 1.6x over second place.

### The finding worth keeping: token inflation under an unfamiliar language

Every pair writes more Swift than Python. **How much more varies 2.3x across
pairs**, and that is the discriminator gmail-archive never produced:

| pair | Python | Swift | inflation |
|---|---|---|---|
| **`ds4` × claude** | 3,234 | 3,835 | **1.19x** |
| `qwen38fnq3` × codex | 3,152 | 6,120 | 1.94x |
| `ds4anthropic` × codex | 4,662 | 9,082 | 1.95x |
| `qwen36coding` × claude | 2,268 | 5,232 | 2.31x |
| `ornith15` × codex | 7,618 | 20,788 | **2.73x** |

All five faced identical tasks, so the *relative* spread is meaningful even
though the Swift tasks are not difficulty-matched to the Python five — that
caveat bounds the absolute inflation figures, not the ordering.

**`ds4 × claude` barely notices the language change. `ornith15 × codex` nearly
triples.** Since token volume is the dominant term in agent wall time, how
gracefully a pair degrades on unfamiliar territory is arguably a better proxy
for real use than a pass rate everything saturates.

### The one failure was a compile failure

`ornith15 × codex` on `swift-downsample-buckets`: the agent worked normally —
18,694 output tokens, 30 tool calls, clean `turn.completed` — and produced Swift
that **did not compile**. Not a wrong answer; unbuildable code.

That is a failure mode Python cannot produce in this harness, and the pair that
produced it is the one that writes 2.7x more under Swift. One instance is not a
pattern, but "verbosity correlates with unbuildable output" is now a question
this data can be pointed at.

### Two harness defects the second repository exposed

Both were invisible to three days of Python-only runs:

**`swift test` writes compile errors to stderr and leaves stdout empty.**
`tests_pass` read only stdout, so the run's one real failure was recorded as
`"no output"` — true, useless, and indistinguishable from a harness fault.
`summarise_run` now falls back to stderr. pytest never had this: a Python syntax
error is a collection error on stdout.

**Shim-fronted backends did not declare their real server port.** `preflight`
warned that llama-server on :8020 was unexpected while it served exactly the
selected backend, because `qwen38fnq2`, `qwen38fnq3` and `glm53` named only the
shim. Fourth false positive of the same shape, and a check that fires on a
correct state is one nobody reads.

## #45 — Verbosity compounds with difficulty (2026-08-29, 8 trials)

**The question:** #44's single failure was `ornith15 × codex` emitting Swift that
did not compile, and that pair was also the one that inflated most moving from
Python to Swift (2.73x, against 1.19x for `ds4 × claude`). Does writing more, on
less familiar ground, predict producing code that will not build?

**Design.** Two harder Swift tasks against the two *extremes* of the #44
inflation table, not the whole field. Each target leans on a Swift construct with
no Python equivalent, so a model transferring Python habits has somewhere to
slip: `ScaleLadder.snap` uses `if`-as-expression assigned to a `let`, and
`SevenSegment.glyphs` mutates an array of value types in place — `Glyph` is a
struct, so the body cannot mutate `result.last` and has to rebuild the element
and assign it back. Both controls verified: each stubs to `fatalError` and fails
the 215-test suite before the agent runs.

**This is a screening run, not a measurement.** 2 trials per cell, well under
#23's 10-trial bar. The token ratios below are large and consistent and are the
trustworthy part; nothing here supports a pass-rate claim.

| pair | set | n | pass | median s | median out_tok | s/1k |
|---|---|---|---|---|---|---|
| `ornith15 × codex` | 1 (easier) | 9 | 8/9 | 318.9 | 20,788 | **15.3** |
| `ornith15 × codex` | 2 (harder) | 4 | 4/4 | 645.9 | 42,545 | **15.2** |
| `ds4 × claude` | 1 (easier) | 9 | 9/9 | 180.5 | 3,835 | 47.1 |
| `ds4 × claude` | 2 (harder) | 4 | 4/4 | 220.7 | 5,152 | 42.8 |

### The finding: the verbosity gap is not a stable pair property. It widens.

| | tokens | time |
|---|---|---|
| gap on set 1 | 5.42x | 1.77x |
| gap on set 2 | **8.26x** | **2.93x** |

Scaling from the easier set to the harder one, per pair:

| pair | time | tokens |
|---|---|---|
| `ornith15 × codex` | 2.03x | **2.05x** |
| `ds4 × claude` | 1.22x | **1.34x** |

**The terse pair degrades gracefully and the verbose pair inflates further.**
Harder tasks cost `ds4 × claude` 34% more output; they cost `ornith15 × codex`
105% more. This was the open question from #44 — whether 1.19x-2.73x was a fixed
trait — and the answer is that it is not fixed. It gets worse with difficulty,
which makes the cheap easy-task measurement an *under*-estimate of the spread on
hard work.

### Throughput did not move, so all of this is tokens

`ornith15 × codex` decoded at **15.3 s/1k on the easier set and 15.2 on the
harder one.** Time scaled 2.03x and tokens 2.05x. Harder tasks did not make the
model slower by any measurable amount; every extra second is an extra token.
Third time in this project a wall-time difference has resolved to a token count —
the rule in AGENTS.md holds on new material.

### The headline question is still n=1

**8/8 passed. No compile failures.** The unbuildable result from #44 did not
recur in four harder attempts on the pair that produced it. So verbosity has not
been shown to predict unbuildable code — the run answered the *secondary*
question cleanly and left the primary one where it was.

That is worth stating plainly: this run was designed around a hypothesis it did
not confirm, and the useful output came from the control variable.

### `restored_verbatim` 0/8, and a gate that is silently inert

Nothing was recalled: 0/8 verbatim, 7 distinct solutions across 8 trials
(`ornith15` produced the same code twice).

**`gates_delta` reports `{"ruff": 0}` on every Swift row.** ruff and mypy are
Python tools; on a Swift task they lint nothing and return a clean delta. The
gates are not wrong, they are *absent*, and an absent gate that reports `0` reads
exactly like a gate that passed. The quality axis is unmeasured on Swift — which
matters because "passes but is worse" (#4) was found by exactly these gates on
the Python side.

## #48 — The F16 tensors are required by the engine, not left behind (2026-08-30)

**The hypothesis is refuted, and by reading the engine rather than by measurement.**

@ShankPeople measured **+20% decode** moving GLM-5.3's KDA projection and head
from BF16 to Q8, and antirez agreed the choice was inefficient. Our primary's
GGUF has an analogous set of F16 tensors, so the question was whether the same
win was available on the model we actually run.

**It is not. `ds4` hardcodes F16 for almost every tensor involved.**

```
ds4: tensor blk.0.hc_attn_fn.weight has type q8_0, expected f16
```

From `ds4.c`, these are load-time requirements, not preferences:

| tensor family | GiB | engine constraint |
|---|---|---|
| `indexer.attn_q_b` | 0.328 | **`f16 or q8_0`** — the only eligible one |
| `attn_compressor_{gate,kv,ape}` | 0.487 | `tensor_expect_layout(..., DS4_TENSOR_F16, ...)` |
| `indexer_compressor_{gate,kv,ape}` | 0.082 | hardcoded F16 |
| `hc_attn_fn`, `hc_ffn_fn` | 0.062 | hardcoded F16 |
| `indexer.proj` | 0.010 | hardcoded F16 |

It is not only a load check. The Metal fused paths branch on the type as well —
`layer->attn_compressor_kv->type == DS4_TENSOR_F16` gates the fast kernels, and
one error message says outright: *"Metal graph indexer compressor expects paired
F16 projections"*. Quantizing these would not merely be rejected; on a build
that accepted them it would fall off the optimised path.

**So the eligible saving is `indexer.attn_q_b` alone: 0.154 GiB, or 1.7% of
per-token traffic.** That is below anything this instrument can resolve and is
not worth 104 minutes of requantization to chase.

**The GLM finding does not transfer.** GLM-5.3 and DeepSeek V4 Flash are
different code paths in the same engine. antirez's BF16 choice for GLM was a
choice; F16 here is a constraint.

### What the run produced anyway

Both arms were generated in full before the engine rejected arm B, so the work
is not wasted:

| arm | size | F16 | Q8_0 | loads |
|---|---|---|---|---|
| A — regenerated, unchanged | 90.889 GiB | 359 | 345 | **yes**, coherent, 45.39 t/s |
| B — `--attention q8_0` | 90.449 GiB | 88 | 616 | **no** — engine refuses |

**The pipeline is validated end to end.** Arm A regenerates the published model's
exact tensor-type structure (1328 tensors, F16=359, Q8_0=345, IQ2_XXS=74,
Q2_K=37, Q4_K=18), loads, and writes correct Python at `--temp 0`.

**But it does not reproduce the published bytes.** `--compare-tensor` fails on
both an expert tensor and `attn_q_a`, which does not depend on the imatrix — so
the difference is the HF revision, the quantizer version, or whatever `-fixed`
means in the published filename. **That is why both arms had to be regenerated**
rather than comparing against the shipped GGUF: otherwise every tensor would
have differed, including the 82 GiB of routed experts.

### The question that remains open

We still do not know **what binds decode on this model.** Two results narrow it:

- Speculation **costs** 23–44% here (#39), which is not what a dispatch-bound
  workload does.
- The bandwidth lever is now untestable by this route, because the bytes in
  question are structurally F16.

A cheaper probe is needed, or the decode-rate line closes. Recorded in #48.

### Corrections made during this work

Two of my own numbers were wrong and are fixed in the issue:

- **`token_embd` is not per-token traffic.** It is a lookup — one row, ~8 KB per
  token, not 0.99 GiB. Counting it inflated the F16 share from 11.5% to 20.2%.
- **The saving was 4.8%, not 9.5%**, once the embedding was excluded and only
  `--attention` tensors counted.

## GLM-5.3-Flash on the `glm-5.3-flash` branch — it works (2026-08-30)

**Three claims in this project's own notes were wrong, and all three are now
retired by measurement.**

Built `antirez/ds4` at `767e517` (branch tip) in `~/git/ds4-glm53`, ran
antirez's own `GLM-5.3-Flash-Q2.gguf` (90 GB, `glm5-next`) with his own recipe.

| run | ctx | prefill | generation |
|---|---|---|---|
| short prompt | 32768 | 78.88 t/s | **35.92 t/s** |
| **~7–8k token prompt** | 32768 | **460.21 t/s** | 29.57 t/s |
| coherence prompt | 8192 | 101.90 t/s | 35.86 t/s |

Planned memory at ctx 32768: **93.21 GiB** = 89.87 resident model + 2.98
buffers + 0.37 KV.

### 1. "Unusable — no engine loads it" is false

`GLM-5.3-Flash-Q2.gguf` loads, runs, and is **coherent at `--temp 0`** — correct
Python and a correct one-sentence explanation. The note in NEXT.md came from
trying it on the *wrong engine build*; the file was always fine, our engine was
not.

### 2. ds4#890's ">4096 tokens fails" does not reproduce here

A ~30 KB prompt prefilled at **460.21 t/s with no OOM and no failure**, on a
build that logs `full-attention prefill/work cap=4096; compact indexed decode is
used beyond the cap` — i.e. it genuinely crossed onto the compact indexed path
that #890 reports failing.

**So #890 is a memory-budget failure, not a prefill-path defect**, which is what
the thread concluded after our correction: the root cause was the Metal working
set, not the prompt length.

### 3. Our numbers independently corroborate ds4#892

That PR reports, on an M5 Max 128 GB — the same machine class:

| | ds4#892 | here |
|---|---|---|
| prefill, long prompt | 474 t/s @ 4500 tok | **460.21 t/s @ ~7–8k** |
| decode, serial | 33.0 t/s | **35.9 t/s** |

Two machines, same class, agreeing closely. That is a useful cross-check on both.

### Why it fits at all: the sysctl is load-bearing

`b0c31af` replaced the unconditional 110 GiB cap with host-aware budgeting, and
`budget_base = ds4_gpu_recommended_working_set_size()` — which returns Metal's
`recommendedMaxWorkingSetSize` (`ds4_metal.m:4244`). Tracing the arithmetic on
this host:

| | stock (107.52 GiB) | raised (112.00 GiB) |
|---|---|---|
| `base_gib >= 120`? | no | **no** — 112 < 120 |
| reserve applied | 32 GiB generic | 32 GiB generic |
| budget before override | 75.5 GiB | 80 GiB |
| sysctl override (−2 GiB margin) | none | **110 GiB** |

**The 128 GiB-host branch (`base_gib >= 120.0`) cannot fire on a 128 GiB Mac**,
because the *working set* is 107.52–112.00 GiB, never ≥ 120. So the budget comes
entirely from the sysctl-override path — and without
`iogpu.wired_limit_mb = 114688` the budget would be **75.5 GiB against a 89.87
GiB model.** The raised ceiling is not an optimisation here; it is the reason
this runs at all.

### What is still not answered

**Whether GLM-5.3 can drive an agent.** ds4#569 (tool-call parser stringifies
every argument) and ds4#816 (stateless clients never reuse the KV session) are
both still open and neither is touched by this branch. Everything above is
one-shot CLI generation, not an agent loop. **Do not promote GLM on this
evidence** — it clears the "does it run" bar and says nothing about the bar that
actually matters here.

## #54 — OpenCode works: 1/15 → 12/20, and the fix was the environment (2026-08-31)

**The client was never the whole story. We were handing it an un-excised copy of the answer.**

A model asked to implement `src/gmail_archive/parser.py` guesses the repo is at
`~/git/gmail-archive`, and it was right. That path held the **real, intact**
checkout: tests green, nothing to fix. OpenCode looked, correctly concluded
there was no work to do, and wrote nothing — recorded as a model failure with
the control's exact test counts.

| | pass rate |
|---|---|
| before, `ds4 × opencode` | **1/15 (7%)** |
| after, same backend/tasks | **12/20 (60%)**, Wilson 95% CI **39–78%** |

Per task, over the 15-trial run:

| task | result |
|---|---|
| `mbox-scan` | **3/3** |
| `parser-mbox-quoting` | **3/3** |
| `storage-blob-put` | 2/3 |
| `parser-date` | 1/3 |
| `mbox-strip-envelope` | **0/3** — passed in the earlier 5-trial run |

Two tasks are now fully reliable, which the pooled figure hides. Per-task rates
are **not** stable at n=3: four of five tasks flipped verdict between runs.

### Why OpenCode and not the others

From 276 retained client logs: **27 of 35 OpenCode trials worked outside the
trial checkout. Codex 0 of 135. Claude Code 0 of 106** — and Claude Code runs
here with `--permission-mode bypassPermissions`, so nothing was stopping it. It
stays put because its tools are workspace-rooted by construction. Codex ships an
OS sandbox on by default and calls bypassing it "dangerous".

OpenCode's boundary is `external_directory: {"*": "ask"}` (`agent.ts:122`).
Interactively a human declines the prompt. Under `opencode run` there is
**nobody to ask**. And [#41067](https://github.com/anomalyco/opencode/issues/41067)
means out-of-worktree paths reach the matcher as `../…`, so an explicit `deny`
never fires — confirmed on 1.18.25, where a `{"*": "deny"}` rule loaded, ordered
last, and was bypassed.

**Its safety model assumes a human in the loop, and unattended benchmarking
removes the human.** Every external source that praises it was using the TUI.

### What the fix actually was

Stand the **export** at the path the model guesses. The real checkout moves to
`<name>-real` for the batch; `git archive` puts the excised tree at
`~/git/gmail-archive` — right files, already excised, **no `.git` history the
original body was ever in**.

Running in-place was considered and rejected: `git show 56e55cc:…` hands over
the answer, and inspecting history is an obvious first move for a coding agent.

Supporting work, all client-agnostic:

- **`sandbox-exec` confinement below the client** — inherited through
  `bash → sh → cat`, proof against symlinked dirs, symlinked files, hard links
  and local `git clone`. Path *shape* is irrelevant because the kernel resolves
  before checking, which is exactly why it defeats #41067 where config cannot.
- **`ensure_pristine()`** — fetch, assert the pinned commit is reachable from an
  `origin/*` ref, `reset --hard`, `clean -ffd`, assert clean. Refuses rather
  than warns. A commit that exists only locally is rejected.
- **Crash recovery** — marker file, `restore_targets()` from `atexit`, batch
  start **and preflight**. It was needed within an hour of being written.
- **`paths_outside()`** — records `workspace_escapes` per row; answer-tree
  escapes auto-exclude.

### Three false negatives caught, two of them mine

Worth recording because it is #55's thesis in miniature — **the harness could
not tell a broken measurement from a bad result**:

1. **`source_repo_intact` inverted.** It watched the export, which is *supposed*
   to be modified. `verdict()` treats that as an escape, so a genuine pass would
   have been filed as a failure.
2. **`paths_outside` was handed a key that is never set**, so the worktree
   itself counted as an escape on every row.
3. **Denying `~/git/local-llm` killed every trial in 0.4s** — OpenCode `lstat`s
   the launcher's cwd. Seven rows excluded with cause. **Confinement has to
   leave the agent able to run.**

### The claim, stated narrowly

OpenCode runs this suite at roughly **60%** against a local model on local
hardware. It is not yet a client to trust unattended, and the variance is large
(47 s to a 1800 s timeout on the same task). But the open stack —
**OpenCode + DeepSeek V4 Flash + ds4 on an M5 Max** — works, which it did not
appear to this morning.

`passed == wrote_file` held across every trial. **OpenCode has never once
written incorrect code here.** Its failures are missing edits, not wrong ones —
a plumbing failure, not a reasoning one, which is why #56 asks whether a
different open agent charges less for the same property.
