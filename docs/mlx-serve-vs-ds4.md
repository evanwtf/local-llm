# mlx-serve vs ds4

A living scoreboard for the two stacks we run on the M5 Max. Add a row to
**Measurements** each time one of them is measured; do not rewrite history.

**Last updated:** 2026-09-08, after #191.

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

### The whole difference is one task

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
