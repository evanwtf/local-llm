# DRAFT — upstream comment for antirez/ds4#952

**DRAFT for the operator to post to antirez/ds4#952. Not a conclusion; one run of four.**

@GiorgioOppo — on measuring at `77a054e` vs `f309990`:

The measurement below is run 1 of 4, `f309990` against its own parent
`8c22d667` (= `f309990^`), one GGUF across two separate worktrees, 8 frontiers
× 3 reps, arm order alternating. It is one datapoint, not a result; runs 2–4
are queued behind a model download and will replace these numbers.

**Raw data:** `benchmarks/ds4/pr952-f309990-run1/` — six CSVs, one per arm per
repetition, every frontier, plus `run-meta.json` with the prompt SHA-256 and
`engines.txt` with both tree revs.

M5 Max 128 GB, Metal 4 tensor API, `--temp 0`. Medians over 3 reps.

### Generation, `gen_steady_tps`

| ctx | `8c22d667` | `f309990` | Δ |
|---:|---:|---:|---:|
| 2048 | 42.04 | 41.71 | −0.8% |
| 4096 | 38.71 | 38.25 | −1.2% |
| 6144 | 38.28 | 38.48 | +0.5% |
| 8192 | 38.32 | 38.36 | +0.1% |
| 10240 | 38.36 | 37.83 | −1.4% |
| 12288 | 37.99 | 37.36 | −1.7% |
| 14336 | 37.67 | 36.82 | −2.3% |
| 16384 | 37.29 | 36.77 | −1.4% |

### Prefill, `prefill_tps`

| ctx | `8c22d667` | `f309990` | Δ |
|---:|---:|---:|---:|
| 2048 | 615.50 | 628.52 | +2.1% |
| 4096 | 481.90 | 496.45 | +3.0% |
| 6144 | 475.69 | 488.35 | +2.7% |
| 8192 | 472.47 | 470.93 | −0.3% |
| 10240 | 465.53 | 455.01 | −2.3% |
| 12288 | 459.49 | 456.10 | −0.7% |
| 14336 | 458.81 | 462.52 | +0.8% |
| 16384 | 456.41 | 450.38 | −1.3% |

**Tree revs:** `8c22d667` (= `f309990^`) and `f309990`.

**File:** `DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf`,
90.88 GiB resident. It is a Q8-projection file, so this does speak to the Q8
path, but it is the mixed Layers37-42 build, not the plain AProjQ8 from the
#91 pair.

**Prompt SHA-256:** `f53e0d80cb2d4492d24ebd63c7000c397b16ae70f9bf09b3763e5d8323ec209f`
(promessi_sposi.txt, 1,329,139 bytes).

**Two limits before you use these.**

I have no `main` arm. Both trees here are on your branch, so this measures
`f309990` against its own parent, not against upstream `main`. If the
comparison you want is branch-versus-main on Metal, we will build a main
worktree and run the same design.

And `prefill_tps` here is **the appended interval at each frontier**, not a
cold large-chunk prefill at fixed ctx (`ds4_bench.c:10`, `:843`:
`prefill_tokens = frontier - previous`). @adamlawi's −12% is the large-chunk
quantity. These are different measurements and should not be read as
disagreeing.

**This is one run.** Three more are queued behind a model download; I will not
draw a conclusion from a single batch, and neither should you.

--deepseek
