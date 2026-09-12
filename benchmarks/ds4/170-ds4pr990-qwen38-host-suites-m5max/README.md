# ds4#990 Qwen3.8-Flash-Next Metal kernels — model-free suites on an M5 Max

**Issue:** [#170](https://github.com/evanwtf/local-llm/issues/170). Parent [#212](https://github.com/evanwtf/local-llm/issues/212), done-when #3.

**What this is.** The four model-free Metal suites that antirez/ds4#990 ships for
its Qwen3.8-Flash-Next port. They exercise the GPU kernels with generated
weights — no model file, no run lock. The PR author validated on an **M5 Pro**
(20-core GPU, 64 GB); this runs them on an **M5 Max** (128 GB) to check the
kernels pass on wider hardware.

**Tree.** `~/git/ds4-pr990` at `9803df46` (ds4#990 head, branch `qwen38-series`,
the current PR head on 2026-09-12). Detached checkout, built earlier the same day.

**Machine.** MacBook Pro M5 Max, 128 GiB, macOS 26, Metal 4 tensor API enabled.
Machine was FREE (no run lock, no resident server). Run 2026-09-12T16:03:48Z.

## Result — all four PASS, on the GPU

| suite | `make` target | ran on | result |
|---|---|---|---|
| Gated DeltaNet | `test-qwen38-gdn` | Metal (M5 Max) | **PASS** |
| QSA sparse attention | `test-qwen38-qsa` | Metal (M5 Max) | **PASS** |
| Hyper-connections / PLE | `test-qwen38-hc` | Metal (M5 Max) | **PASS** |
| PLE hash | `test-qwen38-ple-hash` | host (CPU) | **PASS** |

The three GPU suites logged `Metal device Apple M5 Max, 128.00 GiB RAM` and
`Metal 4 tensor API enabled`, so the kernels executed here — they did not skip.
Drift-patch flags on every GPU run: `hc_stable=on norm_unify=on kv_raw_f32=off
rope_exp2_log2=off math_safe=off tensor_matmul=on`. Full output in `suites.log`.

## What it answers, and what it does not

- **Answers:** ds4#990's Metal kernels for all four Qwen3.8-Flash-Next mechanisms
  pass on an M5 Max, not only the author's M5 Pro.
- **Does not answer:** throughput. These are correctness fixtures. The model run —
  a converted quant, residency, prefill/decode — is the separate, larger part of
  #170 and needs a converted GGUF plus the run lock.

Not posted upstream — whether any of this goes to ds4#990 is the operator's call
(#170, #212).
