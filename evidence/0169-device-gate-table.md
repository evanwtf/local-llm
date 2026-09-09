# #169 — device-class gate taxonomy

**Source-read only; no measurement. Each citation names its tree and sha: the main body is `ds4.c` / `ds4_metal.m` at `ds4-main` `9ab70534`, the `giorgio/aprojq4-dense-attention` tip at `ds4-pr952` `77a054e1`. The machine is an `Apple M5 Max`.**

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
| 1 | ds4.c:23514 at ds4-main 9ab70534 | `fuse_kv_rope_store` | KV rope FP8 store fuse | decode | reference KV decode path |
| 2 | ds4.c:27927 at ds4-main 9ab70534 | Q2 decode split2_32 | early split-flush schedule, Q2 decode | decode | standard split schedule |
| 3 | ds4.c:28009 at ds4-main 9ab70534 | early-split3 schedule | MXFP4 routed decode, split at layer 3 | decode | standard schedule |
| 4 | ds4.c:28021 at ds4-main 9ab70534 | early-split5 schedule | MXFP4 routed decode, split at layer 5 | decode | standard schedule |
| 5 | ds4.c:28063 at ds4-main 9ab70534 | `ds4_gpu_decode_pipeline_fast_lookup` | MXFP4 routed decode pipeline fast lookup | decode | standard pipeline lookup |
| 6 | ds4.c:28126 at ds4-main 9ab70534 | second split schedule | MXFP4 routed decode, second split | decode | standard schedule |
| 7 | ds4_metal.m:7581 at ds4-main 9ab70534, ds4_metal.m:9434 at ds4-main 9ab70534 | head RMS/RoPE tail pipeline | fused head RMS norm + RoPE tail static PSO | decode attention | separate RMS + RoPE dispatches |
| 8 | ds4_metal.m:27518 at ds4-main 9ab70534 | persistent zero attention mask | persistent zero attention mask, decode | decode attention | materialized zero mask |
| 9 | ds4_metal.m:39947 at ds4-main 9ab70534 | MXFP4 MoE decode NSG1 | MXFP4 MoE decode, NSG=1 encoder | decode MoE | standard NSG |
| 10 | ds4_metal.m:40150 at ds4-main 9ab70534 | MXFP4 MoE decode TG multiple | MXFP4 MoE decode, threadgroup multiple | decode MoE | standard threadgroup |
| 11 | ds4_metal.m:43002 at ds4-main 9ab70534 | MXFP4 MoE MM-ID pair swiglu compact tile | prefill MoE compact tile | **prefill** MoE | padded direct launch |
| 12 | ds4_metal.m:43019 at ds4-main 9ab70534 | MXFP4 MoE MM-ID map scatter | prefill MoE map scatter | **prefill** MoE | padded direct launch |
| 13 | ds4_metal.m:43039 at ds4-main 9ab70534 | MXFP4 MoE MM-ID pair tail simdgroup cull | prefill MoE tail cull | **prefill** MoE | padded direct launch |
| 14 | ds4_metal.m:43051 at ds4-main 9ab70534 | MXFP4 MoE MM-ID down tail simdgroup cull | prefill MoE down tail cull | **prefill** MoE | padded direct launch |
| 15 | ds4_metal.m:43069 at ds4-main 9ab70534 | MXFP4 MoE MM-ID down half LUT | prefill MoE down half LUT | **prefill** MoE | padded direct launch |
| 16 | ds4_metal.m:43174 at ds4-main 9ab70534 | MXFP4 MoE MM-ID pair half scale | prefill MoE pair half scale | **prefill** MoE | padded direct launch |

`ds4_metal.m:33640 at ds4-main 9ab70534` is **not** in this family. `pre_m5_device` there feeds `use_simd_finalize`, whose condition is `(pre_m5_device || name_contains("M5"))` — true on M5 Max. It is M5-admitting, not pre-M5-only. The `&&` at `ds4_metal.m:33645 at ds4-main 9ab70534` is a hardware-capability check (`maxTotalThreadsPerThreadgroup >= 256u`), not a device-class predicate, so it is not part of this taxonomy.

## The M3-only family (8) — M5 Max is excluded

| # | file:line | function / gate | what it guards | phase | fallback |
|---|---|---|---|---|---|
| 17 | ds4_metal.m:2406 at ds4-main 9ab70534 | zero-prefix prefill mask cache | zero-prefix prefill mask cache | **prefill** | no cache |
| 18 | ds4_metal.m:21962 at ds4-main 9ab70534 | HC RMS scale project | HC RMS scale project | decode | reference HC RMS scale |
| 19 | ds4_metal.m:23366 at ds4-main 9ab70534 | compressor APE add fuse | compressor APE add fuse | **prefill** | separate APE add |
| 20 | ds4_metal.m:24125 at ds4-main 9ab70534 | compressor ratio4 pack fusion | compressor ratio4 pack fusion | decode | separate pack |
| 21 | ds4_metal.m:24171 at ds4-main 9ab70534 | compressor ratio4 direct pool | compressor ratio4 direct pool | decode | legacy GGML reduction graph |
| 22 | ds4_metal.m:29177 at ds4-main 9ab70534 | shared KV pad | shared KV pad | **prefill** | per-row KV pad |
| 23 | ds4_metal.m:33918 at ds4-main 9ab70534 | router weights batch fusion | router weights batch fusion | **prefill** | per-token router weights |
| 24 | ds4_metal.m:44974 at ds4-main 9ab70534 | output HC weights4 | output HC weights4 | decode | standard output HC weights |

## The M3-Ultra-only family (2) — M5 Max is excluded

| # | file:line | function / gate | what it guards | phase | fallback |
|---|---|---|---|---|---|
| 25 | ds4_metal.m:45907 at ds4-main 9ab70534 | GLM-5.3 BF16 NSG decode | GLM-5.3 BF16 NSG decode | decode | standard GLM-5.3 decode |
| 26 | ds4_metal.m:46001 at ds4-main 9ab70534 | GLM-5.3 BF16 QKV | GLM-5.3 BF16 QKV | decode | standard GLM-5.3 QKV |

## The prefill list (10)

**pre-M5-only (6):** the MXFP4 MoE MM-ID prefill kernels — ds4_metal.m:43002 at ds4-main 9ab70534, ds4_metal.m:43019 at ds4-main 9ab70534, ds4_metal.m:43039 at ds4-main 9ab70534, ds4_metal.m:43051 at ds4-main 9ab70534, ds4_metal.m:43069 at ds4-main 9ab70534, ds4_metal.m:43174 at ds4-main 9ab70534.

**M3-only (4):** the zero-prefix prefill mask cache (ds4_metal.m:2406 at ds4-main 9ab70534), the compressor APE add fuse (ds4_metal.m:23366 at ds4-main 9ab70534), the shared KV pad (ds4_metal.m:29177 at ds4-main 9ab70534), the router weights batch fusion (ds4_metal.m:33918 at ds4-main 9ab70534).

## The M5-admitting family (7) — M5 Max takes these

The `pre_m5 || is_m5` pattern admits M5 at ds4.c:22769 at ds4-main 9ab70534 (attn inverse RoPE fuse), ds4.c:22980 at ds4-main 9ab70534 / ds4.c:24612 at ds4-main 9ab70534 (HC norm mix fuse), ds4.c:23356 at ds4-main 9ab70534 (QKV norm KV store fuse), ds4.c:23587 at ds4-main 9ab70534 (compressor quad store), ds4.c:24786 at ds4-main 9ab70534 (router shared fuse); ds4_metal.m:23831 at ds4-main 9ab70534 (compressor exact reduction fusion), ds4_metal.m:24138 at ds4-main 9ab70534 (compressor ratio4 decode pack). The `ported_m5_decode_feature_enabled` helper (`pre_m5 || is_m5`) admits M5 at ds4.c:22834 at ds4-main 9ab70534, ds4.c:22985 at ds4-main 9ab70534, ds4.c:23171 at ds4-main 9ab70534, ds4.c:23229 at ds4-main 9ab70534, ds4.c:23691 at ds4-main 9ab70534, ds4.c:24617 at ds4-main 9ab70534 and ds4_metal.m:24304 at ds4-main 9ab70534, ds4_metal.m:29129 at ds4-main 9ab70534. The `name_contains("M3") || name_contains("M5")` pattern admits M5 at ds4_metal.m:3806 at ds4-main 9ab70534, ds4_metal.m:23106 at ds4-main 9ab70534, ds4_metal.m:27293 at ds4-main 9ab70534, ds4_metal.m:29146 at ds4-main 9ab70534, ds4_metal.m:21110 at ds4-main 9ab70534, ds4_metal.m:6135 at ds4-main 9ab70534, plus partial admissions at ds4_metal.m:6118 at ds4-main 9ab70534, ds4_metal.m:33647 at ds4-main 9ab70534, ds4_metal.m:33656 at ds4-main 9ab70534, ds4_metal.m:33661 at ds4-main 9ab70534, ds4_metal.m:33668 at ds4-main 9ab70534, ds4_metal.m:42921 at ds4-main 9ab70534.

## The M5-only family (15) — M5 Max takes these, pre-M5 does not

ds4.c:22743 at ds4-main 9ab70534 (HC expand producer fuse), ds4.c:24792 at ds4-main 9ab70534 / ds4.c:24859 at ds4-main 9ab70534 (router project select fuse), ds4.c:25750 at ds4-main 9ab70534 (TP parallel FFN), ds4.c:31678 at ds4-main 9ab70534 (TP batched MoE), ds4.c:50613 at ds4-main 9ab70534 (GLM attn head-split), ds4.c:54327 at ds4-main 9ab70534 (DSPark seed batch); ds4_metal.m:29140 at ds4-main 9ab70534 (M5 persistent zero mask), ds4_metal.m:40213 at ds4-main 9ab70534 (TP MXFP4 static), ds4_metal.m:40261 at ds4-main 9ab70534 (IQ2 pair pack2), ds4_metal.m:42935 at ds4-main 9ab70534 (TP MXFP4 static batch), ds4_metal.m:44465 at ds4-main 9ab70534 (HC norm mix cluster2), ds4_metal.m:45773 at ds4-main 9ab70534 (Q8 HC vec), ds4_metal.m:1613 at ds4-main 9ab70534 (M5 private scratch), ds4_metal.m:2875 at ds4-main 9ab70534 (Metal4 neural accelerator hint).

## What the `giorgio/aprojq4-dense-attention` tip adds on top

The tip (`77a054e1`, `20d5dff6` visible) adds 6 more pre-M5-only gates, all in prefill — the Q4_K dense attention-projection family that PR #952 is named for. These are the subject of issue #169. Each citation names the tip's `ds4_metal.m` and its sha:

| file:line at sha | function | what it is | phase |
|---|---|---|---|
| ds4_metal.m:14410 at ds4-pr952 77a054e1 | `ds4_gpu_stream_expert_prefill_nocache_auto` | SSD-streaming expert prefill (also gated on `g_ssd_streaming_mode`) | prefill MoE |
| ds4_metal.m:24094 at ds4-pr952 77a054e1 | `ds4_gpu_try_q4_K_prefill_pair_f16_rhs` | the Q4_K dense prefill pair path | prefill attention |
| ds4_metal.m:27760 at ds4-pr952 77a054e1 | `ds4_gpu_prepare_q4_attn_q_b_transient_f16` | transient-F16 `attn_q_b` scratch | prefill attention |
| ds4_metal.m:27946 at ds4-pr952 77a054e1 | `ds4_gpu_prepare_q4_attn_q_b_f16_sidecars` | F16 sidecars | prefill attention |
| ds4_metal.m:28270 at ds4-pr952 77a054e1 | `ds4_gpu_attn_q_b_f16_head_rms_rope_tail_tensor_impl` | fused head RMS + RoPE tail | prefill attention |
| ds4_metal.m:28649 at ds4-pr952 77a054e1 | `ds4_gpu_attn_q_b_transient_f16_head_rms_rope_tail_tensor` | same, transient variant | prefill attention |

With the tip, the pre-M5-only family is **22 distinct** (16 + 6), **12 in prefill** (6 + 6).

## What this does not say

A gate tells you a path did not dispatch on this machine. It does not tell you the path would have been faster here. The gated kernels may be pre-M5-only because they lose on M5, or because nobody has ported them yet. That is a question for upstream, not an inference from the gate.

--deepseek
