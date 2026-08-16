# Agent benchmark — ds4 vs Qwen3.8 vs Qwen3.6

Run 2026-08-15/16. MacBook Pro M5 Max, 128 GiB, macOS 26.5.
5 tasks × 3 trials per backend. Methodology in [`METHODOLOGY.md`](METHODOLOGY.md).

| backend | model | quant | gen t/s | context |
|---|---|---|---|---|
| `ds4` | DeepSeek V4 Flash 0731, via `ds4-server` | mixed q2/q4 | 34.4 | 100,000 |
| `qwen` | `qwen3.8:27b-mlx`, Ollama 0.32.14-rc0 | 4-bit affine | 57.1 | 262,144 |
| `qwen36` | `qwen3.6:27b-mlx`, Ollama 0.32.14-rc0 | nvfp4 | 29.3 | 262,144 |

---

## Headline

**Every model solved every task, every time. 45/45.**

No failures. No timeouts. No run edited a test to pass. On this task set the
difference between them is entirely *how long they take*, never *whether they
get there*.

| | pass rate | median wall | median turns | median output tokens |
|---|---|---|---|---|
| **ds4** | **15/15** | **164.4 s** | **8** | **2,130** |
| qwen3.6 | 15/15 | 248.4 s | 13 | 3,725 |
| qwen3.8 | 15/15 | 272.2 s | 13 | 6,237 |

**ds4 is ~1.66× faster end to end than Qwen3.8 and needs 60% of the turns**,
while generating at 34.4 t/s against Qwen3.8's 57.1 t/s.

That inversion is the central result: **token throughput did not predict agent
latency.** The mechanism is visible in the last column — ds4 reaches the same
passing answer with a third of the output tokens.

### Newer is not better here

Qwen3.6 beats Qwen3.8 despite generating at **half** its token rate:

| | gen t/s | median wall | median output tokens |
|---|---|---|---|
| qwen3.6 | 29.3 | **248.4 s** | **3,725** |
| qwen3.8 | 57.1 | 272.2 s | 6,237 |

Identical median turn count (13), but 3.8 emits **67% more tokens** to do the
same work. It generates twice as fast and still finishes later. Whatever
changed between the two generations made the model more verbose per turn, and
on agent work that outweighs the speedup.

Total wall clock for all 15 trials: ds4 **42.1 min**, qwen3.6 **67.3 min**,
qwen3.8 **72.6 min**.

---

## Per task

Median wall seconds over 3 trials.

Median wall seconds over 3 trials, with the full range in brackets.

| task | broken | ds4 | qwen3.8 | qwen3.6 |
|---|---|---|---|---|
| `mbox-strip-envelope` | 3 | **127.2** (120–133) | 123.6 (109–190) | 149.6 (140–166) |
| `mbox-scan` | 13 | **163.5** (141–178) | 173.3 (84–281) | 331.1 (156–386) |
| `storage-blob-put` | 14 | **170.4** (140–216) | 501.8 (386–854) | 414.8 (257–457) |
| `parser-mbox-quoting` | 34 | **211.6** (191–226) | 272.2 (222–331) | 254.2 (248–537) |
| `parser-date` | 49 | **164.4** (138–206) | 280.6 (257–291) | 198.0 (135–208) |

ds4 posts the best median on **all five** tasks, though `mbox-strip-envelope`
is a dead heat with Qwen3.8 and within noise.

Neither Qwen is uniformly slower. Both are far more *variable*, and both
struggle on the same task.

---

## The `storage-blob-put` result — a Qwen-family weakness

`BlobStore.put` writes a blob durably: temp file, `fsync`, atomic rename,
sha256 verification. It breaks 14 tests, fewer than two other tasks.

| trial | ds4 | qwen3.8 | qwen3.6 |
|---|---|---|---|
| 1 | 170.4 s | 853.6 s | 257.1 s |
| 2 | 216.5 s | 501.8 s | 456.9 s |
| 3 | 140.4 s | 386.5 s | 414.8 s |
| **median** | **170.4 s** | 501.8 s | 414.8 s |

**Every single Qwen run on this task, across both generations, is slower than
every ds4 run.** Six for six, no overlap.

Qwen3.6's first trial at 257.1 s briefly suggested the problem was specific to
3.8. Trials 2 and 3 refuted that — it was the fast tail of a wide
distribution. **Both generations share the weakness; 3.8 amplifies it.**

Durability semantics are a plausible trap: `fsync` ordering and atomic-rename
behaviour is exactly the sort of thing a model can keep re-verifying when the
tests do not pin it down.

Trial 1 is the clearest look at why:

| | wall | turns | output tokens |
|---|---|---|---|
| ds4 (median) | 170.4 s | 8–11 | 1,742–3,865 |
| qwen trial 1 | 853.6 s | **35** | **24,970** |

**3× the turns and 7× the tokens** to reach the same passing result. That is
thrashing, not slow generation — and it is far too large to be explained by the
empty-virtualenv tax described in the methodology, which costs a few turns.

Durability semantics look like a plausible trap: `fsync` and atomic-rename
behaviour is exactly the kind of thing a model can keep re-verifying when the
tests do not pin it down.

---

## Variance

Qwen's spread is much wider than ds4's on the same task.

Ratio of slowest to fastest run on each task.

| task | ds4 | qwen3.8 | qwen3.6 |
|---|---|---|---|
| `mbox-strip-envelope` | 1.1× | 1.7× | 1.2× |
| `mbox-scan` | 1.3× | **3.3×** | 2.5× |
| `storage-blob-put` | 1.5× | 2.2× | 1.8× |
| `parser-mbox-quoting` | 1.2× | 1.5× | **2.2×** |
| `parser-date` | 1.5× | 1.1× | 1.5× |

**ds4 never exceeds 1.5× on any task.** Both Qwen builds exceed 2× on two
tasks each.

The behaviour is bimodal rather than noisy: most runs are direct, then one
wanders badly. Qwen3.6's `parser-mbox-quoting` went 248.4 s, 254.2 s — near
identical — and then 537.2 s. A model that looks stable over two trials is not
necessarily stable.

**Practical reading:** ds4 is the more predictable agent backend by a clear
margin. Both Qwen builds are faster than their medians suggest when they go
straight at a problem, and much slower when they do not.

**Practical reading:** ds4 is the more predictable agent backend. When Qwen goes
straight at a problem it is quick; when it does not, it burns 25,000 tokens
deciding.

---

## What this does not say

- **Not a quality ranking.** Both scored 100%. This measures completion and
  latency, not craftsmanship. A passing solution may still be ugly or slow.
- **Not a general claim.** Five single-function tasks in one Python repository.
  Nothing here tests multi-file refactors, ambiguity, or long-context recall.
- **Three trials detects large effects only.** The `storage-blob-put` gap is
  large and consistent enough to believe. The 0.97× and 1.06× results are
  within noise — read them as "no difference detected", not as a ranking.
- **Absolute times carry an environment tax.** A fresh worktree has no `.venv`,
  so part of every number is the agent working out how to run pytest. This is
  symmetric across backends, so the comparison holds, but the absolute figures
  are inflated. See METHODOLOGY §9.

---

## Cost of the run

About 105 minutes of wall time for the 30 trials, phased so only one model was
resident at a time — ds4 first at 90.9 GiB, then freed, then Qwen at ~18 GB.
Neither model was measured while paged out.

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
