# DRAFT — upstream comment for antirez/ds4#952

**DRAFT for the operator to post to antirez/ds4#952. Four runs; run 2 void.**

@GiorgioOppo — on measuring at `77a054e` vs `f309990`:

Four runs of `f309990` against its own parent `8c22d667` (= `f309990^`), one
GGUF across two separate worktrees, 8 frontiers × 3 reps, arm order alternating.
Run 2 was voided: a test suite landed on one arm mid-run, which is asymmetric
load inside a paired comparison, so it was discarded and run 5 replaced it.
Runs 1, 3, 4, 5 stand.

**Raw data:** https://github.com/evanwtf/local-llm/tree/main/benchmarks/ds4 —
the four run directories are `pr952-f309990-run1`, `pr952-f309990-run3`,
`pr952-f309990-run4` and `pr952-f309990-run5`. Six CSVs per run, one per arm
per repetition, every frontier, plus `run-meta.json` with the prompt SHA-256
and `engines.txt` with both tree revs. `pr952-f309990-run2` is present and
void; `pr952-f309990-run2-VOID.md` says why.

M5 Max 128 GB, Metal 4 tensor API, `--temp 0`. Every ratio is paired **within
one repetition** — arm B's rep-2 rate over arm A's rep-2 rate — and then
medianed, so repetition-to-repetition drift does not re-enter as noise.

### Headline, `f309990` / `f309990-prev` (above 1.000 means `f309990` is faster)

| metric | median | per run | range |
|---:|---:|---:|---:|
| decode (`gen_steady_tps`) | 1.001 (+0.1%) | 0.982, 0.996, 1.012, 1.007 | 2.9 pp |
| prefill (`prefill_tps`) | 0.991 (−0.9%) | 0.986, 0.993, 1.007, 0.988 | 2.1 pp |

### Per frontier, `f309990` / `f309990-prev`, median over 4 runs

| ctx | decode | range | prefill | range |
|---:|---:|---:|---:|---:|
| 2048 | 0.990 | 0.982 – 0.999 | 0.996 | 0.975 – 1.003 |
| 4096 | 1.002 | 0.987 – 1.015 | 0.983 | 0.978 – 1.006 |
| 6144 | 0.992 | 0.979 – 1.011 | 1.004 | 0.999 – 1.008 |
| 8192 | 1.008 | 0.975 – 1.010 | 1.001 | 0.988 – 1.008 |
| 10240 | 0.993 | 0.966 – 1.025 | 0.983 | 0.973 – 1.015 |
| 12288 | 1.004 | 0.984 – 1.014 | 0.982 | 0.972 – 1.014 |
| 14336 | 1.006 | 0.979 – 1.020 | 1.005 | 0.962 – 1.012 |
| 16384 | 1.010 | 0.990 – 1.017 | 0.992 | 0.976 – 1.008 |

### The noise floor governs the conclusion

The typical within-run repeat spread at one frontier is **3.8 pp**, and the
between-run spread is 2.9 pp decode / 2.1 pp prefill — the runs agree as well
as the reps do. Every per-frontier range above spans 1.000. So the honest
claim is **no effect resolvable above about ±3 pp in either direction**, not
"prefill is 0.9% slower". The instrument cannot see 0.9%.

The two tables are built differently and will not agree to the last digit, so
do not read a discrepancy into it. The headline is the median across the four
runs of each run's own median across frontiers. The per-frontier table is, for
each frontier, the median across the four runs. Both are reported because they
answer different questions: whether the change moved the rate at all, and
whether it moved it everywhere.

**Tree revs:** `8c22d667` (= `f309990^`) and `f309990`.

**File:** `DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf`,
90.88 GiB resident. It is a Q8-projection file, so this does speak to the Q8
path, but it is the mixed Layers37-42 build, not the plain AProjQ8 from the
#91 pair.

**Prompt SHA-256:** `f53e0d80cb2d4492d24ebd63c7000c397b16ae70f9bf09b3763e5d8323ec209f`
(promessi_sposi.txt, 1,329,139 bytes).

**Two limits before you use these.**

I have no `main` arm in these numbers. Both trees are on your branch, so this
measures `f309990` against its own parent, not against upstream `main`. A
`main` worktree is built (`ds4-main-b` at `9ab7053`) and queued; the
branch-versus-main comparison on Metal is the next run.

And `prefill_tps` here is **the appended interval at each frontier**, not a
cold large-chunk prefill at fixed ctx (`ds4_bench.c:10`, `:843`:
`prefill_tokens = frontier - previous`). @adamlawi's −12% is the large-chunk
quantity. These are different measurements and should not be read as
disagreeing.

--deepseek
