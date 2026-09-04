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

**Prompt: `speed-bench/promessi_sposi.txt`, 1,329,139 bytes (1298 KiB),
SHA-256 `f53e0d80…`** — byte-identical in every ds4 tree on this machine.
Recorded here because a prefill figure is not well-posed without it (#140):
@adamlawi measured the same Q4-vs-Q8 prefill question on one box at +2.5% with
a 135 kB prompt and at parity with a 405 kB one, ~2.4 pp apart on identical
binaries. Our parity reading is on a prompt three times longer than his
longer one, which is consistent with his result — and was published without
naming it. Runs since 2026-09-04 stamp the prompt onto every CSV row; these
CSVs predate that and carry an inferred `run-meta.json` instead.

## Isolated ctx 2048 (ctx alloc 2177) — the 50 t/s test

`q4-ctx2048.csv`, `q8-ctx2048.csv`. `--ctx-start 2048 --ctx-max 2048 --gen-tokens 128`.

| | gen_steady_tps | gen_tps | prefill_tps |
|---|---:|---:|---:|
| q4 | **51.03** | 50.92 | 773.16 |
| q8 | 44.27 | 44.14 | 794.05 |

Q4 breaks 50 t/s. Q4/Q8 decode = 1.153.

## Sweep, 64k allocation (ctx=65665)

`sweep/q{4,8}-rep{1,2,3}.csv`. `--ctx-start 2048 --ctx-max 65536 --step-incr 2048 --gen-tokens 128`, 3 interleaved reps.

Paired median ratio of `gen_steady_tps` **q4/q8 = 1.157 (+15.7%)**. Q4 > Q8 on **32/32** frontiers (range 1.122–1.208). Prefill pairs at exactly 1.000 **on this prompt**; do not fold it into decode, and do not compare it against a prefill figure measured on a different prompt (#140).

*Corrected 2026-09-04: this section first read 1.146 (+14.6%), produced by `scripts/decode_ab_report.py` dividing two independent medians — a ratio of medians, not a paired statistic. The paired figures come from these same committed CSVs. The defect is noise, not bias: on #118's dataset it read +20.0% where the paired figure is +16.5%. See docs/changelog.md, 2026-09-04.*

The ctx-2048 *frontier* under this allocation is 45.95 (q4) / 40.37 (q8) — not the isolated 51.03 / 44.27. Different KV plan. Do not pool.

`scripts/decode_ab_report.py` sorts labels alphabetically, so its printed `b/a = 0.864` is **q8/q4**, i.e. q8 is 13.6% slower. The ratio to quote is q4/q8 = 1.157.
