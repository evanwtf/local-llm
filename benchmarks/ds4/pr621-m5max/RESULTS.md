# ds4 PR #621 on this M5 Max (#52)

Measured 2026-08-30. Engine: `~/git/ds4-pr621` detached at
`2669a8e9ccc2c97719617c2fe25b3529b5f57fbc` (antirez/ds4#621
`aprojq4-dense-attention`). Metal, `--power` default 100, no `DS4_METAL_*`
overrides. cwd for every `ds4-bench` invocation: the worktree (shaders resolve
relative to cwd).

Weights from Hugging Face `antirez/deepseek-v4-gguf` `refs/pr/22`:

| label | file | bytes |
|---|---|---|
| q4 | `DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ4-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf` | 84,420,584,288, SHA-256 `413cf0a6…c767` |
| q8 | `DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf` | 86,720,111,488 |

Both coherent at `--temp 0`.

## Isolated ctx 2048 (ctx alloc 2177) — the 50 t/s test

`q4-ctx2048.csv`, `q8-ctx2048.csv`. `--ctx-start 2048 --ctx-max 2048 --gen-tokens 128`.

| | gen_steady_tps | gen_tps | prefill_tps |
|---|---:|---:|---:|
| q4 | **51.03** | 50.92 | 773.16 |
| q8 | 44.27 | 44.14 | 794.05 |

Q4 breaks 50 t/s. Q4/Q8 decode = 1.153.

## Sweep, 64k allocation (ctx=65665)

`sweep/q{4,8}-rep{1,2,3}.csv`. `--ctx-start 2048 --ctx-max 65536 --step-incr 2048 --gen-tokens 128`, 3 interleaved reps.

Paired median ratio of `gen_steady_tps` **q4/q8 = 1.146 (+14.6%)**. Q4 > Q8 on **32/32** frontiers (range 1.118–1.220). Prefill is close; do not fold it into decode.

The ctx-2048 *frontier* under this allocation is 45.95 (q4) / 40.37 (q8) — not the isolated 51.03 / 44.27. Different KV plan. Do not pool.

`scripts/decode_ab_report.py` sorts labels alphabetically, so its printed `b/a = 0.872` is **q8/q4**, i.e. q8 is 12.8% slower. The ratio to quote is q4/q8 = 1.146.
