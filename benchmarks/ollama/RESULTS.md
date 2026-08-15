# Qwen3.8-27B on Ollama — M5 Max 128 GiB

Measured 2026-08-15. Ollama 0.32.13 (MLX backend), model `qwen3.8:27b-mlx`,
18 GB on disk, fully GPU-resident.

Machine: MacBook Pro M5 Max, 128 GiB, macOS 26.5. Same machine as every run in
[`../ds4/0731/REPORT.md`](../ds4/0731/REPORT.md), so the numbers sit on the same
hardware baseline.

## Speed

Prompt: the first 40,000 bytes of `promessi_sposi.txt` — the same text the ds4
speed sweeps use — giving an 11,451-token prefill, then 128 generated tokens.

| | prefill | generation |
|---|---|---|
| `qwen3.8:27b-mlx` @ 11,451 tokens | **730.3 t/s** (15.68 s) | **46.3 t/s** |
| ds4 mixed q2/q4 @ 12,288 ctx | 488.1 t/s | 34.4 t/s |

**The prefill figures are not measured the same way.** `ds4-bench` reports
throughput for a 2048-token prefill at a given context size; the Qwen figure is
a single 11,451-token prefill, and longer prefills batch better. Treat prefill
as indicative and generation as directly comparable.

Resident memory is the unambiguous result: **18 GB against 90.9 GiB**.

## Agentic behaviour

One real task through Claude Code: read `ds4_kvstore.c` (~1,300 lines of C) and
explain the prefix cache — data structure, hit detection, eviction and disk
spill. 4 min 02 s end to end.

Every one of ~30 cited line numbers was correct:

| cited | actual |
|---|---|
| `ds4_kvstore_try_load_text` :1215 | 1215 |
| `ds4_kvstore_find_text_prefix` :1190 | 1190 |
| `ds4_kvstore_entry_eviction_score` :532 | 532 |
| `ds4_kvstore_evict` :561 | 561 |
| `kv_cache_incoming_supersedes_continued` :504 | 504 |
| `ds4_kvstore_byte_prefix_match` :667 | 667 |
| `ds4_kvstore_continued_store_target` :741 | 741 |
| `KV_CACHE_MIN_EFFECTIVE_HITS` 0.01 | ds4_kvstore.c:45 |
| 6 h hit half-life | ds4_kvstore.h:13 |

It also described the design intent correctly: eviction runs with the incoming
entry in view, so it discards what the new store makes redundant.

Peak memory grew 18 → 29.7 GiB as context grew (KV, not weights). The heaviest
turn ran 1,996 decode iterations.

## Speculative decoding is roughly a wash

Ollama drafts 2–4 tokens ahead with acceptance 0.70–0.96, which looks like a
win but is not.

The [Qwen 3.8 MTP challenge harness](https://github.com/Layr-Labs/qwen-3.8-mtp-challenge)
scores the shipped depth-2 configuration at **~0.994 against true serial
decode** — a measured 0.6% regression. High acceptance does not imply net gain;
drafting overhead eats it.

This matches the ds4 finding independently: `--dspark` was lossless but 23–44%
*slower*, and the cost was speculation itself, not instrumentation
([`../ds4/0731/REPORT.md`](../ds4/0731/REPORT.md)). Two engines, same result.

That harness measures on the same M5 Max / 128 GiB configuration. Its top
submission reaches 56.7 decode t/s; its serial reference works out to roughly
20.8 t/s. So the headline "172.7% faster" is against a baseline far slower than
stock Ollama — the real headroom over what runs here today is about 20–25%.

## Prefix caching

Ollama has its own prefix cache and it works. On a 48k-token turn:

```
prefix_cache.go:124 msg="cache hit" total=47894 matched=47839 cached=47839 left=55
```

99.9% hit, 55 tokens left to prefill. Same mechanism `--kv-disk-dir` provides in
ds4, and the same reason multi-turn agent work stays affordable.

## Reproducing

```sh
ollama pull qwen3.8:27b-mlx
../../claude-ollama          # Ollama <= 0.32.13 needs the shim; see repo README
```

The speed numbers came from `/api/generate` with `num_ctx` 16384 and
`temperature` 0, reading `prompt_eval_count` / `prompt_eval_duration` and
`eval_count` / `eval_duration` from the response.
