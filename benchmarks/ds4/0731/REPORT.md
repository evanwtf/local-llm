# DeepSeek V4 Flash 0731 — benchmark report

**Machine:** MacBook Pro, Apple M5 Max, 128 GiB unified memory, macOS 26.5.2
**Engine:** DwarfStar `ds4`, rebuilt from `main` @ `b030961`, Metal backend
**Date:** 2026-08-08 (runs 01:47 → 04:18)
**Results directory:** `/Users/evanhoffman/git/ds4/bench-0731/`

---

## Bottom line

**Switch to the mixed Q2/Q4 0731 build.** It won every measurement taken: best
perplexity, the best eval score (**76/92 vs 68/92** for both alternatives over
the full question set — see §3b), and it answers using 27% fewer output tokens
than the model you run today, finishing the same 92-question suite in 2h20m
against baseline's 3h07m. Generation speed is unchanged; the cost is ~6%
prefill throughput and 10 GiB more resident memory, both of which your machine
absorbs comfortably.

Most of the accuracy advantage is concentrated in **math** (AIME2025: 6
failures vs 11–12). If your work is not math-heavy, expect a smaller real-world
gap than the headline suggests — but it is still the best build on every axis
measured, including heat.

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

**Do not adopt `q2_0731`.** It is strictly dominated by the mixed build at no
speed advantage. Its value is as evidence: it isolates how much of the mixed
build's win comes from Q4 experts on the last six layers rather than from the
new weights. (§3 originally claimed it beat baseline on reasoning, 13/15 vs
12/15 — **withdrawn**; at 92 questions they tie exactly. See §3b.)

The one scenario that would change this: if you need very long contexts
(approaching 256k+) where 10 GiB of KV headroom becomes decisive, `q2_0731` is
the fallback, and it is still better than what you run today. **Untested — see
issue #5.**

## 3b. Full eval sweep (92 questions) — supersedes §3

The 15-question pass above was unrepresentative. Re-run over the **complete
embedded set of 92 questions**, `-n 8000`, same binary, same conditions:

| model | passed | rate | runtime | total tokens | avg/question |
|---|---|---|---|---|---|
| **mixed q2/q4 0731** | **76/92** | **82.6%** | **2h20m** | 286,294 | **3,111** |
| q2_0731 | 68/92 | 73.9% | 2h25m | 298,576 | 3,245 |
| baseline | 68/92 | 73.9% | 3h07m | 392,618 | 4,267 |

**The recommendation holds, and is now properly evidenced.** An 8-question
margin over 92 is a real separation, unlike the single-question margin the
15-question pass produced.

Failures by category:

| category | mixed q2/q4 | q2_0731 | baseline |
|---|---|---|---|
| AIME2025 | **6** | 12 | 11 |
| GPQA Diamond | 5 | 5 | 7 |
| SuperGPQA | 4 | 6 | 4 |
| COMPSEC | 1 | 1 | 2 |

**Nearly the entire advantage is AIME2025** — 6 failures against 11–12. Math
reasoning is where Q4 experts on layers 37–42 pay off; the other categories are
within a question or two of each other. If your work is not math-shaped, the
practical gap is smaller than the headline rate suggests.

### Two claims from §3 that did not survive

1. **"15/15" was optimistic.** The true rate is 82.6%. The first 15 questions
   are easier than the full set. Nothing was wrong with the measurement; the
   sample was just too small to carry the conclusion.
2. **"q2_0731 beats baseline on reasoning" is withdrawn.** At 15 questions it
   led 13–12. At 92 they tie *exactly* (68/92 each). The apparent edge was
   noise. What survives is a **timing** difference: q2_0731 reaches the same
   accuracy in 2h25m vs baseline's 3h07m, 29% less time at load.

Token efficiency is also more modest than §3 implied: 3,111 vs 4,267 avg tokens
is a 27% reduction, not the ~50% the small sample showed.

### Thermals

Logged throughout the 7.5-hour sweep (`thermal_watch.csv`, 228 samples), using
two no-sudo proxies: macOS thermal/performance warning levels, and sustained
throughput.

- **Zero thermal or performance warnings recorded**, across all three runs.
- Sustained generation held **34–36 t/s (mean 34.71)** for 7.5 hours of
  continuous full-GPU load, with no downward trend.

> **CORRECTION (2026-08-08 20:22).** This section originally concluded "this
> machine does not throttle under sustained multi-hour load." **That was wrong.**
> Once a passwordless `powermetrics` rule was installed, direct measurement
> showed thermal pressure **Heavy** and the GPU clamped at ~1274–1295 MHz
> against a **1620 MHz** ceiling — zero residency at 1470/1578/1620 — while
> requesting maximum P-state 100% of the time. It draws ~16–18 W in that state.
>
> Both proxies failed in the same direction. `pmset` never recorded a warning
> despite Heavy pressure, and flat throughput showed only that the machine had
> reached a *stable* state, not an *unthrottled* one. It throttles quickly, then
> holds — externally indistinguishable from never throttling.
>
> What survives: performance is stable and predictable over many hours, so the
> benchmark comparisons between models remain valid (all measured under the same
> clamped conditions). What does not: the claim that no performance is being left
> on the table. Roughly 21% of GPU clock is.
>
> Real telemetry now logged in `thermal_watch2.csv` via `thermal_watch2.sh`.

The cost of running these models hard is comfort, fan noise, **and ~21% of GPU
clock**.

For heat, the relevant quantity is time at load, and the ranking follows wall
clock directly: baseline runs **33% longer** than the mixed build for identical
work (3h07m vs 2h20m). The model that is best on quality is also the one that
heats the laptop least — there is no trade-off to make.

---

## 3c. SSD-streamed 4-bit models (issue #3) — better answers, much slower prompts

MXFP4 (145.3 GiB) and Q4 (153.3 GiB) exceed 128 GiB and require
`--ssd-streaming`. Both were measured; **MXFP4 wins on every axis** (better
perplexity, faster, 8 GiB smaller), so it carried the full eval.

### Quality

| model | perplexity | eval | avg tokens/q | runtime |
|---|---|---|---|---|
| **MXFP4 0731 (streamed)** | **4.5078** | **80/92 (87.0%)** | **2,157** | 3h03m |
| Q4 0731 (streamed) | 4.5629 | not run | — | — |
| mixed q2/q4 0731 (resident) | 5.9082 | 76/92 (82.6%) | 3,111 | 2h20m |

MXFP4 is genuinely better: **+4 questions and 31% fewer tokens per answer.**
Two independent signals agree — a 23.7% perplexity advantage and a higher eval
score — so this is not a lucky run.

Failures by category:

| category | MXFP4 | mixed q2/q4 |
|---|---|---|
| AIME2025 | **4** | 6 |
| GPQA Diamond | **4** | 5 |
| SuperGPQA | 4 | 4 |
| COMPSEC | **0** | 1 |

### Speed — the catch

| | prefill @8192 | gen steady @8192 |
|---|---|---|
| mixed q2/q4 (resident) | **488.5** | **35.5** |
| MXFP4 (streamed) | 115.7 | 18.1 |
| MXFP4 (streamed, 100 GB expert cache) | 63.6 | 20.9 |

Generation drops to ~51–59% of resident. **Prefill drops to 13–24%**, and that
is what decides it. Concretely, a 30,000-token prompt:

- resident mixed q2/q4: ~61 s
- MXFP4 streamed: ~260 s

Over four minutes versus one, on every long prompt.

### Recommendation: depends on your workload

**Short prompts, hard problems → MXFP4 streamed.** Chat-style reasoning, maths,
one-shot questions. Better answers, fewer tokens, and the prompt is too short
for the prefill penalty to bite. It is also *cooler*: mean 1430 MHz vs ~1274–1295
for resident runs, because I/O stalls let the GPU shed heat (see thermals).

**Long prompts, agentic work, large context → mixed q2/q4 resident.** Anything
that repeatedly feeds large contexts pays the prefill penalty on every turn, and
4 questions out of 92 does not buy back four minutes per prompt.

**If you only want one: keep the mixed q2/q4 resident build.** The 87.0% vs
82.6% gap is real but modest, and prefill throughput is felt constantly in
interactive use whereas the accuracy difference shows up occasionally. MXFP4 is
worth keeping on disk for hard one-off problems.

Note the mixed build finished the suite in 2h20m against MXFP4's 3h03m *despite*
generating 44% more tokens — streaming's per-token cost outweighs MXFP4's
efficiency.

---

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

---

## 5. Backing a coding agent (Claude Code, Codex, Pi)

**Use the mixed q2/q4 build, resident, via `ds4-server`.** Not MXFP4, despite
its better eval score.

A coding agent is **prefill-dominated**. Every turn resends a large system
prompt, tool definitions, file contents and a growing transcript; generation is
a few hundred tokens against tens of thousands prefilled. That is precisely the
axis where the models differ most:

| | prefill @8192 | a 30k-token turn |
|---|---|---|
| mixed q2/q4 (resident) | 488.5 t/s | ~61 s |
| MXFP4 (streamed) | 115.7 t/s | ~260 s |

Four minutes of latency per turn makes an agent unusable. Agents amortise
accuracy across many cheap turns rather than winning single hard problems, so
MXFP4's +4 questions does not repay a 4× prefill cost.

Two settings matter more here than anywhere else in this report:

- **`--warm-weights`.** Only +3.3% mean on a benchmark sweep (§2 of issue #2),
  but a server is the case it was built for: one long-lived load serving many
  prompts, so the +36% on early prefill is paid once and never decays.
- **Prefix caching.** `ds4-server` keeps a rax-backed KV store
  (`ds4_kvstore.c`) so an unchanged system prompt and file context are reused
  instead of re-prefilled. For agent workloads this plausibly matters more than
  the choice of model.

Suggested launch:

```sh
./ds4-server \
  -m gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-...-fixed-0731.gguf \
  --warm-weights --ctx 100000
```

Claude Code connects through the Anthropic-compatible `/v1/messages` endpoint;
the README documents a wrapper (`ANTHROPIC_BASE_URL=http://127.0.0.1:8000`,
`ANTHROPIC_MODEL=deepseek-v4-flash`) around line 1258.

> **Caveat — nothing here measured coding.** The 92-question set is GPQA
> Diamond, SuperGPQA, AIME2025 and one COMPSEC category; only the last touches
> code, and it is security analysis of C snippets, not code generation. The
> 87.0% vs 82.6% ranking is *general reasoning*, extrapolated to coding on the
> assumption the two correlate. That assumption is untested here. A real coding
> benchmark (SWE-bench-style, or HumanEval) would be needed before treating this
> as a coding recommendation rather than a latency one.

---

## Open questions

| # | question | status |
|---|---|---|
| 5 | long-context behaviour beyond 64k | **not measured** — matters for agents; the mixed build uses 90.9 of 128 GiB, so KV headroom is tighter than the q2 builds |
| 4 | GLM 5.2 as an alternative family | not started (197 GiB, streamed) |
| 7 | refresh stale `speed-bench/m5_max_128gb_resident.csv` | superseded data exists here; upstream contribution should state whether it is a first-run or steady-state number |
| 8 | disk: ~320 GiB reclaimable in `gguf/`, 636 GiB in Ollama | pruning deferred until #4/#5 finish |
| — | coding benchmark | **gap** — see caveat above |
| — | thermal follow-ups: cold-start ramp, `--power` sweep, joules per question | queued; `powermetrics` now available passwordless |

---

## Reproducing

`run_bench.sh` (speed + perplexity + capped eval) and `run_eval2.sh` (8000-token
eval) in this directory drive everything. Both pass `-m` explicitly to every
invocation and restore `ds4flash.gguf` to the baseline model on completion, so
they never silently change your default.
