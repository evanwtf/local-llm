# #267: the automatic Q4 default path on M5 Max, reviewed per @GiorgioOppo

A raw `ds4-bench` admission A/B, not a harness run — no `run-meta.json` or
`stacks.txt`. It answers one question: do the two Q4 Metal controls that are
on the automatic/default path on M5 (the only two in `Q4_CONTROLS.md` with no
pre-M5 gate) change any number on this hardware?

## What ran

- Tree: `ds4-pr952-head` at `7a5002de`.
- Model: `DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ4-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf`
  (dense attention requantized to Q4, "AProjQ4").
- Prompt: `promessi_sposi.txt`, 1,329,139 bytes,
  sha256 `f53e0d80cb2d4492d24ebd63c7000c397b16ae70f9bf09b3763e5d8323ec209f`.
- `ds4-bench --metal --ctx-start 2048 --ctx-max 16384 --step-incr 2048 --gen-tokens 128`.
- Two arms:
  - **default** — both controls unset (the automatic path).
  - **rollback** — `DS4_METAL_DISABLE_Q4_ATTN_OUT_HC_FUSE=1` and
    `DS4_METAL_DISABLE_Q4_DENSE_PAIR=1`.
- 3 reps, arm order alternated per rep (default first on odd reps).

## Result

No effect attributable to either control, prefill or decode.

- **Prefill**: mean delta (rollback − default) across the 8 ctx points is
  −0.8% (range −5.9%..+1.9%), no consistent direction.
- **Decode**: the raw mean is +2.2% for rollback, but it is thermal, not the
  knob. Each rep is slower than the last as the laptop heats over the ~5-minute
  run (default decode falls 53.2→46.3 tok/s in rep 1, 48.1→42.0 in rep 3). The
  +2.2% comes entirely from rep 2, where the *default* arm ran in the hotter
  second slot. In the two reps where rollback ran second, the arms are within
  0.6% at every ctx. The difference tracks arm order.

With every other Q4 rollback in `Q4_CONTROLS.md` gated to pre-M5, the automatic
Q4 path is effectively the only path this hardware takes, and it is
performance-neutral to these two switches here.

## Files

`{default,rollback}-rep{1,2,3}.csv` — 8 ctx points each. `runner.log` — the
per-arm start/finish timestamps that establish the run order above.
