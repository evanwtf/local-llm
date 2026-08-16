# Agent benchmark — ds4 vs Qwen3.8, first series

Run 2026-08-15. MacBook Pro M5 Max, 128 GiB, macOS 26.5.
5 tasks × 2 backends × 3 trials. Methodology in [`METHODOLOGY.md`](METHODOLOGY.md).

| backend | model | context |
|---|---|---|
| `ds4` | DeepSeek V4 Flash mixed q2/q4 0731, via `ds4-server` | 100,000 |
| `qwen` | `qwen3.8:27b-mlx`, via Ollama 0.32.14-rc0 | 262,144 |

---

## Headline

**Both models solved every task, every time. 30/30.**

No failures. No timeouts. No run edited a test to pass. On this task set the
difference between them is entirely *how long they take*, never *whether they
get there*.

| | pass rate | median wall | median turns |
|---|---|---|---|
| ds4 | **15/15** | **164.4 s** | **8** |
| qwen | **15/15** | 272.2 s | 13 |

**ds4 is ~1.66× faster end to end and needs about 60% of the turns**, despite
generating at 34.4 t/s against Qwen's 57.1 t/s.

That inversion is the result. Token throughput did not predict agent latency.

---

## Per task

Median wall seconds over 3 trials.

| task | tests broken | ds4 | qwen | ratio |
|---|---|---|---|---|
| `mbox-strip-envelope` | 3 | 127.2 s | 123.6 s | 0.97× |
| `mbox-scan` | 13 | 163.5 s | 173.3 s | 1.06× |
| `storage-blob-put` | 14 | 170.4 s | **501.8 s** | **2.95×** |
| `parser-mbox-quoting` | 34 | 211.6 s | 272.2 s | 1.29× |
| `parser-date` | 49 | 164.4 s | 280.6 s | 1.71× |

Two tasks are a dead heat (0.97×, 1.06×), two are moderate ds4 wins, and one is
a rout. Qwen is not uniformly slower — it is far more variable, and one task
goes badly wrong.

---

## The `storage-blob-put` result

`BlobStore.put` writes a blob durably: temp file, `fsync`, atomic rename,
sha256 verification. It breaks 14 tests, fewer than two other tasks.

| trial | ds4 | qwen |
|---|---|---|
| 1 | 170.4 s | 853.6 s |
| 2 | 216.5 s | 501.8 s |
| 3 | 140.4 s | 386.5 s |

**Three for three, with no overlap: Qwen's fastest run is 1.8× ds4's slowest.**
This is not one unlucky path.

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

| task | ds4 range | ds4 spread | qwen range | qwen spread |
|---|---|---|---|---|
| `mbox-strip-envelope` | 120.3–133.3 s | 1.1× | 109.3–190.2 s | 1.7× |
| `mbox-scan` | 141.3–178.1 s | 1.3× | 84.4–281.4 s | **3.3×** |
| `storage-blob-put` | 140.4–216.5 s | 1.5× | 386.5–853.6 s | 2.2× |
| `parser-mbox-quoting` | 190.9–226.0 s | 1.2× | 222.5–330.6 s | 1.5× |
| `parser-date` | 138.2–206.2 s | 1.5× | 257.2–291.2 s | 1.1× |

ds4 lands in tight bands — its widest spread on any task is 1.5×. Qwen's
`mbox-scan` runs span 84.4 s to 281.4 s, a **3.3× spread on the same problem**:
its fastest run of any task, and one of its slowest.

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
