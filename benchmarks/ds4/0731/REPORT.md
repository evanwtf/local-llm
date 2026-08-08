# DeepSeek V4 Flash 0731 — benchmark report

**Machine:** MacBook Pro, Apple M5 Max, 128 GiB unified memory, macOS 26.5.2
**Engine:** DwarfStar `ds4`, rebuilt from `main` @ `b030961`, Metal backend
**Date:** 2026-08-08 (runs 01:47 → 04:18)
**Results directory:** `/Users/evanhoffman/git/ds4/bench-0731/`

---

## Bottom line

**Switch to the mixed Q2/Q4 0731 build.** It won every measurement taken: best
perplexity, a perfect 15/15 on the eval harness, zero truncated reasoning
chains, and it reaches its answers using less than half the output tokens of
the model you run today. Generation speed is unchanged; the cost is ~6% prefill
throughput and 10 GiB more resident memory, both of which your machine absorbs
comfortably.

```sh
ln -sfn gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf ds4flash.gguf
```

**Do not enable DSpark speculative decoding** (section 4). It is lossless and
correct, but costs 23–44% of generation speed on this machine at every setting
tried. Leave it off.

Your symlink is currently still pointing at the **old** baseline model — nothing
was changed on your behalf. The command above makes the switch; all three GGUFs
are on disk, so reverting is a one-line symlink change either way.

---

## What was compared

| Tag | File | On disk |
|---|---|---|
| `baseline` | `...chat-v2-imatrix.gguf` (pre-0731, what you run today) | 80.76 GiB |
| `q2_0731` | `...chat-v2-imatrix-0731.gguf` | 80.76 GiB |
| `q2q4_0731` | `...Layers37-42Q4KExperts...-fixed-0731.gguf` | 90.89 GiB |

All three are the weights behind the DeepSeek-V4-Flash-0731 release Ollama just
made their cloud default, except `baseline`, which is the previous release. The
`0731` files came from `antirez/deepseek-v4-gguf` via `./download_model.sh`.

All three run **fully resident** on 128 GiB — no SSD streaming, no context
compromise. Measured memory plans: 81.62 GiB (both Q2 builds) and 91.75 GiB
(mixed), against a 128 GiB budget.

### Methodology note

Every run in this report used the **same freshly built binary**. Your existing
`speed-bench/m5_max_128gb_resident.csv` was produced on 2026-06-28 by a binary
that predates the Metal MoE and indexed-prefill commits, and it is **not** a
valid baseline: rebuilt, the *same old model* went from 412 → 621 t/s prefill at
2048 ctx. Comparing 0731 against that file would have credited a ~50% engine
improvement to the new weights. The `baseline` row below is a fresh measurement.

---

## 1. Speed

`ds4-bench` over `speed-bench/promessi_sposi.txt`, context 2048 → 65536 in 2048
steps, 128 generated tokens per frontier. Grid is a superset of the historical
CSV, so every old data point remains directly comparable.

| | prefill mean | prefill @2048 | prefill @65536 | gen mean | gen @2048 | gen @65536 |
|---|---|---|---|---|---|---|
| baseline | 465.1 | 621.0 | 402.1 | 31.95 | 38.70 | 29.07 |
| q2_0731 | 465.1 | 593.0 | 399.0 | 32.15 | 36.16 | 29.30 |
| q2q4_0731 | 439.2 | 468.8 | 385.4 | 31.99 | 35.90 | 29.19 |

*(tokens/sec)*

**Speed is not a differentiator.** Mean generation is ~32 t/s for all three and
the spread across the whole sweep is under 1%. Mean prefill is identical between
baseline and Q2 0731; the mixed build gives up 5.6%, concentrated at short
contexts (it starts at 469 t/s rather than 621), and the gap closes to ~4% by
64k. In interactive use you will not perceive the difference.

Raw data: `speed_baseline.csv`, `speed_q2_0731.csv`, `speed_q2q4_0731.csv`.
Plot: `speed_q2q4_0731_ts.svg`.

*Caveat:* every sweep reports `kvcache_bytes=0` at the final 65536 frontier while
all other rows are populated. This looks like a reporting artifact at the last
step of the sweep, not a real measurement. It does not affect the throughput
columns.

## 2. Perplexity

Teacher-forced NLL over an identical 300 KB held-out slice of
`promessi_sposi.txt` (the scorer caps at the 32,768 context, so 32,736 tokens
were scored for each model — same tokens, same order, every time).

| | avg NLL | perplexity | vs baseline |
|---|---|---|---|
| baseline | 1.78736 | **5.9737** | — |
| q2_0731 | 1.84681 | **6.3396** | +6.1% (worse) |
| q2q4_0731 | 1.77634 | **5.9082** | −1.1% (better) |

The interesting comparison is *within* the 0731 weights: moving layers 37–42 to
Q4 experts recovers **6.8%** of perplexity (6.3396 → 5.9082). The 0731 weights
tolerate pure IQ2_XXS noticeably worse than the previous release did, which is
the whole argument for the mixed quant on a 128 GiB machine.

**Read this metric narrowly.** The corpus is 19th-century Italian literary prose,
which is far from how you use the model, and section 3 shows Q2 0731 *beating*
baseline on reasoning despite scoring 6.1% worse here. Perplexity on
out-of-domain prose and task accuracy are measuring different things; where they
disagree, trust the eval.

## 3. Eval harness

`ds4-eval`, first 15 embedded questions (GPQA Diamond, SuperGPQA, AIME2025),
graded automatically. Two passes were run.

### Primary pass — 8000 token budget

| | passed | failures hitting the cap | total gen tokens | avg/question | wall clock |
|---|---|---|---|---|---|
| baseline | 12/15 | 3 | 52,956 | 3,530 | 25 min |
| q2_0731 | 13/15 | 1 | 32,467 | 2,164 | 15 min |
| q2q4_0731 | **15/15** | **0** | **25,661** | **1,710** | **12 min** |

The mixed build answered every question correctly, never ran out of budget, and
did it in **half the tokens** baseline needed. That token efficiency compounds
with the throughput numbers: despite being 5.6% slower per token at prefill, it
finished the whole suite in 12 minutes against baseline's 25, because it simply
generates less.

Both baseline failures and the remaining pure-Q2 failure are dominated by
**non-termination**: the model is still mid-argument when the budget runs out,
not producing a wrong answer. Three of baseline's failures hit the ceiling even
at 8000 tokens.

### Secondary pass — 3000 token budget

Kept for completeness, and as a caution about how it was nearly misread:

| | passed | failures hitting the cap |
|---|---|---|
| baseline | 11/15 | 4 |
| q2_0731 | 10/15 | 4 |
| q2q4_0731 | 14/15 | 1 |

At 3000 tokens Q2 0731 looks **worse** than baseline (10 vs 11); given room to
finish it is **better** (13 vs 12). The capped pass was measuring which model
gets cut off first, not which model is right. Only the 8000-token pass should be
quoted. Full graded traces are in `eval8k_*.trace` (and `eval_*.trace` for the
capped run) if you want to read the actual failures.

---

## Recommendation

**Adopt `q2q4_0731`.** Three independent measurements agree, which is what makes
this convincing rather than a lucky 15-question run: it has the best perplexity,
the best eval score, and the fewest runaway generations. The 10 GiB of extra
resident memory is the real cost, and on 128 GiB it still leaves room for a
normal working context.

**Do not adopt `q2_0731`.** It is a genuine improvement over baseline on
reasoning (13/15 vs 12/15, and far more token-efficient), but it is strictly
dominated by the mixed build at no speed advantage. Its value is as evidence: it
isolates how much of the mixed build's win comes from Q4 experts on the last six
layers rather than from the new weights.

The one scenario that would change this: if you need very long contexts
(approaching 256k+) where 10 GiB of KV headroom becomes decisive, `q2_0731` is
the fallback, and it is still better than what you run today.

## 4. DSpark speculative decoding — tested, do not enable

**Result: DSpark is a consistent loss on this machine. Leave it off.**

DSpark is a 5.58 GiB auxiliary draft model, 0731-only, that reads hidden states
from the main model and proposes up to five future tokens for the target model
to verify. Measured on `q2q4_0731`, greedy (`--temp 0`), 512 tokens, three
prompts (one prose, two code-heavy — the content type the README says benefits
most). Generation t/s:

| config | prose | code | code | vs off |
|---|---|---|---|---|
| off | 38.40 | 39.08 | 37.29 | — |
| `--dspark-strict` (capture, target-only decode) | 39.59 | 38.92 | 38.31 | ~0% |
| `--dspark` confidence 0.7 (default) | 31.45 | 29.18 | 28.91 | −23% |
| `--dspark` confidence 0.5 | 27.04 | 26.14 | 26.08 | −31% |
| `--dspark` confidence 0.3 | 24.31 | 23.67 | 23.36 | −38% |
| `--dspark` confidence 0 (forced 5-token blocks) | 21.45 | 21.29 | 21.45 | **−44%** |

Two things this isolates:

- **The cost is speculation, not instrumentation.** `--dspark-strict` loads
  DSpark but keeps target-only decode, and it matches *off* to within noise. So
  hidden-state capture (layers 40, 41, 42) is effectively free; the loss comes
  entirely from proposing and verifying.
- **The relationship is monotonic in the wrong direction.** Lowering the
  confidence threshold admits more proposals and makes it *steadily slower*,
  bottoming out at −44% with forced five-token blocks. Verification is costing
  more than accepted tokens repay, on every prompt type tried — including the
  boilerplate-heavy code generation that is DSpark's documented best case.

**It is not broken.** Generated text was diffed against the non-speculative
baseline for all nine speculative configurations and was **byte-identical every
time**, which is the correctness property speculative decoding promises at temp
0 (target model authoritative, rejected drafts discarded). It works exactly as
specified; it just does not pay off here. This is consistent with the README,
which calls DSpark "still experimental and explicitly opt-in" and warns that
low-yield prompts "can be no faster or even slower."

Raw logs: `dspark2/`. A first pass in `dspark/` swept `--mtp-draft` 1–4 and
produced flat results — that flag drives the legacy one-stage MTP path which
DSpark *replaces*, so it was inert. Ignore that directory; `--dspark-confidence`
is the real control.

*Caveat:* measured on one model (`q2q4_0731`) with 512-token generations and
short prompts. The picture could differ at long contexts or with a
CUDA backend, where the kernel paths are quite different.

---

### Worth following up

- **15 questions is a small sample.** 15/15 vs 12/15 is a real signal reinforced
  by the perplexity and token-efficiency numbers, but a wider run (drop
  `--questions`, default is the full set) would firm it up if you want more
  confidence before committing.
- The old `speed-bench/m5_max_128gb_resident.csv` is now stale in two ways (old
  engine, old model). Worth replacing with `speed_q2q4_0731.csv` if you intend
  to upstream numbers.

---

## Reproducing

`run_bench.sh` (speed + perplexity + capped eval) and `run_eval2.sh` (8000-token
eval) in this directory drive everything. Both pass `-m` explicitly to every
invocation and restore `ds4flash.gguf` to the baseline model on completion, so
they never silently change your default.
