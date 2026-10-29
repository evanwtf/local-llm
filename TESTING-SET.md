# The testing set

**What this project measures, and on what.** Four axes (hardware, client,
engine, model) plus the task set that runs across them. Anything not listed
here is not part of the matrix. A retired backend stays documented because
rows in a `results.jsonl` still reference it. Row counts and results are in
[`docs/results.md`](docs/results.md), generated from the ledgers; this file
does not repeat them.

---

## Hardware

The registry is [`hardware/MACHINES.md`](hardware/MACHINES.md), generated from
`scripts/machines.py`. The active machines are the **M5 Max** and the
**two-Spark cluster**.

| machine | `tier` | status | ledger |
|---|---|---|---|
| MacBook Pro, Apple **M5 Max, 128 GB** unified, macOS 26 | *(none)* | active; the primary coding-agent machine | [`hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/`](hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/RECOMMENDATIONS.md) |
| **Two DGX Sparks** (2x GB10, 2x 128 GiB, 200 Gb/s RoCE), served to a remote OpenCode client | `gb10-spark-x2` | active | [`hardware/Cortex-X925-128GB-GB10-x2/`](hardware/Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md) |
| **One DGX Spark** (GB10, 128 GiB unified, CUDA `sm_121`) | `gb10-spark` | testing stopped 2026-10-23; rows kept | [`hardware/Cortex-X925-128GB-GB10/`](hardware/Cortex-X925-128GB-GB10/RECOMMENDATIONS.md) |
| Ryzen 9 7900X + **RTX 3080 Ti, 12 GiB** VRAM, 30 GiB RAM | `desktop-3080ti` | secondary, not always on | [`hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/`](hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/RESULTS.md) |

**Each machine is its own baseline; rows are never pooled across them.** The
harness refuses to append to a `results.jsonl` that already holds rows from
other hardware. The first Linux run appended 13 rows beside the Mac's and
nothing objected until a later `git pull` refused to merge over them.

**On the M5 Max, the Metal ceiling is required.** `iogpu.wired_limit_mb =
114688` (112 GiB), set at boot by a LaunchDaemon. It is a cap, not a
reservation. Stock gives ds4 a 75.5 GiB budget against an 89.87 GiB GLM-5.3,
which is a refusal.

**Off the Mac: no MLX, no Swift oracle, no confinement.** MLX ships no Linux
runtime (#293), so the MLX backends are Mac-only. The 5 Swift excisions are
skipped, so a Linux machine's full matrix is 10 tasks. `sandbox-exec` is
macOS-only, so `workspace_escapes` is unenforced on Linux.

**The Ryzen desktop's backends** are `dtmistralnemo`, `dtgemma412b`,
`dtornith15`, `dtornith159b`, `dtqwen359b`, `dtqwen359bq8`, `dtgemma4e4b`,
`dtbonsai27b`, `dtternarybonsai27b`, `dtbonsai27bllamacpp`,
`dtternarybonsai227b`, `dtternarybonsai2ptq127b` and `dtsparkx254b`. The five
llama.cpp-served ones are not engine-comparable to the Ollama ones (#192,
#269), and run under three different builds: the v1 bonsai pair on PrismML
fork `d8f26ee`, the Ternary Bonsai 2 pair on PrismML fork `1a07bfa` (#479),
and `dtsparkx254b` on upstream `91f6a6c`, the only one that serves `spark2_5`
(#278). Its 30 GiB of RAM is what makes the MoE CPU-offload path real (#20).

---

## Client

**OpenCode, and nothing else** — `opencode run --dir "$PWD"`.

The client axis is measured and closed. One server, one session, same task:
**Aider 11.1 s, OpenCode 39.5 s, Claude Code 189.6 s**, with prompt sizes of
737 / 11,721 / 85,413 tokens. The cause is prompt size, paid on every turn.
Aider is far faster and passes 22/34 inside a repository against OpenCode's
91/93, so "use the fastest client" would be wrong advice.

OpenCode is fixed as the answer by the project's premise: a local setup exists
to keep working when a vendor does not, so the agent has to be open too.

| also wired | run only when |
|---|---|
| Claude Code | the question is about Claude Code itself |
| Codex | the question is about Codex itself |
| Aider | a deliberate low-prompt reference point |
| hosted **Opus 5** | establishing a new task class's ceiling |

**`--dir` is not optional.** `opencode run` attaches to a persistent server
holding its own working directory and ignores the caller's `cwd`. Every
OpenCode row before **2026-08-31 21:47 EDT** measures that bug, not the client.

---

## Engines

| machine | engines |
|---|---|
| M5 Max | **llama.cpp** (Metal), **ds4** (DwarfStar), **Ollama**, **mlx-serve**, **Sushi** (an mlx-serve fork, #749), MTPLX |
| Two Sparks | **vLLM** (with ExLlamaV3 for EXL3), **TensorFold** (Python, native and Zig engines), **SGLang**, Atlas (SparkGLM) |
| One Spark | llama.cpp CUDA, ds4 CUDA (`sm_121a`), Ollama, vLLM, SGLang, TensorFold, TandemLLM |
| Ryzen | Ollama, llama.cpp (PrismML forks and upstream) |

Each row pins its engine build: `llamacpp_head` + `build_info` for llama.cpp
(a source build, so a commit is the only identifier), `ds4_head` +
`server_argv` for ds4, the version string + `digest_<backend>` for Ollama, and
`engine_version` elsewhere. Every row also stamps `harness_head`.

Ollama is in the set on **friction, not speed**: it is the 31 GB entry point a
newcomer follows on the M5 Max.

**Retired: LM Studio** (2026-09-01). Its runtime is llama.cpp underneath, so on
the same GGUF it can only add a layer — and it does: identical UD-Q3_K_XL
weights and client, **90 s median against 122 s**, correctness identical. Kept
in `tasks.toml` as `retired`, not deleted: 27 rows reference it and they are
unreadable without the sampler and context length that block records. Also
retired that day: `ornith:35b`, superseded by `ornith-1.5:35b` (`ornith15`).

**`b10729` is preserved** at `~/llamacpp-builds/b10729/bin` on the M5 Max. It
produced the early published llama.cpp numbers, and a `git pull` plus in-place
rebuild would have destroyed it.

---

## Models

Every live backend in `tasks.toml`, by machine. A `retired` note in
`tasks.toml` says why a backend left the default matrix.

### M5 Max

| backend | model | engine | size |
|---|---|---|---|
| `qwen38fnq3` | Qwen3.8-Flash-Next `UD-Q3_K_XL` | llama.cpp | 83.8 GiB |
| `ds4` | DeepSeek-V4-Flash 0731 | ds4 (OpenAI wire) | 90.9 GiB |
| `glm53ds4` | GLM-5.3-Flash | ds4 | — |
| `qwen36coding` | `qwen3.6:27b-coding-mxfp8` | Ollama | 31 GB |
| `ornith15` | `ornith-1.5:35b` | Ollama | 22 GB |
| `gemma4` | `gemma4:31b-mxfp8` | Ollama | 32 GB |
| `devstralsmall2` | `devstral-small-2:24b` (Devstral Small 2, Mistral 24B dense, Q4_K_M) — #3 | Ollama | 15 GB |
| `ds4anthropic` | DeepSeek-V4-Flash 0731 | ds4 (Anthropic wire) | 90.9 GiB |
| `qwen38fnds4shim` | Qwen3.8-Flash-Next DS4-Q4 fast-pack, MTP off | ds4 (via tool shim) | 113 GB |
| `qwen38fnds4mtp7shim` | the same fast-pack, MTP `--mtp-draft 7` | ds4 (via tool shim) | 113 GB |
| `qwen38fnds4mtp7greedy` | the same fast-pack, MTP `--mtp-draft 7`, **temperature pinned to 0** | ds4 (via a second tool shim on :8102) | 113 GB |
| `qwen38fnds4greedy` | the same fast-pack, MTP off, **temperature pinned to 0** | ds4 (via a second tool shim on :8102) | 113 GB |
| `qwen38fnds4kimat` | Q4_K **imatrix** rebuild of the same model, MTP off | ds4, ivanfioravanti fork (via tool shim) | 105 GB |
| `qwen38fnds4q4exp` · `qwen38fnds4q4exppr5` | the same `-q4` fast-pack **converted to the `qwen4exp` schema** (`--ple-external`), MTP off | ds4 `6c1e836` (ds4-metal-228, via tool shim) | 107 GB |
| `qwen38fnds4mtpauto` | the same `-q4` qwen4exp fast-pack with **embedded MTP (nextn), MTP-on-by-pack** — the #212 pack-level MTP A/B arm against `qwen38fnds4q4exp` | ds4 `ccea768` (ds4-metal, via tool shim) | 107 GB |
| `qwen38fnds4main` | upstream's own `qwen38-q4k` GGUF (Q4_K imatrix gate/up, MXFP4 down; n-grams inside, no `--ple`), MTP off | ds4 upstream main `8db1d1d1` (via tool shim) | 177 GB |
| `qwen38fnmlxserve` | the same model as MLX mixed 4/8-bit weights | mlx-serve 26.9.1 (no shim) | 101 GB |
| `qwen38fnmlxserve-git` | the same model as MLX mixed 4/8-bit weights | mlx-serve git main+PR383 at `~/git/mlx-serve` (no shim) | 101 GB |
| `bonsai2mlxserve` | Ternary Bonsai 2 27B (prism-ml `mlx-2bit`, Hadamard-rotated ternary, rev `3f926b41`), a Qwen3.8-27B derivative | mlx-serve git main `6dea4241` at `~/git/mlx-serve-6dea4241` (no shim) | 8.6 GB |
| `qwen38fnmlxservenopld` | the same MLX mixed 4/8-bit weights, **PLD off** (`--no-pld`) — the #262 control arm | mlx-serve 26.9.1 (no shim) | 101 GB |
| `qwen38fnmlxservenopld-main` | the same MLX mixed 4/8-bit weights, **PLD off** — the #707 Unreal arm | mlx-serve origin/main `3a9181f` at `~/git/mlx-serve-main` (no shim) | 101 GB |
| `qwen38fnsushi4` | the same model as a **Sushi-4bpw** pack (EXL3 experts + 8-bit dense), MTP on, kv8 (#749) | sushi v1.0.2 built from source at `~/git/sushi` (no shim) | 159 GiB |

### Two DGX Sparks (`gb10-spark-x2`)

| backend | model | engine | size |
|---|---|---|---|
| `nemotron35supervldflashnothinkdual2xv032` | #1231: Super VL NVFP4 + LiLiCorr DFlash, reasoning off, both Sparks (TP=2), remote client | vLLM v0.32.0 | 70 GB |
| `mimov26flashrlexl3tfdflashdual2x` | #1032: MiMo-V2.6-Flash-RL EXL3 3.0 bpw + DFlash, TensorFold python-0.6, both Sparks (TP=2), remote client | TensorFold | 114 GB |
| `glm53fexl3tfnative113dual2x` | #1095: GLM-5.3-Flash EXL3 4bpw TF quant on stock TensorFold native 1.1.3, both Sparks, remote client | TensorFold | 164 GB |
| `glm53ffp4localdual2xrc` | NVIDIA's first-party QAD FP4 GLM-5.3-Flash across two Sparks, eugr recipe glm-5.3-flash.yaml @0bdc9c9 with GMU 0.79 (memory floor), thinking on like C1, remote client (#1128) | vLLM TP=2 in eugr/spark-vllm-b12x | 165.3 GiB |
| `dsv4flashvisiondspark2xrc` | **DeepSeek-V4-Flash-Vision-Exp across BOTH Sparks** (#649/#650) — vLLM TP=2 over the 200 Gb/s fabric, DSpark speculation, 1M context, from a **remote client**. The first backend on the `gb10-spark-x2` tier: it needs two nodes and does not run on one | vLLM (anemll dspark-vllm-gx10 0.1.1, pinned by digest) | 157 GiB on each node |
| `qwen38fnnvfp4dual2xrc` | **Qwen3.8-Flash-Next NVFP4 across BOTH Sparks** (#663) — vLLM TP=2 + EP + MTP 3 over the fabric, first-party NVIDIA weights, from a **remote client**. The two-node counterpart to the most-measured single-Spark model here | vLLM (vllm/vllm-openai:qwen38-flash-next) | 133 GiB |
| `qwen38fnnvfp4dual2xrcflags` | the row above **with the published two-node vLLM flags** (#664) — `IPC_LOCK`, marlin NVFP4 GEMM, trtllm allreduce, FlashInfer MoE FP4 off. The recipe's own `EXTRA_DOCKER_ARGS` cannot deliver them: it is a documented no-op | vLLM (vllm/vllm-openai:qwen38-flash-next) | 133 GiB |
| `qwen38fnnvfp4dual2xrcv030` | the flags row above on **the latest recipe and vLLM 0.30.0** (#717): recipe @d23790b with its multi-turn change (block drop off, index share on), the v0.30 lane, BF16 KV, GMU 0.78 | vLLM 0.30.0 (vllm/vllm-openai:v0.30.0) | 133 GiB |
| `qwen38fnnvfp4nv2xspec` | **#865 control**, retired 2026-10-12: the v030 row above re-run on the **i9 client**, MTP declared with `draft_engine = "vllm"` so each trial records draft acceptance from `/metrics` (#680 finding 7). Screened out at 248.2% of the leader (PR #1046) | vLLM 0.30.0 (vllm/vllm-openai:v0.30.0) | 133 GiB |
| `qwen38fnnvfp4nv2xspec031` | **#865 control on vLLM 0.31.0** (#1049), retired 2026-10-12 without a row: #865 moved to vLLM main the same hour | vLLM 0.31.0 (vllm/vllm-openai:v0.31.0) | 133 GiB |
| `qwen38fnnvfp4nv2xspecmain` | **#865 control on vLLM main**: the control row on one pinned nightly of main, shared with the treatment so the weights are the only difference | vLLM main (vllm/vllm-openai:nightly-fe4df3df, 2026-10-12) | 133 GiB |
| `qwen38fnnvfp4rh2xspecmain` | **#865 treatment on vLLM main**: RedHatAI's NVFP4 (compressed-tensors, BF16 PLE), which loads only with vllm#59431 (main since 2026-09-30) | vLLM main (vllm/vllm-openai:nightly-fe4df3df, 2026-10-12) | 173.6 GiB |
| `qwen38fnnvfp4rh2xspec` | **#865 treatment**, retired 2026-10-12 without a row: the control row with **RedHatAI's NVFP4 quant** (snapshot c8f2fb1b) in place of NVIDIA's, via `OVERRIDE_MODEL_ID`. vLLM 0.30.0 and 0.31.0 refuse it at model init (compressed-tensors PLE) | vLLM 0.30.0 (vllm/vllm-openai:v0.30.0) | 173.6 GiB |
| `qwen38fnfp8dual2xrc` | the same model as **the official FP8 checkpoint** (#721), same recipe (`start-fp8.sh`) and launch patch: the higher-precision build, measured beside NVFP4 for #717's quality-first ranking | vLLM (vllm/vllm-openai:qwen38-flash-next) | 172.8 GiB |
| `glm53fexl3dual2xrc` | **GLM-5.3-Flash EXL3 4bpw across BOTH Sparks** (#648) — EXL3 vLLM TP=2 over the fabric, DFlash2 speculation, 262k context, reasoning effort low server-side, from a **remote client**. The two-node counterpart to #298's single-Spark GLM-5.3-Flash Q2 on ds4 | vLLM (MiaAI-Lab GLM-5.3-Flash-2x-DGX-Sparks EXL3 build @c1b7d4c) | 164 GiB |
| `glm53fexl3dual2xrcctx` | the row above **re-run with the client fixed** (#648, #680): OpenCode context limit 262144 instead of 65536, and a 12 GiB kernel-enforced client container. Same server | vLLM (MiaAI-Lab GLM-5.3-Flash-2x-DGX-Sparks EXL3 build @c1b7d4c) | 164 GiB |
| `glm53fexl3dual2xrclatest` | the ctx row above on **the latest recipe** (#717): launcher @943912c with its host-mounted overlays (#264 KV tail-seed stride fix, loader v2, warmup canary). Same image, weights and .env | vLLM (MiaAI-Lab GLM-5.3-Flash-2x-DGX-Sparks EXL3 image, digest 447114ee) | 164 GiB |
| `glm53ftfjaydual2xrc` | **GLM-5.3-Flash on TensorFold** via [jayleaton/glm53-tensorfold-spark](https://github.com/jayleaton/glm53-tensorfold-spark) (#798): TensorFold v0.3.4 + 57 patches, 1M shared FP8 latent KV pool, **abliterated** neko-legends EXL3 weights (a different model from our GLM pick), reasoning effort low | TensorFold v0.3.4 + recipe patches (local image glm53-tensorfold:dev) | ~176 GB |
| `glm53ftfjaydual2xrchigh` | The same TensorFold stack with the server's **reasoning effort high** and tool fixes all (#840 effort arm, recipe @ad63acf), against `glm53ftfjaydual2xrc` at low | TensorFold v0.3.4 + recipe patches (local image glm53-tensorfold:dev) | ~176 GB |
| `glm53fexl3tfmiaaidual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe** (#892): TensorFold v0.6.0 + MiaAI patches, the same TR3 4bpw checkpoint as our GLM pick, reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfmiaaiv132dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.3.2** (#892; cache entries 32, multi-lone 0): TensorFold v0.6.0 + MiaAI patches, the same TR3 4bpw checkpoint as our GLM pick, reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfmiaaiv14dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.4+** (#892; earlier turns' reasoning kept, smooth streaming, sliced prompt fill): TensorFold v0.6.0 + 68 MiaAI patches, the same TR3 4bpw checkpoint as our GLM pick (pinned in `scripts/local.sh`), reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfmiaaiv15dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.5** (#892; v1.4 plus the 8-stream and serial-stop patches): TensorFold v0.6.0 + 70 MiaAI patches, the same TR3 4bpw checkpoint as our GLM pick (pinned in `scripts/local.sh`), reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfmiaaiv17dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.7, TR3 weights** (#912 control): v1.5 plus patches 0071–0075 (shared-prefix copy, compact before evict, queued-request cancellation), TensorFold v0.6.0 + 75 MiaAI patches, the pick's TR3 4bpw checkpoint @9eaebb7, reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfqmiaaiv17dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.7, MiaAI's TensorFold quant** (#912 treatment): the same build as the control, with Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold @76c0b517 (MiaAI's own calibration, the recipe default), reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfmiaaiv171dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.7.1, TR3 weights** (#912 control on v1.7.1): v1.7 plus patch 0077 (shared-prefix states kept by recency), TensorFold v0.6.0 + 76 MiaAI patches, the pick's TR3 4bpw checkpoint @9eaebb7, reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfqmiaaiv171dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.7.1, MiaAI's TensorFold quant** (#912 treatment on v1.7.1): the same build as its control, with Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold @76c0b517, reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfmiaaiv18dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.8, TR3 weights** (#912 control on v1.8): v1.7.1 plus patches 0078-0083, TensorFold v0.6.0 + 82 MiaAI patches, the pick's TR3 4bpw checkpoint @9eaebb7, reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfqmiaaiv18dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.8, MiaAI's TensorFold quant** (#912 treatment on v1.8): the same build as its control, with Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold @76c0b517, reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfqmiaaiv110dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.10, MiaAI's TensorFold quant** (#997): the pick's weights (@76c0b517) on recipe v1.10 @549fdc5, whose default sampling no longer takes a CPU sort; reasoning effort low sent per request | TensorFold v0.6.0 + 96 recipe patches (built locally) | ~164 GiB |
| `glm53fexl3tfqmiaai20261010dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe main @b5808ac, MiaAI's TensorFold quant** (#1018): the pick's weights (@76c0b517) on the recipe's 2026-10-10 main: FILL_ROWS=2048 and patches 0100-0103; reasoning effort low sent per request | TensorFold v0.6.0 + 107 recipe patches (built locally) | ~164 GiB |
| `glm53fnvfp4sparkglmdual2xrc` | **GLM-5.3-Flash NVFP4 on Enntity's Atlas fork via SparkGLM** (#999): nvidia/GLM-5.3-Flash-NVFP4 @423acf37 with DFlash2 drafts, Enntity/sparkglm @cd6b47d defaults (4 requests x 512K), reasoning effort low sent per request | Atlas (Enntity fork @7838b34c, built locally) | ~200 GiB |
| `glm53fexl3tfablitmiaaiv18dual2xrc` | **GLM-5.3-Flash on TensorFold via MiaAI-Lab's recipe v1.8, abliterated TensorFold quant** (#940): Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit @57edefd2 with `ABLIT=1` and `THINKING=1` (matching its parent), the #912 v1.8 build, reasoning effort low sent per request | TensorFold v0.6.0 + recipe patches (built locally) | ~164 GiB |
| `glm53fnvfp4kindlingdual2xrc` | **GLM-5.3-Flash on NVIDIA's NVFP4 weights via kindlingai's recipe** (#896): vLLM nightly + adaptive-k DFlash2 and RDMA collectives, mentat launcher, TP=2, reasoning effort low sent per request | vLLM nightly-ddd6fbca148a + recipe overlays (built locally) | ~183 GiB |
| `qwen38fnhibrid48dual2xrc` | **Qwen3.8-Flash-Next hibrid48 on vr8vr8's two-Spark recipe** (#806): myllmbox/qwen38-flash-next-cluster-recipe v4.1, NVFP4 output head, MTP K=5, bf16 KV; served as the NVFP4 arm's model name, reasoning effort low | vLLM 0.30.0 + patches (myllmbox/qwen38-flash-next-cluster-vllm:v6) | ~99 GB |
| `qwen38fnhibrid48v52dual2xrc` | **Qwen3.8-Flash-Next hibrid48 on recipe v5.2, thinking on** (#977): myllmbox/qwen38-flash-next-cluster-recipe v5.2 @d2721ec, the #806 weights @e316057d, MTP up to 7 with dynamic depth, RoCE all-reduce, dual rail, 33G KV per box; reasoning effort low server-side | vLLM 0.30.0 + patches (myllmbox/qwen38-flash-next-cluster-vllm:v5.2) | ~99 GB |
| `qwen38fnhibrid48v52nothinkdual2xrc` | **Qwen3.8-Flash-Next hibrid48 on recipe v5.2, thinking off** (#977): the same server with `enable_thinking: false` as the server's default chat-template argument | vLLM 0.30.0 + patches (myllmbox/qwen38-flash-next-cluster-vllm:v5.2) | ~99 GB |
| `qwen38fnnvfp4tfmiaaidual2xrc` | **Qwen3.8-Flash-Next NVFP4 on TensorFold's Zig engine via MiaAI-Lab's two-Spark recipe** (#897): MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold @cbfc429, nvidia/Qwen3.8-Flash-Next-NVFP4 @fc694b54 (the vLLM NVFP4 arms' weights), recipe defaults (16 requests, FP8 KV, 1M window, MTP + copy drafts), reasoning effort low sent per request | TensorFold zig-flashnext @db281878 + recipe patches (built locally) | ~124 GiB |
| `glm53fexl3dual2xrclatestout64k` | GLM on **the latest of everything** with **OpenCode's per-step output cap at 65,536** instead of 16,384 (#726): recipe @94ae731, OpenCode 1.18.33, provider entry `vllm8888out64k` | vLLM (MiaAI-Lab GLM-5.3-Flash-2x-DGX-Sparks EXL3 image, digest 447114ee) | 164 GiB |
| `dsv41fexl3dual2xrc` | **DeepSeek V4.1 Flash EXL3 2.9 bpw across BOTH Sparks** (#685) — vLLM TP=2 + ExLlamaV3, native Engram tables, DSpark k=3, 262k context, reasoning effort low server-side, from a **remote client**. 385 GiB of weights | vLLM (MiaAI-Lab DeepSeek-v4.1-Flash-EXL3-2x-DGX-Sparks @6f7d159, local build) | 385 GiB |
| `dsv41ftfzigjaydual2xrc` | **DeepSeek V4.1 Flash EXL3 2.8 bpw on the TensorFold CUDA Zig engine across BOTH Sparks** (#919): jayleaton/deepseek-v41-tensorfold-spark @2dcce03, q28-v2 pack @915e1d0, packed Engram shards, DSpark drafting, 1M context, reasoning effort low server-side, from a **remote client**. 189.8 GiB weights + 188.8 GiB packed Engram (two rank shards) | TensorFold Zig (engine/zig on TensorFold 1.0.2, local build) | 378.6 GiB |
| `dsv41f1017tfzigjaydual2xrc` | **DeepSeek V4.1 Flash 1017 EXL3 2.8 bpw on the TensorFold CUDA Zig engine across BOTH Sparks** (#1142): jayleaton/deepseek-v41-tensorfold-spark @853310d, flash17 pack @9e7c02ec06, 1017 Engram shards, 2.1M-token pool, remote client. 189.8 GiB weights + the packed 1017 Engram (two rank shards; size set at packing) | TensorFold Zig (engine/zig, local build) | 189.8 GiB + Engram |
| `dsv41f1017eaqtfmiaaidual2xrc` | **DeepSeek V4.1 Flash 1017 EXL3-EAQ-v1 2.9 bpw on MiaAI's TensorFold recipe across BOTH Sparks** (#1168): MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks @1afc891, Mia-AiLab EAQ-v1 @6d54be4fca, unquantized 1017 Engram shards, 1M-token pool, worker reads the head's HF cache over NFS, remote client. 196.2 GiB weights + Engram (~189 GiB per node) | TensorFold (MiaAI recipe, local build) | 196.2 GiB + Engram |
| `dsv41f1017eaq11tfmiaaidual2xrc` | **DeepSeek V4.1 Flash 1017 EXL3-EAQ-v1.1 2.9 bpw on MiaAI's TensorFold recipe across BOTH Sparks** (#1168): the v1 arm's recipe @27741e1 and image with `EAQ_V1_1=1` (Mia-AiLab EAQ-v1.1 @aee640e2af, indexer path BF16), reasoning effort low server-side (the v1 rows ran at the template's high), remote client. 196.3 GiB weights + Engram | TensorFold (MiaAI recipe, local build) | 196.3 GiB + Engram |
| `dsv41f1017tfbertdual2xrc` | **DeepSeek V4.1 Flash 1017 EXL3 2.5 bpw on bertholomus's TensorFold TP2 build across BOTH Sparks** (#930): recipe @503b64e + a local GID commit, engine `deepseek-v41-zig-tp2` @a57deb9d47 + a local GID commit (Zig 0.17.0, built locally), bertholomus 2.5 bpw @b8054f701a, unquantized 1017 Engram shards, 8 streams, reasoning effort low server-side, remote client. 172.0 GiB weights + Engram | TensorFold (bertholomus fork, local build) | 172.0 GiB + Engram |
| `beam10tfbertdual2xrc` | **Beam-1.0-501B-A23B (Reflection) EXL3 3 bpw on bertholomus's TensorFold TP2 fork across BOTH Sparks** (#1308): recipe `bertholomus/beam-1.0-2x-dgx-spark` @bdfa59f + 2 local commits (SERVE_ARGS; image check by registry digest), engine `beam-1.0-tp2` @d00201b4 + local commit d4976fbc (streamed calls close their JSON) built at start, 262,144-token pool in `nvcr.io/nvidia/pytorch:26.07-py3`, bertholomus 3 bpw @fb04dd6990, Reflection DFlash2 drafter @163b276be7, reasoning effort low server-side, remote client. 180.4 GiB weights + 3.6 GiB drafter | TensorFold (bertholomus fork, built at start) | 184.0 GiB |
| `dsv41f1017exl3serodual2xrc` | **DeepSeek V4.1 Flash 1017 EXL3 2.7 bpw on vLLM across BOTH Sparks** (#1160): the same 1017 checkpoint as #1142 on a different engine. sudoingX/dgx-spark-deepseek-v41 @cc59c360 (vLLM 6e517b15 + ExLlamaV3, local build), 0xSero quant @e34ef55bda, 1017 Engram shards, DSpark, 262k context, reasoning effort low server-side, remote client | vLLM (sudoingX dgx-spark-deepseek-v41 @cc59c360, local build) | 184.9 GiB + 189 GiB Engram |
| `dsv41f1017exl3sudov2dual2xrc` | **DeepSeek V4.1 Flash 1017 EXL3 2.8 bpw on vLLM across BOTH Sparks** (#1244): the pick's stack at its v2.0 release. sudoingX/dgx-spark-deepseek-v41 v2.0 @f8f20b37 (image dsv41-serve:v5, local build), sudoingx quant @aba08ea002, 1017 Engram shards, DSpark, 262k context, reasoning effort low server-side, remote client | vLLM (sudoingX dgx-spark-deepseek-v41 v2.0 @f8f20b37, local build) | 189.9 GiB + 189 GiB Engram |
| `mm31fexl3jaydual2xrc` | **MiniMax-M3.1-Flash EXL3 3.55 bpw on TensorFold across BOTH Sparks** (#1245): jayleaton/minimax-m31-tensorfold-spark @7ff046b8 (TensorFold v1.1.2 + the minimax_m3_vl family, image built locally; the recipe's heat guard turned off by a local commit, operator 2026-10-26), jayleaton/MiniMax-M3.1-Flash-EXL3-3.55bpw-mm31 @4576da0c1d (186.2 GiB), TP=2, 114,688-token window, 1 stream, dspark drafts, :8888; the i9 drives it | TensorFold v1.1.2 (jayleaton recipe, local build) | 186.2 GiB |
| `mimo26fdual2xrc` | **MiMo-V2.6-Flash-RL across BOTH Sparks** (#672) — SGLang TP=2 EP=2 over the fabric, DFlash speculation, 262k context, thinking off server-side, from a **remote client**. The first model on the pair that cannot run on one Spark at all | SGLang (MiaAI-Lab MiMo-V2.6-Flash-2x-DGX-Sparks @201be3e on lmsysorg/sglang nightly 20260921) | 161 GiB |
| `mimo26fdual2xrcthink` | the row above **with thinking on** (#672): the server-side `enable_thinking=false` default removed, nothing else changed | SGLang (MiaAI-Lab MiMo-V2.6-Flash-2x-DGX-Sparks @201be3e) | 161 GiB |
| `ling30fdual2xrc` | **Ling-3.0-flash official FP8 across BOTH Sparks** (#752) — the first Ling (inclusionAI BailingMoeV3) model measured here: 124B total / 5.1B active, SGLang TP=2 over the fabric, built-in MTP, 262k context, thinking on, from a **remote client** | SGLang (lmsysorg/sglang nightly-dev-cu13-20260921-0f6761b5, mainline) | 119.6 GiB on each node |

### One DGX Spark (`gb10-spark`): testing stopped 2026-10-23

| backend | model | engine | size |
|---|---|---|---|
| `qwen36codinggguf` | `qwen3.6:27b-coding` (**GGUF**) | Ollama | 17 GB |
| `nemotron33` | `nemotron3:33b-q4_K_M` — **Nano Omni, multimodal/document-intelligence, not a coding model** | Ollama | 26 GB |
| `nemotroncascade2` | `nemotron-cascade-2:30b-a3b-q4_K_M` (**agentic**, GGUF) | Ollama | 23 GB |
| `nemotron3nano` | `nemotron-3-nano:30b-a3b-q8_0` (**agentic**, GGUF) | Ollama | 31 GB |
| `nemotronsuper` | `nemotron-3-super:120b-a12b-q4_K_M` (**GGUF**) | Ollama | 81 GB |
| `qwen38fnq3dgx` | Qwen3.8-Flash-Next `UD-Q3_K_XL` | llama.cpp CUDA sm_121 | 83.8 GiB |
| `ds4dgx` | DeepSeek-V4-Flash **Q2** 0731 | ds4 CUDA sm_121a | 80.8 GiB |
| `glm53dgx` | GLM-5.3-Flash **Q2**, ctx 100000 | ds4 CUDA sm_121a | — |
| `qwen36nvfp4dgx` | `nvidia/Qwen3.6-27B-NVFP4` (**base**, not the coding tune) | vLLM 0.29.0 CUDA sm_121 | 20.4 GiB |
| `qwen36a3bnvfp4dgx` | `nvidia/Qwen3.6-35B-A3B-NVFP4` (**the A3B tier**, thinking off) — fastest agent backend on this box (#335, #342) | vLLM 0.29.0 CUDA sm_121 | 24.2 GiB |
| `ornith15a3bdgx` | `harborwater/Ornith-1.5-35B-A3B-AWQ-INT4` (multimodal `qwen3_5_moe`, served text-only) — the cross-arch A3B check (#335) | vLLM 0.29.0 CUDA sm_121 | 24.4 GiB |
| `nemotron35lightninga3bdgx` | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` (hybrid Mamba-2+MoE A3B, thinking off) — NVIDIA's A3B-leader competitor; measured slower on the agent suite (#404) | vLLM 0.29.0 CUDA sm_121 | ~18 GiB |
| `nemotron35lvllmrcdgx` | #1066 control: the same NVIDIA NVFP4 weights (@bee75962), thinking off, 131k ctx, on a **remote client** after the #968 fix | vLLM latest CUDA sm_121 | 20.1 GiB |
| `nemotron35lvllm262rcdgx` | #1066 control: the same NVIDIA NVFP4 weights (@bee75962), thinking off, 262k ctx, on a **remote client** after the #968 fix | vLLM latest CUDA sm_121 | 20.1 GiB |
| `nemotron35ltf109rcdgx` | #1066: the same weights on **TensorFold 1.0.9's native CUDA engine** (release archive), MTP drafts, thinking off, remote client: the engine-only A/B | TensorFold 1.0.9 CUDA sm_121 | 20.1 GiB |
| `nemotron35ltf1010rcdgx` | #1066: the same weights on **TensorFold 1.0.10's native CUDA engine** (release archive), MTP drafts, thinking off, remote client: the engine-only A/B | TensorFold 1.0.10 CUDA sm_121 | 20.1 GiB |
| `nemotron35ltf1010c262rcdgx` | #1066: the same weights on **TensorFold 1.0.10's native CUDA engine** (release archive), MTP drafts, thinking off, 262k ctx, remote client: the engine-only A/B | TensorFold 1.0.10 CUDA sm_121 | 20.1 GiB |
| `nemotron35ltf1011c262rcdgx` | #1066: the same weights on **TensorFold 1.0.11's native CUDA engine** (release archive), MTP drafts, thinking off, 262k ctx, remote client: the engine-only A/B | TensorFold 1.0.11 CUDA sm_121 | 20.1 GiB |
| `gptoss20bdgx` | `openai/gpt-oss-20b` (MXFP4, reasoning model, `reasoning_effort=low`) — the open OpenAI model as an agent backend (#407); needs the harmony vocab pre-seeded | vLLM 0.29.0 CUDA sm_121 | ~13 GiB |
| `nemotron3super120bdgx` | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` (120B/12B-active LatentMoE, thinking off, no MTP) — the quality tier above the A3B leader that still fits one Spark (#406) | vLLM 0.29.0 CUDA sm_121 | 74.8 GiB |
| `nemotron3super120bmtpdgx` | the same NVFP4 weights plus the MTPv2 draft head, 3 speculative tokens — the wall-time lever for the row above (#406) | vLLM 0.29.0 CUDA sm_121 | 85.8 GiB |
| `nemotron3super120bmtpnopcdgx` | the MTP row above with prefix caching off, to isolate corrupted outputs (#406) | vLLM 0.29.0 CUDA sm_121 | 85.8 GiB |
| `qwen38fnnvfp4miaaidgx` | Qwen3.8-Flash-Next NVFP4 via the MiaAI-Lab single-Spark recipe: third-party weights, container and nine patched vLLM modules, operator-approved; KV target lowered 20 -> 10 GiB so a trial fits beside it (#468) | vLLM (MiaAI image) CUDA sm_121 | 99 GiB |
| `qwen38fnnvfp4miaainothinkdgx` | the MiaAI row above with **thinking off**, via the server's default chat-template kwargs; that row reasons by default and the arm it is compared with (`qwen38fnnvfp4dgx`) did not (#468) | vLLM (MiaAI image) CUDA sm_121 | 99 GiB |
| `qwen38fnnvidianvfp4nothinkdgx` | `nvidia/Qwen3.8-Flash-Next-NVFP4` (NVIDIA's own quant, 124 GiB) through the MiaAI recipe, **thinking off** — the checkpoint lever against the row above (#493) | vLLM (MiaAI image) CUDA sm_121 | 124 GiB |
| `qwen38fnnvidianvfp4dgx` | the NVIDIA row above with **thinking on** (the template default) — completes checkpoint × thinking with the two MiaAI rows (#493) | vLLM (MiaAI image) CUDA sm_121 | 124 GiB |
| `glm53flashexl3k2nothinkdgx` | GLM-5.3-Flash (320B-A18B) as **EXL3 K2** on one Spark: vcruz305's recipe, vLLM fork + vllm-exl3, MTP k=2, thinking off (#298) | vLLM fork + vllm-exl3 CUDA sm_121 | 97.7 GB |
| `glm53flashexl3k2nothinkrcdgx` | the EXL3 K2 row above from a **remote client** (#562) — the local arm stands at 1 row because 97.7 GB of weights left 16 GiB idle and its runs used a 1 GiB client cap; moving OpenCode off the box is what makes this model measurable | vLLM fork + vllm-exl3 CUDA sm_121 | 97.7 GB |
| `qwen3827bnvfp4miaainothinkdgx` | `unsloth/Qwen3.8-27B-NVFP4` (**dense 27B**) via MiaAI-Lab's single-Spark vLLM recipe, YaRN 1M, MTP k=2, thinking off; GMU 0.70 not the recipe's 0.84 (#303) | vLLM nightly-aarch64 CUDA sm_121 | 22.6 GB |
| `qwen3827bsglangdsparknothinkdgx` | the 27B on **SGLang** with the **DSpark** drafter via MiaAI-Lab's SGLang recipe, thinking off, mem 0.70 not 0.90 (#350, #303) | SGLang qwen38-27b image CUDA sm_121 | 24 GB + 2.7 GB draft |
| `qwen3827bsglangdflash2nothinkdgx` | the SGLang 27B row above with the **DFlash2** drafter instead of DSpark (#303) | SGLang dev image CUDA sm_121 | 24 GB + 2.6 GB draft |
| `qwen3827bsglangdsparkpinnednothinkdgx` | the DSpark row on the **DFlash2 row's pinned image**: separates the image from the drafter (#303) | SGLang dev image CUDA sm_121 | 24 GB + 2.7 GB draft |
| `qwen3827bsglangdflash2dgx` | the DFlash2 row with **thinking on** (the template default): the thinking lever on the 27B, as #493 did for Flash-Next (#303) | SGLang dev image CUDA sm_121 | 24 GB + 2.6 GB draft |
| `qwen36a3bsglangdgx` | the A3B NVFP4 agent leader on **SGLang**'s pinned nightly instead of vLLM, flags matched (#553) | SGLang dev image CUDA sm_121 | 22 GB |
| `qwen36a3bsglangrcdgx` | the #553 A3B SGLang arm unchanged, OpenCode on a **remote client**: the topology A/B (#562) | SGLang dev image CUDA sm_121 | 22 GB |
| `qwen36a3bsglangfmrcdgx` | A3B SGLang, remote client, **full memory** (mem-fraction 0.85) (#562) | SGLang dev image CUDA sm_121 | 22 GB |
| `qwen36a3bsglang1017dgx` | A3B SGLang nightly 2026-10-17, S1 refresh (#1087), remote client, full memory (mem-fraction 0.85) | SGLang nightly-cu134-20261017-a126e8d | 22 GB |
| `qwen36a3bnvfp4vllm032dgx` | A3B NVFP4 vLLM 0.32.0 (PyPI), S2 refresh (#1088), remote client, full memory (GMU 0.85) | vLLM 0.32.0 | 22 GB |
| `qwen36a3bnvfp4fmrcdgx` | A3B vLLM, remote client, **full memory** (gpu-memory-utilization 0.85) (#562) | vLLM 0.29.0 CUDA sm_121 | 24.2 GiB |
| `qwen3827bsglangdflash2nothinkfmrcdgx` | 27B SGLang + DFlash2, remote client, **full memory** (0.85; the recipe's 0.90 drops the box under earlyoom's line, #582) (#562) | SGLang dev image CUDA sm_121 | 24 GB + 2.6 GB draft |
| `qwen3827bsglangdflash2nothink1017dgx` | 27B SGLang + DFlash2 nightly 2026-10-17, S5 refresh (#1091), worker Spark, remote client, full memory (0.85) | SGLang nightly-cu134-20261017-a126e8d | 31 GB |
| `qwen3827bsglangdflash2nothink1021dgx` | #1220 arm A: the S5 stack on S5's nightly 2026-10-17 (BF16 LM head, RadixArk), worker Spark, remote client, full memory (0.85) | SGLang nightly-cu134-20261017-a126e8d | 31 GB |
| `qwen3827bnvfp4headsglangdflash2nothink1021dgx` | #1220 arm B: arm A with nvidia/Qwen3.8-27B-NVFP4 (NVFP4 LM head) | SGLang nightly-cu134-20261017-a126e8d | 20 GB |
| `qwen3827btandem083nvfp4nothinkrcdgx` | 27B TandemLLM 0.8.3 nvfp4 profile: StairCut over DFlash2 b8/b16, thinking off per request, remote client (#1067) | TandemLLM 0.8.3, host venv, CUDA sm_121 | 15 GB NVFP4 + 30 GB FP8 base + 7.7 GB drafters |
| `qwen38fnnvidianvfp4nothinkfmrcdgx` | NVIDIA Flash-Next NVFP4 via MiaAI, remote client, reserve 30 GiB (the recipe's own value for this checkpoint, #583), recipe 6b50864 (#562). **270 rows across three clients**: 90 bare-metal on the i3, 90 from the pinned client image on the i3, and 90 from the image on the Ryzen 9 7900X (#611), split by `client_machine` and `client_image` in every table | vLLM (MiaAI image) CUDA sm_121 | 124 GiB |
| `qwen38fnq3nothinkrcdgx` | Flash-Next Q3 llama.cpp, thinking off, remote client (no memory knob) (#562) | llama.cpp CUDA sm_121 | 83.8 GiB |
| `qwen38fnq3nothinkkv8rcdgx` | the q8_0-KV Q3 row, remote client (#562) | llama.cpp CUDA sm_121 | 83.8 GiB |
| `ds4rcdgx` | DeepSeek-V4-Flash on ds4, remote client (#562) | ds4 CUDA sm_121a | 80.8 GiB |
| `qwen38fnq3rcdgx` | Flash-Next Q3 llama.cpp, thinking on, remote client (#562) | llama.cpp CUDA sm_121 | 83.8 GiB |
| `qwen38fnq3nothinkb11642rcdgx` | #1089 refresh: Flash-Next Q3, thinking off, on **upstream llama.cpp b11642** with the card's non-thinking sampler (temp 0.7, top_p 0.8, top_k 20, presence 1.5), remote client | llama.cpp b11642 CUDA sm_121 | 83.8 GiB |
| `qwen38fnq3nothinkkv8b11827rcdgx` | #1090 refresh: S4, Flash-Next Q3, thinking off, **q8_0 KV**, on **upstream llama.cpp b11827** with the card's non-thinking sampler (temp 0.7, top_p 0.8, top_k 20, presence 1.5), remote client | llama.cpp b11827 CUDA sm_121 | 83.8 GiB |
| `qwen38fnq3nothinkkv8b11700rcdgx` | #1090 refresh: the q8_0-KV Q3 row on b11700, card non-thinking sampler, remote client | llama.cpp b11700 CUDA sm_121 | 83.8 GiB |
| `qwen38fnq3nothinkkv8b11642rcdgx` | #1090 refresh: the q8_0-KV Q3 row on b11642, card non-thinking sampler, remote client | llama.cpp b11642 CUDA sm_121 | 83.8 GiB |
| `qwen38fnq3b11642rcdgx` | #1094 refresh: Flash-Next Q3, thinking on, on b11642 with the GGUF's thinking-mode sampler, remote client | llama.cpp b11642 CUDA sm_121 | 83.8 GiB |
| `nemotron35boltdflashnothinkrcdgx` | #1103 screen: NVIDIA Nemotron 3.5 Bolt 74B-A9B 24GB (IQ2 QAT), thinking off, the card's 128 GB recipe with the DFlash drafter, remote client | llama.cpp b11669 CUDA sm_121 | 23.0 GiB |
| `nemotron35boltdflashrcdgx` | #1103 screen: Nemotron 3.5 Bolt, thinking on, same recipe, remote client | llama.cpp b11669 CUDA sm_121 | 23.0 GiB |
| `comet60bnvfp4mtpnothinkrcdgx` | #1111 screen: Multiverse Comet 60B-A12B NVFP4 (compressed Super VL) + MTP, card recipe + Super VL prefix flags, reasoning off, remote client | vLLM nightly 3f1bcfd | 37.7 GiB |
| `nemotron35bolttfnothinkrcdgx` | #1127 screen: NVIDIA Nemotron 3.5 Bolt 74B-A9B 24GB + LiLiCorr drafter on MiaAI's TensorFold recipe, thinking off, remote client | TensorFold zig 46a08c18 (built locally) | 23.9 GiB |
| `nemotron35supervldflashrcdgx` | #1109 screen: NVIDIA Nemotron 3.5 Super VL 120B-A12B NVFP4 + LiLiCorr DFlash, the card's Spark recipe, reasoning on, remote client | vLLM nightly 3f1bcfd | 71.1 GiB |
| `nemotron35supervldflashnothinkrcdgx` | #1109 screen: Super VL, reasoning off, same recipe, remote client | vLLM nightly 3f1bcfd | 71.1 GiB |
| `qwen38fnfp4localrcdgx` | #1108 screen: nvidia Qwen3.8-Flash-Next-FP4-Local (QAD FP4), the card's Spark recipe, thinking on, remote client | vLLM in eugr/spark-vllm-b12x | 95.2 GiB |
| `qwen38fnfp4localnothinkrcdgx` | #1108 screen: FP4-Local, thinking off, same recipe, remote client | vLLM in eugr/spark-vllm-b12x | 95.2 GiB |
| `qwen38fnfp4localtfnothinkrcdgx` | the #1108 FP4-Local weights on TensorFold's Zig engine via MiaAI's single-Spark TensorFold recipe @656879f, thinking off, head Spark, remote client (#1143) | TensorFold zig db281878 + 15 patches, built locally, CUDA sm_121 | 96 GiB |
| `qwen38fnexl3nothinkdgx` | Qwen3.8-Flash-Next as **EXL3 3.05 bpw** (turboderp's pack) via vLLM + vllm-exl3, MTP k=3, n-gram table on disk, thinking off (#434) | vLLM 0.29.0 + vllm-exl3 CUDA sm_121 | 80 GB |
| `qwen38fnexl3nothinkrcdgx` | the EXL3 row above from a **remote client** (#562), identical server config (ngram on disk, GMU 0.60) so the rows pool; the 80 GB pack no longer shares the pool with a trial | vLLM 0.29.0 + vllm-exl3 CUDA sm_121 | ~80 GB |
| `qwen38fnexl3nomtpnothinkdgx` | the EXL3 row above with **MTP off**: isolates whether the drafter causes its corrupted generations (#434) | vLLM 0.29.0 + vllm-exl3 CUDA sm_121 | 80 GB |
| `qwen38fnexl3nomtpnothinkrcdgx` | the MTP-off row above from a **remote client** (#562) — the lever for the #434 corruption, which the MTP arm reproduced remotely with 46 GiB free (one trial of 30 at 36,340 output tokens against a 1,303 median), ruling memory out | vLLM 0.29.0 + vllm-exl3 CUDA sm_121 | ~80 GB |
| `dsv41f1017sage162exl3rcdgx` | #1208: DeepSeek V4.1 Flash 1017 SAGE-EXL3 1.62 bpw on **one** Spark (vcruz305 recipe @6bfd102, built locally), reasoning off, remote client | vLLM TP=1 + ExLlamaV3 (vllm-exl3 30e8b94) | 304.2 GiB |
| `qwen36bf16dgx` | `Qwen/Qwen3.6-27B` **BF16**, the unquantized control for `qwen36nvfp4dgx` | vLLM 0.29.0 CUDA sm_121 | 51.7 GiB |
| `qwen36nvfp4v1dgx` | the same NVFP4 weights on vLLM's **V1** model runner, speculation off | vLLM 0.29.0 CUDA sm_121 | 20.4 GiB |
| `qwen36nvfp4specdgx` | the same again with **draftless n-gram speculation** (5 tokens, lookup 3-8) | vLLM 0.29.0 CUDA sm_121 | 20.4 GiB |
| `qwen36nvfp4specnoprefixdgx` | speculation again with **prefix caching off** — #354's test of whether the pass-rate decline is server state | vLLM 0.29.0 CUDA sm_121 | 20.4 GiB |
| `qwen36nvfp4nothinkdgx` | the same weights with **thinking off**, via a one-condition chat-template change | vLLM 0.29.0 CUDA sm_121 | 20.4 GiB |
| `qwen38fnq3nothinkdgx` | the machine's best backend with **thinking off**, via `--chat-template-kwargs` | llama.cpp CUDA sm_121 | 83.8 GiB |
| `qwen38fnq3nothinkkv8dgx` | the same, thinking off, **q8_0 KV cache** | llama.cpp CUDA sm_121 | 83.8 GiB |
| `qwen38fnq3nothinktopkdgx` | the same, thinking off, **PR27172 batched top-k** | llama.cpp CUDA sm_121 | 83.8 GiB |
| `qwen38fnq3nothinkub2048dgx` | the same, thinking off, **ubatch 2048** | llama.cpp CUDA sm_121 | 83.8 GiB |

**Also wired on the M5 Max, with no table row:** `qwen38fnq3reap` ·
`gemma426` · `qwen36a3b` · `qwen` · `qwen36` · `qwen38flashnext` ·
`qwen38fnq2` · `qwen38fnq4m64` · `ornith15llamacpp` · `glm53` · `glm52ds4` ·
`glm53ds4shim` · `mtplx` · `opus5` · `qwen38fnds4` · `qwen38fnds4mtp7` ·
`qwen38fniq4`. Several carry rows only from Claude Code or Codex, which are not
OpenCode figures. Every backend needs a block **and** an `opencode_model`, and
a test enforces it: **installed in Ollama is not the same as testable** (#69).

### Notes on specific M5 Max backends

**`qwen36codinggguf` exists because `qwen36coding` is macOS-only.** The
`mxfp8` and `nvfp4` tags ship as `vnd.ollama.image.tensor` layers that Ollama
routes to the MLX runtime, so on Linux the pull fails with "this model requires
MLX support" (#293). The plain GGUF tag runs anywhere. **Its rows are not
comparable to `qwen36coding` rows**: same weights, different quantization and
container.

**`qwen38fnds4kimat` is a different stack, not a different quant.** Its Q4_K
imatrix weights need ivanfioravanti's engine branch, and each engine refuses
the other's weights (`deepseek4.block_count missing`). Engine and quant move
together, so no comparison against `qwen38fnds4shim` can attribute a
difference to either alone (#138).

**`ds4` and `ds4anthropic` isolate the wire format**: identical weights and
server, only the protocol differs.

**The `qwen38fnds4*shim` backends carry the set's only deliberate confounds.**
`ds4_qwen_tool_shim.py` (a) adds a system line naming the tool-call format,
(b) **takes tool requests off ds4's streaming path**, and (c) translates the
XML tool dialect into real `tool_calls`. (b) matters: on-stream, ds4 drops an
invalid tool call and the client sees an empty turn. One identical request, 12
samples each: `stream:true` gave `tool_calls` 1/12, `stream:false` 7/12.
Through the shim the suite went from 0/45 to 36/45. A shim row therefore did
not stream from the engine and is not comparable to a streaming backend; a
comparison to llama.cpp must also state that the quant differs (Q4_0-routed
here, `UD-Q3_K_XL` there). The shim build's provenance problem is in
[`docs/results.md`](docs/results.md#the-ds4-shim-rows-were-measured-on-a-build-its-author-has-withdrawn).

**Every `qwen38fnds4mtp7shim` row was taken on an arm that never
speculated.** ds4 reaches its Qwen MTP path only at `temperature <= 0`, and
OpenCode sends no temperature for this model, so the engine default of 1.0
applies (#151). `qwen38fnds4mtp7greedy` and `qwen38fnds4greedy` are the pair
that tests MTP: a second shim on :8102 pins the temperature to 0, and the
greedy MTP-off arm separates speculation from greedy decoding.
`scripts/greedy_mtp_ab.py` runs them alternated.

`qwen38fnds4` and `qwen38fnds4mtp7` are the direct (no shim) engine isolation
for Qwen3.8-Flash-Next (#94): same weights and binary, only MTP differs.

### Notes on the DGX backends

`qwen36bf16dgx` is the unquantized BF16 control for `qwen36nvfp4dgx`. The
remote-client (`rc`) arms run OpenCode off the Spark (#562), so a trial does
not share the memory pool with the model. Every two-Spark backend uses a
remote client.

---

## Tasks

Fifteen in the default matrix, in three classes, plus a fourth class that runs
only when asked for. The exact prompt for each is published in
[`benchmarks/agent/PROMPTS.md`](benchmarks/agent/PROMPTS.md), generated from
the file the harness reads, with a test that fails if the two drift.

| class | count | what it measures |
|---|---|---|
| **Excision** (Python) | 8 | Find your way around unfamiliar code. One function body deleted; the repo's own suite is the only oracle |
| **Excision** (Swift) | 5 | The same, off the model's comfort ground — and a compile step Python cannot fail at. **macOS only** — `monitor` is an AppKit desktop app, so `swift test` is not an oracle off a Mac |
| **Script** | 2 | Empty directory, produce a working CLI. Trivial logic, real boilerplate, almost no variance |
| **Replay** (Python) | 14 | Rebuild a real gmail-archive feature commit, 37 to 583 reverted lines across 1 to 9 files, from its own tests (#714). Seven more are harder (#726): five of the original seven with some tests held out of the agent's tree and run only by the oracle, and a five-commit span of 1,643 added lines with held-out tests, once with a prompt that names no test file. **Not in the default matrix**: run the first seven with `--replay --targets sandbox`, the harder seven with `--replay-hard --targets sandbox`. See [METHODOLOGY](benchmarks/agent/METHODOLOGY.md#replay-tasks-714) |

Script tasks vary 1.0–2.1× and are the fair way to compare stacks. Excision
tasks are noisier and closer to real work.

**Ten of the fifteen are cross-platform**: the 8 Python excisions and the 2
script tasks. A comparison that mixes a 15-task Mac run with a 10-task Linux
run compares different suites.

---

## What counts as valid

A figure may be quoted only from rows that are **OpenCode**, **after
2026-08-31 21:47 EDT**, and **not excluded**. Use `results.is_excluded()` —
never a hand-rolled `r.get("excluded")`, which misses `agent_error` rows and
has already produced two sets of published numbers that were wrong.

Every row stamps the harness commit, the engine build, and the target repo
commit; M5 Max rows also stamp the Metal ceiling (`iogpu.wired_limit_mb`), which
Linux rows do not carry. A row that cannot name the code and the engine that
produced it cannot be re-derived once either moves.

---

## Changing the set

Retire, do not delete. `retired = "<reason>"` in `tasks.toml` drops a backend
from the default matrix; naming it with `--backend` still runs it, so a
decision can be revisited without editing config back in. The config block is
the record of how its existing rows were made.
