# mlx-serve vs ds4

A living scoreboard for the two stacks we run on the M5 Max. Add a row to
**Measurements** each time one of them is measured; do not rewrite history.

**Last updated:** 2026-09-10, after #282.

## Standing, as of #282 (2026-09-10) — mlx-serve 26.9.2 takes about half the wall

> **Findings so far, not a verdict — no recommendation change yet.** The ds4 arm
> below is `ffd85d42`, which turned out to be **one head behind** the ivan
> `qwen3.8-flash-next` tip `6c1e8367` (the #228 head, reported ~+8.9% decode). A
> **both-latest** retest — ds4 `6c1e8367` vs mlx-serve 26.9.2, multiple runs — is
> under way; `RECOMMENDATIONS.md` stays unchanged until it confirms these numbers
> hold with ds4 on its latest release.

Two independent 2+2-sweep runs (60 rows/arm, 120 total) of the stacks as first
measured: ds4 `qwen38fnds4kimat` @ `ffd85d42` vs **mlx-serve 26.9.2** (Homebrew)
on `Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit`, PLD on (recorded per row, #262).

Pooled per-task geometric-mean wall, summed over 15 tasks: **ds4 1429 s,
mlx-serve 751 s** — mlx-serve took **53% of the summed time (47% less)**; the
paired per-task geometric ratio is **1.76 (mlx took 57% of the typical-task
time, 43% less)**. mlx-serve wins 13 of 15 tasks; ds4 wins `parser-mbox-quoting`
and ties `script-transform`. Pass parity: ds4 60/60, mlx-serve 59/60. Both runs
agree (ratios 1.85 and 1.68) and within each run both position orders agree, so
it is not position bias.

**This reverses #191's dead heat**, and ds4 did not regress: its per-task times
here (~80–140 s, one 288 s tail) match #191's ds4. mlx-serve roughly halved its
own wall between 26.9.1 and 26.9.2 — the merged `perf(qwen4)` batch (QSA,
allocator, prefix-cache, and #383's EOS-first spec fix, which also removed
#191's 1377 s tail).

**Still a stack comparison.** Engine, quantization and speculative decoding move
together: mlx-serve speculates by default (PLD on), ds4 `kimat` does not. Part of
the win is speculation — but it is the engine's shipped default, "what you get
when you install it," which is what this project measures. It cannot be credited
to the engine alone (#138, #191). mlx-serve also holds ~100 GiB resident against
ds4's ~68–80 GiB.

---

## The two stacks

| | mlx-serve | ds4 |
|---|---|---|
| engine | mlx-serve 26.9.1 (Homebrew) | ds4, tree `ds4-ivan-qwen38fn` |
| weights | `Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit` (~100 GiB) | `Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf` (68 GiB) + PLE sidecar |
| speculative decoding | **prompt-lookup, ON by default** (`draft_len=5`, `key_len=3`) | none unless an MTP sidecar is passed |
| tool-call shim | none — runs bare on :11234 | behind `qwen_tool_shim` on :8101, strip ON |
| port | 11234 | 8000 (shim on 8101) |

## What is comparable, and what is not

**Only the stacks.** The original #191 premise — same weights, two engines —
is impossible by three independent routes:

1. the MLX path has no GGUF reader,
2. there is no PLE sidecar for the GGUF path in MLX,
3. the bundled llama.cpp has no `qwen4exp` graph.

So **engine, quantisation and speculative decoding always move together.** No
sentence about these two may credit a component. Any run comparing them
head-to-head is a stack comparison and must be reported as one.

The one-variable experiments that *can* attribute anything:

- mlx-serve with and without `--no-pld` → isolates speculative decoding (#224)
- ds4 with and without its MTP sidecar → the mirror image (#39)

## Standing, as of #191 (2026-09-08)

60 trials per arm, 15 tasks x 4 sweeps, harness pinned at `7932a5f`.

```
                 total     mean    worst    pass
ds4              5229s    87.2s   189.7s   60/60
mlx-serve        5297s    88.3s  1377.4s   59/60
```

**Total wall is a dead heat — mlx-serve is 1.3% slower.**

Per-task, it is not close: mlx-serve wins 13 of 15, usually by 40-60%.

| endpoint | value |
|---|---|
| paired wall ratio (geometric, registered) | **0.68** (95% CI 0.54-0.87) |
| median per-task ratio | 0.58 |
| win / loss / tie | 12 / 1 / 2 |
| pass, paired by task | 1 down, 0 up, 14 tied — **p = 1.000** |
| deaths | 0 / 0 |
| verdict | SCREEN PASS |

### The whole difference is one task -- SUPERSEDED by #225

**The data below stands; the reading of it does not.** #225 re-ran the same
15 tasks with 26.9.1 as the control arm -- the same build that produced the
1377.4s trial -- and `parser-mbox-quoting` came back at **24 turns / 96.3s
max**. It did not reproduce. The tail landed on
`swift-sevensegment-glyphs` (117 turns / 701.9s) on the control and
`swift-chartaxis-spacing` (44 turns) on the new arm instead, and in #191
sevensegment was unremarkable at 20 turns.

So this task is not the problem task. The tail is a rare event that moves
between tasks and fires on either build, and a single draw of it landed here.
A full-ledger census puts `parser-mbox-quoting` at 7/214 = 3.3% -- mid-pack,
not special. **Do not drop this task and quote the remainder**, which is what
the paragraph below invited.

```
parser-mbox-quoting   mlx  39.4  40.4  283.2  1377.4 s     (9, 9, 35, 334 turns)
                      ds4  45.0  50.1   53.9    66.4 s     (8, 8,  9,   9 turns)
```

Drop that single task and mlx-serve finishes **29% faster** overall. It cost
1740s against ds4's 216s and erased every win.

### Known behaviour: mlx-serve is bimodal, ds4 is not

- ds4's maximum across all 120 rows: **20 turns**. mlx-serve's: **334**.
- Sweep durations: ds4 spanned 2m48s across four sweeps (23m19s / 24m57s /
  26m07s / 24m34s); mlx-serve spanned 33 minutes (14m34s / 20m19s / 47m40s /
  16m45s). The same shape at a different granularity, with no cut chosen by
  anyone.
- Two tasks show true excursions: `parser-mbox-quoting` (334 turns) and
  `mbox-quoting-both-halves` (162). Separately, `swift-downsample-buckets`
  runs 22-29 turns on mlx as its *normal* behaviour against ds4's 11-14 — a
  shift, not an excursion, and a different finding.
- Every excursion **passed**. This is a wall-time tail, not a reliability one.

**Practical read:** mlx-serve is typically faster and occasionally much slower.
Median latency favours mlx-serve; worst case favours ds4.

**Now measured, not asserted (#225 census, `scripts/tail_events.py`).** Over
2084 rows, 67 events at `num_turns > 20`:

    mlx-serve    23/ 196 = 11.7%   95% CI [ 7.9, 17.0]
    all others   44/1888 =  2.3%   95% CI [ 1.7,  3.1]

Non-overlapping, ~5x. **But mlx-serve does not own the high rate**: `qwen36`
runs 14.7% (5/34) and is not mlx at all, and six other backends have
overlapping intervals. There is a high-tail cluster and a low-tail cluster,
and mlx-serve sits in the high one. Writing "mlx-serve is tail-prone" repeats
the #191 error -- a true comparison against one chosen reference, restated as
a property.

Two confounds, controlled separately, agreeing. **The shim-controlled
comparison is era-confounded and the era-controlled comparison is
shim-confounded**, so neither is clean alone:

- *Shim, cross-era.* bare ds4 0/49, shimmed ds4 10/665 = 1.5%. P(observing 0)
  is 0.477 at the ds4 rate and **0.002** at the mlx rate -- the shim is not
  the explanation, though 0/49 cannot pin the ds4 rate tightly.
- *Era, shim uncontrolled.* On 2026-09-07/08 only: mlx 23/196 = 11.7%
  [7.9, 17.0] against shimmed ds4 2/181 = 1.1% [0.3, 3.9]. Non-overlapping,
  same days, same machine.

A 60-trial bare-ds4 run in the current era would close both at once.

### The one failure

`script-transform` on mlx-serve, 23.4s, 4 turns. The answers were **correct** —
the model routed the CLI's output through a configured logger, so each value
arrived with a timestamp, logger name and level, and the assertions compare
bare stdout. Clean tree, ruff passing, right answer, wrong stream.

## Traps for whoever runs the next comparison

- **The geometric mean hides the tail.** #191's registered wall statistic
  compressed an 8.08x gap to 2.96x on the one task that mattered. It is the
  right answer to a ratio question and the wrong answer to "how long might this
  take me." Report total wall alongside it.
- **The pass endpoint cannot discriminate.** Both arms clear this task set at
  59-60 of 60 (#79 saturation). One discordant pair, p=1.000. Do not design a
  run whose primary endpoint is pass rate.
- **`n_pairs` is 15, not 60.** Both endpoints pair by task. Sensitivity figures
  computed for n=60 rows do not describe them.
- **The shim asymmetry is deliberate and was checked.** ds4 runs shimmed,
  mlx-serve bare. The shim corrects a tool-call pathology that is ds4's, not
  the model's; step 0 probed mlx-serve for the same pathology and found it
  ABSENT. Had it been present, a bare mlx arm against a shimmed ds4 arm would
  have handed ds4 roughly 23 points.
- **`ds4-mtp-timing` lies about the mlx arm.** It reports no draft counters and
  invites the conclusion that no speculation occurred. mlx-serve emitted 439
  draft lines in a single sweep. See #222.

## Measurements

| date | issue | what | headline |
|---|---|---|---|
| 2026-09-08 | #191 | 4+4 sweeps, 120 rows, stack vs stack | SCREEN PASS; ratio 0.68, total wall tied, mlx tail to 1377s |
| 2026-09-08 | #225 | 4+4 sweeps, 120 rows, mlx-serve 25de4d5 vs 26.9.1 | **null**: pass 59/60 both, ratio 1.00 (0.83-1.20); #191's tail did not reproduce |
| 2026-09-10 | #282 | 2+2 sweeps ×2 runs, 120 rows, stack vs stack, mlx-serve **26.9.2** | mlx took **57% of ds4's per-task time (43% less): 751 s against 1429 s** summed over 15 tasks; wins 13/15; pass ds4 60/60, mlx 59/60; both runs + both orders agree. **Reverses #191's tie.** |

## Open questions

- **#224** — does PLD cause the wall tail? One-variable mlx-serve run,
  `--no-pld` against default. Pre-registered. **This is the next measurement,
  not the paired 3+3 that #191's report recommends.**
- **#39** — ds4 with its MTP sidecar. The mirror image, and the only way to
  give ds4 the draft path mlx-serve already has.
- Whether the tail is a property of the model, the agent loop, or the draft
  path. #191 cannot say; its arms differ by three things at once.

## Evidence

- Run artifacts:
  `hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/mlx-serve/191-ab/`
- Ledger: rows carry `env.harness_head`; #191's are `7932a5f`,
  `harness_dirty == false`.
- Report: `scripts/stack_agent_report_191.py`.
