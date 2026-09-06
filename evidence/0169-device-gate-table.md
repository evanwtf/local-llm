# #169 — device-class gate taxonomy

**Source-read only; no measurement. Line numbers are `ds4_metal.m` / `ds4.c` at upstream `main` `9ab70534`. The machine is an `Apple M5 Max`.**

Every device-class gate in the engine, what it guards, and whether an M5 Max takes it. A gate is a call to one of the five device predicates:

- `ds4_gpu_device_is_pre_m5_apple_silicon()` — true for `Apple M1`..`Apple M4`. **False on M5 Max.**
- `ds4_gpu_device_is_m5_apple_silicon()` — true for `Apple M5`. **True on M5 Max.**
- `ds4_gpu_device_name_contains(needle)` — substring match on the device name.
- `ds4_gpu_ported_m5_decode_feature_enabled(pre_m5_env, m5_env)` — returns `pre_m5 || is_m5`. **True on M5 Max.**
- `ds4_gpu_device_is_m1_apple_silicon()` — 0 call sites (definition only).

## The headline

**The M5 Max is excluded from two distinct device-gated families, not one.** The pre-M5 family (`!pre_m5`) and the M3 family (`name_contains("M3")`, including `"M3 Ultra"`) are different predicates with different membership: an M4 takes every pre-M5 gate and none of the M3 ones. Collapsing them loses the distinction the table exists to draw.

| family | predicate | distinct gates | M5 Max takes them |
|---|---|---|---|
| pre-M5-only | `!ds4_gpu_device_is_pre_m5_apple_silicon()` | **16** | no |
| M3-only | `ds4_gpu_device_name_contains("M3")` | **8** | no |
| M3-Ultra-only | `ds4_gpu_device_name_contains("M3 Ultra")` | **2** | no |
| M5-admitting | `pre_m5 \|\| is_m5` | **7** | yes |
| M5-only | `ds4_gpu_device_is_m5_apple_silicon()` alone | **15** | yes |

Total excluded: **26** (16 pre-M5 + 10 M3-family). Of those, **10 sit in prefill** (6 pre-M5 + 4 M3).

## The pre-M5-only family (16) — M5 Max is excluded

| # | file:line | function / gate | what it guards | phase | fallback |
|---|---|---|---|---|---|
| 1 | ds4.c:23514 | `fuse_kv_rope_store` | KV rope FP8 store fuse | decode | reference KV decode path |
| 2 | ds4.c:27927 | Q2 decode split2_32 | early split-flush schedule, Q2 decode | decode | standard split schedule |
| 3 | ds4.c:28009 | early-split3 schedule | MXFP4 routed decode, split at layer 3 | decode | standard schedule |
| 4 | ds4.c:28021 | early-split5 schedule | MXFP4 routed decode, split at layer 5 | decode | standard schedule |
| 5 | ds4.c:28063 | `ds4_gpu_decode_pipeline_fast_lookup` | MXFP4 routed decode pipeline fast lookup | decode | standard pipeline lookup |
| 6 | ds4.c:28126 | second split schedule | MXFP4 routed decode, second split | decode | standard schedule |
| 7 | ds4_metal.m:7581,9434 | head RMS/RoPE tail pipeline | fused head RMS norm + RoPE tail static PSO | decode attention | separate RMS + RoPE dispatches |
| 8 | ds4_metal.m:27518 | persistent zero attention mask | persistent zero attention mask, decode | decode attention | materialized zero mask |
| 9 | ds4_metal.m:39947 | MXFP4 MoE decode NSG1 | MXFP4 MoE decode, NSG=1 encoder | decode MoE | standard NSG |
| 10 | ds4_metal.m:40150 | MXFP4 MoE decode TG multiple | MXFP4 MoE decode, threadgroup multiple | decode MoE | standard threadgroup |
| 11 | ds4_metal.m:43002 | MXFP4 MoE MM-ID pair swiglu compact tile | prefill MoE compact tile | **prefill** MoE | padded direct launch |
| 12 | ds4_metal.m:43019 | MXFP4 MoE MM-ID map scatter | prefill MoE map scatter | **prefill** MoE | padded direct launch |
| 13 | ds4_metal.m:43039 | MXFP4 MoE MM-ID pair tail simdgroup cull | prefill MoE tail cull | **prefill** MoE | padded direct launch |
| 14 | ds4_metal.m:43051 | MXFP4 MoE MM-ID down tail simdgroup cull | prefill MoE down tail cull | **prefill** MoE | padded direct launch |
| 15 | ds4_metal.m:43069 | MXFP4 MoE MM-ID down half LUT | prefill MoE down half LUT | **prefill** MoE | padded direct launch |
| 16 | ds4_metal.m:43174 | MXFP4 MoE MM-ID pair half scale | prefill MoE pair half scale | **prefill** MoE | padded direct launch |

`ds4_metal.m:33640` is **not** in this family. `pre_m5_device` there feeds `use_simd_finalize`, whose condition is `(pre_m5_device || name_contains("M5"))` — true on M5 Max. It is M5-admitting, not pre-M5-only.

## The M3-only family (8) — M5 Max is excluded

| # | file:line | function / gate | what it guards | phase | fallback |
|---|---|---|---|---|---|
| 17 | ds4_metal.m:2406 | zero-prefix prefill mask cache | zero-prefix prefill mask cache | **prefill** | no cache |
| 18 | ds4_metal.m:21962 | HC RMS scale project | HC RMS scale project | decode | reference HC RMS scale |
| 19 | ds4_metal.m:23366 | compressor APE add fuse | compressor APE add fuse | **prefill** | separate APE add |
| 20 | ds4_metal.m:24125 | compressor ratio4 pack fusion | compressor ratio4 pack fusion | decode | separate pack |
| 21 | ds4_metal.m:24171 | compressor ratio4 direct pool | compressor ratio4 direct pool | decode | legacy GGML reduction graph |
| 22 | ds4_metal.m:29177 | shared KV pad | shared KV pad | **prefill** | per-row KV pad |
| 23 | ds4_metal.m:33918 | router weights batch fusion | router weights batch fusion | **prefill** | per-token router weights |
| 24 | ds4_metal.m:44974 | output HC weights4 | output HC weights4 | decode | standard output HC weights |

## The M3-Ultra-only family (2) — M5 Max is excluded

| # | file:line | function / gate | what it guards | phase | fallback |
|---|---|---|---|---|---|
| 25 | ds4_metal.m:45907 | GLM-5.3 BF16 NSG decode | GLM-5.3 BF16 NSG decode | decode | standard GLM-5.3 decode |
| 26 | ds4_metal.m:46001 | GLM-5.3 BF16 QKV | GLM-5.3 BF16 QKV | decode | standard GLM-5.3 QKV |

## The prefill list (10)

**pre-M5-only (6):** the MXFP4 MoE MM-ID prefill kernels — ds4_metal.m 43002, 43019, 43039, 43051, 43069, 43174.

**M3-only (4):** the zero-prefix prefill mask cache (2406), the compressor APE add fuse (23366), the shared KV pad (29177), the router weights batch fusion (33918).

## The M5-admitting family (7) — M5 Max takes these

The `pre_m5 || is_m5` pattern admits M5 at ds4.c 22769 (attn inverse RoPE fuse), 22980/24612 (HC norm mix fuse), 23356 (QKV norm KV store fuse), 23587 (compressor quad store), 24786 (router shared fuse); ds4_metal.m 23831 (compressor exact reduction fusion), 24138 (compressor ratio4 decode pack). The `ported_m5_decode_feature_enabled` helper (`pre_m5 || is_m5`) admits M5 at ds4.c 22834, 22985, 23171, 23229, 23691, 24617 and ds4_metal.m 24304, 29129. The `name_contains("M3") || name_contains("M5")` pattern admits M5 at ds4_metal.m 3806, 23106, 27293, 29146, 21110, 6135, plus partial admissions at 6118, 33647, 33656, 33661, 33668, 42921.

## The M5-only family (15) — M5 Max takes these, pre-M5 does not

ds4.c 22743 (HC expand producer fuse), 24792/24859 (router project select fuse), 25750 (TP parallel FFN), 31678 (TP batched MoE), 50613 (GLM attn head-split), 54327 (DSPark seed batch); ds4_metal.m 29140 (M5 persistent zero mask), 40213 (TP MXFP4 static), 40261 (IQ2 pair pack2), 42935 (TP MXFP4 static batch), 44465 (HC norm mix cluster2), 45773 (Q8 HC vec), 1613 (M5 private scratch), 2875 (Metal4 neural accelerator hint).

## What the `giorgio/aprojq4-dense-attention` tip adds on top

The tip (`77a054e1`, `20d5dff6` visible) adds 6 more pre-M5-only gates, all in prefill — the Q4_K dense attention-projection family that PR #952 is named for. These are the subject of issue #169. Line numbers are the tip's `ds4_metal.m`:

| line | function | what it is | phase |
|---|---|---|---|
| 14410 | `ds4_gpu_stream_expert_prefill_nocache_auto` | SSD-streaming expert prefill (also gated on `g_ssd_streaming_mode`) | prefill MoE |
| 24094 | `ds4_gpu_try_q4_K_prefill_pair_f16_rhs` | the Q4_K dense prefill pair path | prefill attention |
| 27760 | `ds4_gpu_prepare_q4_attn_q_b_transient_f16` | transient-F16 `attn_q_b` scratch | prefill attention |
| 27946 | `ds4_gpu_prepare_q4_attn_q_b_f16_sidecars` | F16 sidecars | prefill attention |
| 28270 | `ds4_gpu_attn_q_b_f16_head_rms_rope_tail_tensor_impl` | fused head RMS + RoPE tail | prefill attention |
| 28649 | `ds4_gpu_attn_q_b_transient_f16_head_rms_rope_tail_tensor` | same, transient variant | prefill attention |

With the tip, the pre-M5-only family is **22 distinct** (16 + 6), **12 in prefill** (6 + 6).

## What this does not say

A gate tells you a path did not dispatch on this machine. It does not tell you the path would have been faster here. The gated kernels may be pre-M5-only because they lose on M5, or because nobody has ported them yet. That is a question for upstream, not an inference from the gate.

--deepseek
