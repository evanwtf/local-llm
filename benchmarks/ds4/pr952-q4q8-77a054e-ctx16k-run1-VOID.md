# Wrong sweep for the question — do not compare these to 1.155

These two directories are a valid `q4`/`q8` A/B at ds4#952 head `77a054e`, but
they sweep **8 frontiers, ctx 2048-16384**. The published figures they were
launched to re-test — decode `1.155` at `2669a8e`, and `1.147` at `6a20b13` —
come from a **32-frontier sweep, ctx 2048-65536**
(`benchmarks/ds4/pr621-recheck-run1/`, 32 rows per CSV).

An 8-frontier median and a 32-frontier median are not the same statistic. The
q4/q8 ratio varies with context length, and the 24 frontiers above 16384 are
exactly the region where the KV cache dominates and the two files diverge most.
Quoting one against the other would read as "the ratio moved" when the sweep
moved.

My error, caught after two runs: I launched `decode_ab.sh` on its defaults
(`CTX_START=2048 CTX_MAX=16384 STEP=2048`) without checking what the baseline
had used. Relaunched with `CTX_MAX=65536` as
`benchmarks/ds4/pr952-q4q8-77a054e-run1..4/`.

Run 1 here is complete; run 2 is a partial batch, stopped mid-arm. Neither is
void for a *measurement* reason — no confound, no asymmetric load — so the
numbers are real. They answer a different and narrower question than the one
asked, and nothing published should pool them with the 32-frontier runs.

## What these would have said, and why that is the danger

Run 1 here reads **q4/q8 1.152**. The published figures it was launched against
are **1.155** (`2669a8e`) and **1.147** (`6a20b13`). It would have read as a
clean confirmation: *the finding is stable at head.*

The four-run 32-frontier answer is **1.130**.

The reason is in the per-frontier table: q8/q4 rises monotonically from 0.858 at
ctx 2048 to 0.897 at 65536, so q4's advantage decays with context — +16.6% at
the bottom of the sweep, +11.5% at the top. An 8-frontier sweep stopping at
16384 samples only the steep low-ctx region and reports the largest ratio in the
range.

**The wrong sweep did not produce an obviously wrong number. It produced the
expected one.** Nothing in the output would have prompted anyone to check the
design, and the number would have gone upstream. This is the same shape as the
voided run 2 of the `f309990` batch: a confound that agrees with the hypothesis
is more dangerous than one that argues with it, because only the second gets
investigated.
