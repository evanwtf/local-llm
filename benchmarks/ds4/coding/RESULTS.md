# HumanEval: mixed q2/q4 vs MXFP4

Answers the scoped question in [issue #14](https://github.com/evanwtf/ds4/issues/14):
**does the coding ranking of the two 0731 quants match the general-reasoning
ranking?** Both ladders in `benchmarks/ds4/0731/agent/` ran only the mixed build, so the
recommendation to prefer it over MXFP4 rested on general reasoning plus a
prefill-latency argument, never on code.

Machine: MacBook Pro M5 Max, 128 GiB, macOS 26.5.2. Engine `ds4` @ `a9511f3`.
Run 2026-08-10.

---

## Headline

| | mixed q2/q4 (resident) | MXFP4 (streamed) |
|---|---|---|
| **pass@1** | 158/164 = **96.3%** | 161/164 = **98.2%** |
| wall clock | **118.1 min** | 163.9 min |
| generation | **34.0 tok/s** | 16.1 tok/s |
| GPU energy | **54.8 Wh** | 57.9 Wh |

**The 3-problem gap is not statistically significant.** Paired McNemar, exact,
two-sided: 5 mixed-only failures against 2 MXFP4-only, **p = 0.453**. On this
sample the two models are indistinguishable in correctness.

## Neither model wrote a single incorrect program

Across 328 problem-attempts there is **not one logic error, wrong answer or
failed assertion**. Every failure, in both models, is the same thing: the model
did not stop before the 8192-token cap, so the code block was cut mid-docstring
and the file would not parse.

| | failures | of which truncations |
|---|---|---|
| mixed q2/q4 | 6 | **6** |
| MXFP4 | 3 | **3** |

Excluding truncated problems, mixed is 156/156 and MXFP4 is 160/160.

## HumanEval cannot answer the question

Both models sit at 96–98%, which is the ceiling of what this benchmark
resolves. HumanEval is from 2021, is almost certainly in the training data of
both quants, and its problems are short single functions. A benchmark where two
candidates differ by 3 items out of 164, none of them for reasons of coding
ability, has no power to rank them.

**This is a null result on the question asked.** It does not show the models are
equally good at code; it shows this instrument cannot tell them apart. Ranking
them on coding needs a harder suite — SWE-bench Lite, or the repo-local
reconstruction proposed in #14 — which is a substantially bigger job.

## The real finding: termination, not correctness

The two quants differ in **whether they stop**, and only there.

| | median | mean | p90 | truncated | total tokens |
|---|---|---|---|---|---|
| mixed q2/q4 | 783 | **1467** | **3080** | **8** | 240,589 |
| MXFP4 | 650 | 963 | 1575 | 4 | 158,072 |

Medians are 20% apart; means are 52% apart and p90 is 95% apart. Mixed is not
uniformly more verbose — on a typical problem the two are close. Mixed has a
**fatter right tail**: a minority of prompts send it into reasoning it never
closes out, and that tail is its entire failure set.

The clearest case is `HumanEval/118`. Mixed spent the full 8192 tokens and never
finished; MXFP4 answered in **738** — 11×. Three other contested problems took
MXFP4 2,700–3,900 tokens, so those are genuinely hard and mixed plausibly just
needed more room. `/118` is different in kind: routine for one quant,
non-terminating for the other.

**Why this matters more than the score.** For an agent, a turn that never
terminates is worse than a wrong answer — a wrong answer fails a test and you
retry, a runaway burns the full token budget and the wall clock first. Mixed's
latency has a heavy tail: usually fast, occasionally stalled. That matches
`ROUTING.md`'s advice to keep local work verifiable and cheap to check.

## Thermals and energy

Measured inside each run's own window (`thermal_mixed.csv`, `thermal_mxfp4.csv`,
one `powermetrics` sample per minute).

| | GPU clock | power | active | pressure | energy |
|---|---|---|---|---|---|
| mixed q2/q4 | 1390 MHz | 28.1 W | **99%** | Heavy 116/117 | 54.8 Wh |
| MXFP4 | 1474 MHz | 21.4 W | 81% | Moderate 87, Heavy 62, Nominal 13 | 57.9 Wh |

**Race to idle wins again.** MXFP4 draws 24% less power and still uses **6% more
total energy**, because it runs 39% longer. This is the third time this pattern
has appeared on this machine, after the `--power` capping result in
`../0731/REPORT.md`.

The two also differ in *character*, not just level. Mixed pins the GPU at 99%
active under Heavy pressure for the whole run. MXFP4 oscillates — 33.8 W at 99%
active while computing, 3.1 W at 32% while blocked on SSD reads — so no single
sample looks like its mean.

## What this changes

**Nothing about the recommendation.** Mixed q2/q4 stays the daily driver:

- Coding correctness is a statistical tie (p = 0.453).
- Mixed is 2.1× faster in generation and 39% faster end-to-end.
- The prefill argument in `../0731/claude_code_recommendations.md` §2 is
  untouched and remains the deciding factor for agent work.

**One caveat gets weaker.** §3c of `REPORT.md` calls MXFP4 "the better model"
based on 80/92 vs 76/92 on general reasoning. Coding points the same way —
161 vs 158 — but not significantly. The direction is consistent; the strength of
the claim should not be.

**One new caveat.** Mixed's runaway generation is a real, measured behaviour
(8/164 = 4.9% of prompts) and was not previously documented. Any harness driving
it needs a token cap and should treat cap-hit as a retry signal, not a result.

## Method

- HumanEval 164, pass@1, greedy (`temperature: 0.0`), `max_tokens: 8192`.
- Served by `ds4-server`; mixed resident with `--warm-weights`, MXFP4 with
  `--ssd-streaming --ssd-streaming-cache-experts 100GB` (matching the prior eval).
- `generate.py` checkpoints per problem; `score.py` runs each candidate in an
  isolated subprocess (`python -I`) under a 15 s timeout.

**Two harness decisions that affect the numbers:**

1. **`max_tokens` 8192, not 2048.** A smoke test truncated 1 problem in 3 at
   2048. The earlier eval in `benchmarks/ds4/0731/` was capped at 3000 and the cap
   *inverted the model ranking*; that mistake is not repeated here. 8192 still
   binds on 8 problems for mixed, and those are reported as failures rather than
   excluded, because a model that cannot finish inside 8192 tokens on a
   single-function problem has failed it.
2. **The prompt prelude is re-added at scoring time**, cut at the entry point
   rather than the first `def`. Several problems (`HumanEval/38`) define a
   helper above the target; cutting at the first `def` dropped the helper and
   failed correct code. That bug cost mixed one problem (95.7% → 96.3%) before
   it was found and fixed.

## Raw data

`samples_mixed.jsonl`, `samples_mxfp4.jsonl` (completions, token counts,
finish reasons), `results_*.jsonl` (pass/fail with failure detail),
`thermal_*.csv`.
