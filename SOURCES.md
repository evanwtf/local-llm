# Who to check on X

**Use:** *"Check our influencer list for updates over the past week."*

That means: run the sweep in [How to run it](#how-to-run-it) below, verify every
post before repeating it, and report what changed — not who said what.

The field moves faster than this project measures: two engines once shipped
double-digit gains in 48 hours while our docs still described three engines
(#60).

**Two lanes (#307).** Every account above the [DGX Spark
sources](#dgx-spark-sources) heading is the **Mac lane**, judged by *would this
change a number on an M5 Max, 128 GB, Metal?* The two-Spark cluster (GB10,
CUDA) is a different field: NVFP4/FP8, vLLM, TensorFold and SGLang, and
aggregate multi-stream throughput. The sweep splits by `--platform` (see
[Repositories to watch](#repositories-to-watch)). An account can sit in both
lanes. The Ryzen / RTX 3080 Ti desktop has no lane: it is a secondary tier and
not always on.

---

## Tier 1 — check every time

* **@antirez**: X: [@antirez](https://x.com/antirez) · GitHub: [@antirez](https://github.com/antirez) · Website: [invece.org](http://invece.org)  
DwarfStar / ds4 author  
**Two M5 Max picks run on his engine.** He ships models on preview branches and announces them here before the README catches up: `glm-5.3-flash` was on X days before it was documented.

* **@ivanfioravanti**: X: [@ivanfioravanti](https://x.com/ivanfioravanti) · GitHub: [@ivanfioravanti](https://github.com/ivanfioravanti)  
daily benchmarker, M3 Ultra / multi-Mac  
Highest-volume tester on Apple Silicon. #51 came from one of his posts: Q8_0→Q4_K attention+head, **+12.6% decode with a quality gain**.

* **@ddalcu**: X: [@ddalcu](https://x.com/ddalcu) · GitHub: [@ddalcu](https://github.com/ddalcu) · Website: [mlxserve.com](http://mlxserve.com) · [dalcu.com](http://www.dalcu.com)  
mlx-serve, `llmprobe`  
**Benchmarks on a 1x M5 Max 128 GB laptop — our exact machine** — and mlx-serve is the M5 Max's fastest pick. Ported ds4's evals into a cross-engine harness (`npx llmprobe --eval`).

* **@Beamsters1**: X: [@Beamsters1](https://x.com/Beamsters1) · GitHub: [@beamivalice](https://github.com/beamivalice) · HF: [beamster](https://huggingface.co/beamster)  
mlx-serve qwen4 performance contributor  
**Ships the qwen4 optimizations for Qwen3.8-Flash-Next on mlx-serve**, the M5 Max's fastest pick: split-K QSA, SSD-first cache admission, MTP/QSA rollback without copies. 8 of the 24 commits between our test build and v26.9.1 were theirs (#225). A verified 2026-09-08 post told us to build mlx-serve PR383 before we knew we needed it. They also author Sushi, the mlx-serve fork, and the `beamster` HF packs; the operator approved both for the M5 Max ([#749](https://github.com/evanwtf/local-llm/issues/749); identity verified from the [release post](https://x.com/beamsters1/status/2103362390243066257)). Low volume (~140 followers), so the cap-per-account rule below protects this account.

* **@Spangler3000**: X: [@Spangler3000](https://x.com/Spangler3000) · GitHub: [@jonathan308](https://github.com/jonathan308)  
oMLX Metal kernels (QSA, DFlash2)  
Densest Metal-kernel signal in the field. His PRs are why oMLX leads on prefill — **and prefill is our bottleneck** (#14, #50, ds4#816).

* **@jundotkim**: X: [@jundotkim](https://x.com/jundotkim) · GitHub: [@jundot](https://github.com/jundot) · Website: [omlx.ai](https://omlx.ai)  
oMLX author  
oMLX 0.6.4: PP 32k **834 → 1114 tok/s**, TG **40 → 46**. Unmeasured here (#737).

* **@ggerganov**: X: [@ggerganov](https://x.com/ggerganov) · GitHub: [@ggerganov](https://github.com/ggerganov)  
llama.cpp / ggml author  
llama.cpp is a pick on both machines. He ships backend releases and MTP serve flags; mainline `qwen4exp` MTP (#77) is his surface.

* **@korale77**: X: [@korale77](https://x.com/korale77) · GitHub: [@korale77](https://github.com/korale77) · [@eclaire-labs](https://github.com/eclaire-labs) · Website: [eclaire.co](https://eclaire.co)  
ds4 on an M5 Max 128 GB  
**Our engine, our machine, our models.** With Flor1an-B below, the only account posting ds4 numbers on an M5 Max 128 GB, and he posts on X. Verified examples: GLM-5.3-Flash Q2 on ds4 at `ctx=120128 ... avg=17.32 t/s` on Auto power; DeepSeek-V4-Flash **18–20 t/s at 200k context**. His GitHub holds a `ds4` fork and [`mlx-vlm-kv-bench`](https://github.com/korale77/mlx-vlm-kv-bench) (TurboQuant and TriAttention KV-cache benchmarks). **Single runs on "Auto power", an uncontrolled thermal state** (#58 puts ~4% of throughput on thermal state alone): leads to re-measure, not baselines. ~190 X followers: the volume rule below protects him.

## Tier 2 — check weekly

* **@Youssofal_**: X: [@Youssofal_](https://x.com/Youssofal_) · GitHub: [@youssofal](https://github.com/youssofal)  
MTPLX — MTP speculative decoding, custom Metal kernels. We hold an old, unreplicated MTPLX number marked provisional.

* **@Raullen**: X: [@Raullen](https://x.com/Raullen) · GitHub: [@raullenchai](https://github.com/raullenchai)  
Rapid-MLX (#57). Apache 2.0, OpenAI-compatible, claims 12x prefix cache.

* **@rapidmlx**: X: [@rapidmlx](https://x.com/rapidmlx) · Website: [rapidmlx.com](https://rapidmlx.com) · GitHub: [raullenchai/Rapid-MLX](https://github.com/raullenchai/Rapid-MLX)  
**The project account for Rapid-MLX**, distinct from @Raullen above. It ships the release notes, so watch it for versions. **Read the memory line before the speed line**: 0.13.3's GLM-5.3 headline uses 165 GB of active memory, which does not load in 128 GB. Ask which quant fits; the engine stays a lead. #57.

* **@awnihannun**: X: [@awnihannun](https://x.com/awnihannun) · GitHub: [@awni](https://github.com/awni) · Website: [awnihannun.com](https://awnihannun.com/)  
Co-created MLX. Lower volume since leaving Apple, still framework source-of-truth.

* **@jayleaton**: X: [@jayleaton](https://x.com/jayleaton) · GitHub: [jayleaton/glm53-tensorfold-spark](https://github.com/jayleaton/glm53-tensorfold-spark)  
**GLM-5.3-Flash on TensorFold across two DGX Sparks** (operator-added, 2026-09-29, #798). The recipe pins TensorFold v0.3.4 and adds 57 patches: a latent FP8 KV cache, a 1,048,576-token pool shared by 4 requests, chunked prefill, and a RoCE all-gather. It claims ~1.8x vLLM decode on the same weights, which are the abliterated `neko-legends/GLM-5.3-Flash-Uncensored-EXL3`. "I will publish all future updates on X." DGX lane; the repo ships fast (two merged PRs on its first full day), so read its commits, not just the feed.

* **@zcbenz**: X: [@zcbenz](https://x.com/zcbenz) · GitHub: [@zcbenz](https://github.com/zcbenz) · Website: [zcbenz.com](https://zcbenz.com)  
mlx-lm maintainer.

* **@N8Programs**: X: [@N8Programs](https://x.com/N8Programs) · GitHub: [@N8python](https://github.com/N8python)  
Quant conversions, custom kernels, training on Silicon.

* **@Prince_Canuma**: X: [@Prince_Canuma](https://x.com/Prince_Canuma) · GitHub: [@Blaizzy](https://github.com/Blaizzy) · Repo: [mlx-vlm](https://github.com/Blaizzy/mlx-vlm)  
mlx-vlm, mlx-audio. **Multimodal is the wrong reason to skip him.** `mlx-vlm v0.7.0-rc0` ships **expert offloading** (MoE straight from disk; bears on the 12 GB card in #20), a **prefix cache** claiming up to 166x end-to-end on Gemma 4 31B, and **Qwen3.8-Flash-Next with MTP** (#77). The 166x is a cache-hit number: it measures repeated prefixes, not first-token work.

* **@ShankPeople**: X: [@ShankPeople](https://x.com/ShankPeople)  
GGUF quant surgery. Measured **+20% decode** moving GLM-5.3 KDA proj/head to Q8 — the thread that produced #51. GitHub unresolved: `markshank` matches the name but its repos are macOS/BSD systems work with no LLM presence, so it is **not** linked here.

* **@Kevrsub**: X: [@Kevrsub](https://x.com/Kevrsub)  
Runs real coding benchmarks, regression-tests older models. Found oMLX's **stock MTP depth 3 vs 5 → 50→60 tok/s**. No GitHub found.

* **@0xSero**: X: [@0xSero](https://x.com/0xSero) · GitHub: [@0xSero](https://github.com/0xSero) · Website: [sybilsolutions.io](https://sybilsolutions.io) · YouTube: [@0xSero](https://www.youtube.com/@0xSero)  
Low-bit quantization, on **both of our models**. [`glm-5.3-low-bit-tr3-wiki`](https://github.com/0xSero/glm-5.3-low-bit-tr3-wiki) is a 13-chapter treatment of GLM-5.3 Flash at low bitrate — MoE structure, calibration and sensitivity, EXL3/TR3 trellis encoding, K2/K3/K4 mixed tiers, bitrate arithmetic, validation gates. [`deepseek-v4-flash-0731-spark-sparkinfer`](https://github.com/0xSero/deepseek-v4-flash-0731-spark-sparkinfer) (163★) pins **our exact 0731 checkpoint** with a VALIDATION.md. `turboquant` does 3-bit-key / 2-bit-value KV quantization. **Read the GitHub, not the X feed.** Most of it targets NVIDIA/vLLM: on the Mac the quantization reasoning transfers and the kernels do not. On the DGX lane both transfer, and his DeepSeek-V4.1-Flash-1017 EXL3 2.7 bpw is the two-Spark pick's weights (#1160).

* **@sudoingX**: X: [@sudoingX](https://x.com/sudoingX) · GitHub: [@sudoingX](https://github.com/sudoingX) · Repo: [qwen38-mtp](https://github.com/sudoingX/qwen38-mtp)  
Maintains the **qwen38-mtp** board: **61 paired baseline-vs-MTP runs from 47 contributors** on Qwen3.8-27B dense, each with quants, KV setup and serve config written down. The claim: the MTP head already ships in the GGUF, and one llama.cpp flag is worth **+33–39% decode** (#19, #39). The board is nearly all NVIDIA and AMD, and it ranks on decode rate, so the *flag* is the lead, not the number. The paired-run method is worth copying. **Also on the DGX lane**: he authors the two-Spark pick's stack, [sudoingX/dgx-spark-deepseek-v41](https://github.com/sudoingX/dgx-spark-deepseek-v41) (#1160).

* **@redp314**: X: [@redp314](https://x.com/redp314)  
Paolo Rosson, Dext. **Benchmarks six MLX engines against each other on one M3 Max 96 GB.** His key result: **speculative decoding's gain depends on what you generate.** Every drafter loses 17–36% moving from code to prose, because acceptance falls. We measure code, the favorable case, so an MTP figure quoted at us is close to a best case. The engine ranking transfers; the numbers do not.

* **@_ARahim_**: X: [@_ARahim_](https://x.com/_ARahim_) · GitHub: [@ARahim3](https://github.com/ARahim3) · Repo: [mlx-dspark](https://github.com/ARahim3/mlx-dspark)  
Abdur Rahim. **mlx-dspark**: a native MLX port of DSpark and DFlash speculative decoding, claiming up to 4x lossless decode (#19, #39). **Read #58 first**: our own DSpark result on ds4 inverted on re-measure, so check any DSpark claim on the M5 Max before believing it.

* **@TheDavidTai**: X: [@TheDavidTai](https://x.com/TheDavidTai) · GitHub: [@davidtai](https://github.com/davidtai) · Website: [davidt.ai](https://davidt.ai)  
Runner and drafting optimization — MTPLX PR #391 (Qwen 3.8 Flash Next **50 → 85 t/s**), Qwen 3.8 27B at **113 t/s** via adaptive DFlash2 + mlx.fast. **Mostly replies; originals arrive in bursts** — a sweep of his last 12 posts found 10 replies, 2 quotes, 0 originals, so judge him on a week, not a day. Ties @Youssofal_'s MTPLX to @jundotkim's oMLX.

* **@0xkydo**: X: [@0xkydo](https://x.com/0xkydo) · [MLX Fast leaderboard](https://www.yukon.org/mlxfast) · Repo: [engine](https://github.com/Layr-Labs/mlxfast-gemma4-26b-a4b-engine)  
Kydo, Eigen Labs. Runs **MLX Fast** (`yukon.org/mlxfast`), a public leaderboard for making **Gemma 4 26B A4B** faster on Apple Silicon. Official runs pair baseline and candidate on the same Mac under a thermal gate, a better method than most. It scores `prefill^0.25 · decode^0.75`, so decode carries three quarters of the rank, and decode rate does not predict agent wall time: our `gemma4` backend emits fewer tokens than qwen3.8 and still finishes last. We hold a 31B mxfp8 Gemma, not the 26B A4B, so we have no baseline for it. #16, #80.

* **@yukonresearch**: X: [@yukonresearch](https://x.com/yukonresearch) · Website: [yukon.org](https://yukon.org/) · [MLX Fast](https://www.yukon.org/mlxfast)  
**Yukon**, the Eigen Labs platform that @0xkydo's leaderboard runs on, with several tracks and a weekly prize pool. **Read it for two things.** The Apple Silicon track history: they say Qwen 3.8 27B gained on day one from MTP and reached 3x in two weeks, a claim checkable here (#77, #83). And the **method**: one shared harness, only verified movement scores, and a **public lineage tree that keeps failures searchable**. That is worth copying. Their numbers are decode and prefill rates, not agent wall time. #80.

* **@thdxr**: X: [@thdxr](https://x.com/thdxr) · GitHub: [@thdxr](https://github.com/thdxr)  
Builds **OpenCode**, the only client in this repo. A client release starts a new series (#137 mixed two versions in one cell), so his feed is the cheapest early warning. Judge on releases, not TUI features.

* **@trymirai**: X: [@trymirai](https://x.com/trymirai) · GitHub: [@trymirai](https://github.com/trymirai) · Repo: [uzu](https://github.com/trymirai/uzu)  
**uzu**, an Apple-only inference engine (#134; the engine entry is under [Read the repo, not the feed](#read-the-repo-not-the-feed)). Posts M5-series **speculative decoding** numbers against MTPLX and llama.cpp (#19, #39). Their claim, unmeasured here.

* **@UnslothAI**: X: [@UnslothAI](https://x.com/UnslothAI) · GitHub: [@unslothai](https://github.com/unslothai)  
Dynamic GGUF quants and local-run recipes (#119). Our llama.cpp picks run their `UD-Q3_K_XL` Flash-Next quant. Their speed claims are decode rates, not agent wall time.

* **@YJesus**: X: [@YJesus](https://x.com/YJesus) · GitHub: [@YJesus](https://github.com/YJesus)  
Cross-engine benchmarker on M3 Ultra 256 GB. **Runs ds4** beside llama.cpp, mlx-serve and MTP on one machine. The ranking transfers, the numbers do not.

* **@Eschalabs**: X: [@Eschalabs](https://x.com/Eschalabs) · GitHub: [@Eschalabs](https://github.com/Eschalabs) · Repo: [escha-mlx](https://github.com/Eschalabs/escha-mlx)  
2-bit quants with custom MLX Metal kernels, **prefix caching**, M5 Pro batch numbers. Prefill is our bottleneck (#14, #50) and prefix caching is the lever. Low volume.

* **@mweinbach**: X: [@mweinbach](https://x.com/mweinbach) · GitHub: [@mweinbach](https://github.com/mweinbach)  
Max Weinbach. M5 Metal kernel work: a **packed-INT4 Metal 4.1 path** and MLX Fast runs. Metal 4 tensor kernels are live for us: ds4 auto-enables the tensor route on M5 (#138), and `DS4_METAL_ENABLE_TENSOR` accumulate drift is a known hazard (#149). **High volume**: gather him apart from the small accounts (see the volume rule below). `github.com/mweinbach` is a name match only: its repos are agent and Swift tooling.

* **@13scoobie**: X: [@13scoobie](https://x.com/13scoobie) · GitHub: [@13scoobie](https://github.com/13scoobie)  
David White. **Runs our model on an M5 Max, on oMLX, which we have not measured.** Verified: DeepSeek-V4-Flash 0731 via oMLX, prompt processing **272 tok/s**, generation **14 tok/s** (memory size not stated). Also hit an OOM after ~3 hours of Qwen3.8-Flash-Next MTP on mlx-serve, an MTP failure mode worth knowing. Low volume. His GitHub is a fork list; it forks `ddalcu/llmprobe`, the tool his verified post promotes, and that ties the two accounts.

* **@0xZKnw**: X: [@0xZKnw](https://x.com/0xZKnw) · GitHub: [@0xZKnw](https://github.com/0xZKnw) · Repo: [mlxl3](https://github.com/0xZKnw/mlxl3) · HF: [0xzknw](https://huggingface.co/0xzknw) · Website: [0xzknw.tech](https://0xzknw.tech)  
**MLXL3: EXL3 inference on Apple Silicon in MLX with custom Metal kernels.** Verified: LFM2.5-8B-A1B at 3.1 bpw, ~95 tok/s decode on a base M5. **The mechanism is the lead, not the number**: trellis/EXL3 quantization on Metal, the ground @0xSero writes up for NVIDIA. Sushi's Flash-Next packs are EXL3 too (#749). ~60 followers: the volume rule protects him.

* **@Brooooook_lyn**: X: [@Brooooook_lyn](https://x.com/Brooooook_lyn) · GitHub: [@Brooooooklyn](https://github.com/Brooooooklyn) · HF: [Brooooooklyn](https://huggingface.co/Brooooooklyn) · Repo: [mlx-node](https://github.com/mlx-node/mlx-node) · Website: [lyn.one](https://lyn.one)  
LongYinan, author of napi.rs. **Publishes MXFP4/MXFP8 dynamic MLX checkpoints** (e.g. [Qwen3.8-27B-MXFP4-mlx](https://huggingface.co/Brooooooklyn/Qwen3.8-27B-MXFP4-mlx)), our own quantization axis. Verified claims, unmeasured here: "M5Max is already faster than DGX Spark in prefill", and "MTP and concurrent inference cannot both be had" (#19, #77). Much of the feed is JS/Rust tooling; read for the MLX posts.

* **@no_stp_on_snek**: X: [@no_stp_on_snek](https://x.com/no_stp_on_snek) · GitHub: [@TheTom](https://github.com/TheTom) · Repo: [llama-cpp-turboquant](https://github.com/TheTom/llama-cpp-turboquant) · [turboquant_plus](https://github.com/TheTom/turboquant_plus)  
Tom Turney. **TurboQuant KV-cache compression, shipped as a llama.cpp fork with Metal and CUDA kernels**; KV cache is the prefill lever (#14, #50). Also builds `atlas`, a Rust inference engine. **Owns an M5 Max, an RTX 5090 and a DGX Spark**, so he serves both lanes. **High volume, mostly short replies**: the volume rule below applies; read the repo before the feed.

* **@ashxhart**: X: [@ashxhart](https://x.com/ashxhart)  
**TensorFold's author** ([ashhart](https://github.com/ashhart)). **Owns an M5 Max 128 GB**, an M3 Ultra 256 GB and 2x DGX Spark, and posts agent-relevant numbers, not only decode: TensorFold on dense Qwen3.8-27B on the M5 Max, fresh agent-session TTFT **30 s -> 0.42 s** ([post](https://x.com/ashxhart/status/2102759677637321146), verified). TensorFold runs the two-Spark fast option, so read him on a DGX sweep too.

## Tier 3 — occasional

* **@TeksEdge**: X: [@TeksEdge](https://x.com/TeksEdge) · Website: [teksed.com](https://teksed.com)  
Runnable oMLX/DFlash recipes. No GitHub found.

* **@digitalix**: X: [@digitalix](https://x.com/digitalix) · GitHub: [@alexziskind1](https://github.com/alexziskind1) · YouTube: [@azisk](https://youtube.com/@azisk)  
Alex Ziskind — distributed MLX demos.

* **@MitjaMartini**: X: [@MitjaMartini](https://x.com/MitjaMartini) · GitHub: [@mitja](https://github.com/mitja) · Website: [mitjamartini.com](https://mitjamartini.com)  
DwarfStar Metal numbers on M3 Ultra. GitHub is a **probable** match — name and `llamatunnel` corroborate, not confirmed.

* **@onthexitter69**: X: [@onthexitter69](https://x.com/onthexitter69) · GitHub: [@onthehub97](https://github.com/onthehub97)  
**ANE offload PR, +33.8% GDN projection.**

* **@angeloskath**: X: [@angeloskath](https://x.com/angeloskath) · GitHub: [@angeloskath](https://github.com/angeloskath) · Website: [angeloskath.github.io](https://angeloskath.github.io/)  
MLX team, Apple.

* **@DiganiJagrit**: X: [@DiganiJagrit](https://x.com/DiganiJagrit) · GitHub: [@jagrit06](https://github.com/jagrit06)  
MLX team, Apple.

* **@trebolloc**: X: [@trebolloc](https://x.com/trebolloc) · GitHub: [@andresy](https://github.com/andresy) · Website: [ronan.collobert.com](https://ronan.collobert.com)  
Ronan Collobert — OG Torch, MLX team, Apple.

* **@bleysg**: X: [@bleysg](https://x.com/bleysg) · GitHub: [@bleys](https://github.com/bleys)  
Mac vs DGX Spark arguments — heat, occasionally light. GitHub is a **probable** match — name and `Auto-GPT` corroborate, not confirmed.

* **@atomic_chat_hq**: X: [@atomic_chat_hq](https://x.com/atomic_chat_hq) · GitHub: [@AtomicBot-ai](https://github.com/AtomicBot-ai) · HF: [AtomicChat](https://huggingface.co/AtomicChat) · Website: [atomic.chat](https://atomic.chat)  
**Atomic Chat — a company, not a person.** A local-first agent over llama.cpp and MLX ([`atomic-agent`](https://github.com/AtomicBot-ai/atomic-agent) 2.5k★, [`Atomic-Chat`](https://github.com/AtomicBot-ai/Atomic-Chat) 1.4k★), an `atomic-quantizer`, a **`llama-cpp-turboquant` nightly fork**, and "Atomic Dynamic" GGUF + MLX quants on Hugging Face. Verified 2026-08-26, their claim: **1-bit Qwen3.8-Flash-Next (79 GB) on an M5 Max 64 GB at 30 tok/s, inside an 8-minute agent loop** — our model, our endpoint, and a memory tier below ours (#20). **Read the quants and the repos, not the feed**: about half the window is rented 4x RTX PRO 6000 demos and product promotion, and the Mac numbers are single vendor demos.

* **@RevTheD3v**: X: [@RevTheD3v](https://x.com/RevTheD3v)  
Posted the only public config that matches our M5 Max fast pick ([post](https://x.com/RevTheD3v/status/2102868037854491105), verified 2026-09-24): Qwen3.8-Flash-Next mixed 4/8 on **mlx-serve 26.9.2**, MLX 0.32.2, MTP + PLD draft 5, 8-bit KV, 10 GB prefix cache, NAX on, OpenCode wired up, 128 GB. He reports **~61 GB loaded**, against our ~100 GiB resident, which is a config question worth a test on its own. One post so far; promote on a second. (#722)

* **@Sean74695991625**: X: [@Sean74695991625](https://x.com/Sean74695991625)  
Publicly asks this repo's question ([post](https://x.com/Sean74695991625/status/2100640297021821352), verified 2026-09-24): how good a local coding agent gets on an M5 Max 128 GB, starting from MTPLX 2.11.3. Low volume. If he publishes results, they are the closest outside comparison we will get. (#722)

* **@ldreisi**: X: [@ldreisi](https://x.com/ldreisi)  
Daily driver per his bio: **M5 Max 128 GB with oMLX + OpenCode**, Qwen Code and Hermes, beside a 4x3090 box. oMLX is unmeasured here (#737), and a user running it under OpenCode on our exact machine is the account most likely to report a real-session problem. (#722)

* **@liuliu**: X: [@liuliu](https://x.com/liuliu)  
Engine maintainer. Argues MLX and llama.cpp are already near the hardware ceiling on Apple silicon ([post](https://x.com/liuliu/status/2102433086491505080), verified 2026-09-24), so the next big step is a new machine. Read him as a **check on claims** that look too good, not as a source of numbers. (#722)

* **Splash users, watched through #531 only**: [@Guikingone](https://x.com/Guikingone) (OpenCode on an M5 Max 64 GB, 40–50 tok/s on a loaded machine) and [@techowknow](https://x.com/techowknow) (Splash-HQ Q8, ~75 tok/s on an M5 Max) are the independent Splash reports from #722. **Not tiered**: Splash is paused and `incoai` is not an approved source, so a tier would suggest the engine is on our path. Record what they post on #531.

* **mlx-community**: HF: [mlx-community](https://huggingface.co/mlx-community)  
Where weights appear first.

---

## DGX Spark sources

**The two-Spark cluster's lane (#307).** Single-Spark testing stopped on
2026-10-23, so judge a lead by *would this change a number on two GB10 nodes?*
The cluster is a multipurpose LLM server and a coding backend served over the
network, so this lane reads NVFP4/FP8 and EXL3 recipes, vLLM, TensorFold and
SGLang serving, and aggregate multi-stream numbers that the Mac lane discards.

**Accounts in the Mac tiers that cross into this lane:**

* **@no_stp_on_snek** (Tier 2) owns a DGX Spark and ships
  `TheTom/llama-cpp-turboquant` with CUDA kernels.
* **@0xSero** (Tier 2): his low-bit writeups target NVIDIA/vLLM, and his 1017
  EXL3 is the two-Spark pick's weights (#1160).
* **@sudoingX** (Tier 2) runs 2x Spark and authors the pick's vLLM stack
  (#1160, #1244).
* **@jayleaton** (Tier 2) publishes GLM-5.3-Flash and DeepSeek-V4.1-Flash
  TensorFold recipes for two Sparks (#798, #919).
* **@ashxhart** (Tier 2) is TensorFold's author.
* **@Brooooook_lyn** (Tier 2) and **@bleysg** (Tier 3) post Mac-vs-Spark
  comparisons.
* **@Raullen** (Tier 2) posted a multi-agent coding session on a 2x Spark
  GLM-5.3-Flash EXL3 stack (#722).

### Vetted: MiaAI-Lab (operator-added, 2026-09-22)

**[@MiaAI_lab](https://x.com/MiaAI_lab) and its GitHub account
[MiaAI-Lab](https://github.com/MiaAI-Lab)** publish maintained 2x and 3-4x DGX
Spark recipes. We have run several end to end (#648, #663, #672, #685, #1168),
so our own rows, not the feed, vet them. Their READMEs carry measured memory
budgets, accurate to within a few GiB on our nodes. Their recipe repos are in
[DGX Spark only](#dgx-spark-only----the-cuda-serving-stack) below.

**They ship new recipe repos often, sometimes several a day.** A new model's
recipe is a new repo, not a commit to a watched one, so a DGX sweep also lists
the account's recently pushed repos:

```sh
gh api "users/MiaAI-Lab/repos?sort=pushed&per_page=15" \
  --jq '.[] | "\(.pushed_at) created=\(.created_at[:10]) \(.name)"'
```

**Caveats.** Their decode and throughput figures are short-prompt serving
numbers, not agent wall time. A departure from their memory settings needs a
measured reason (#672: 0.93 did not fit with DFlash). Their weights are
third-party quants, approved **per artifact**.

### From the 2026-09-24 field scan (#722)

`scripts/verify_posts.py` checked the posts cited here. Verification proves the
author, not the number.

| handle | why | what to read |
|---|---|---|
| [@ViC305](https://x.com/ViC305) | EXL3 recipes with the metric written down ([post](https://x.com/ViC305/status/2102228607494201632)), the MTP collapse past about 164k, 8-bit KV scaling with context, and a SAGE-EXL3 DeepSeek-1017 (#1208). He corrects his own artifacts. | His ExLlamaV3 fork ([vcruz305/exllamav3](https://github.com/vcruz305/exllamav3)) before his feed: recipe-first, high volume. |
| [@yume_arasaki](https://x.com/yume_arasaki) | Owns 2x Spark and **reproduces other people's claims**. He publishes a grid (code, prose, JSON, needle to 243k, tools, J/token) and retracted his own headline ([post](https://x.com/yume_arasaki/status/2101741448811229219)). He also states that prefill, not decode, is the agent bottleneck. | Replications, especially of @ViC305. |
| [@sfxnz](https://x.com/sfxnz) | Author of the second DeepSeek V4.1 Flash EXL3 **2x Spark** recipe (#685). | [sfxnz/DeepSeek-V4.1-Flash-EXL3-vLLM-2x-DGX-Spark](https://github.com/sfxnz/DeepSeek-V4.1-Flash-EXL3-vLLM-2x-DGX-Spark). |
| [@plotarmordev](https://x.com/plotarmordev) | Published a full **GLM-5.3-Flash EXL3 2x Spark long-coding config**, each opt-in with its gain and cost ([post](https://x.com/plotarmordev/status/2102840170672218204)); noted on #648. | The config, when a GLM arm needs tuning. |
| [@AlexanderChopra](https://x.com/AlexanderChopra) | 2x Spark GLM EXL3 memory sweep to 500k input with head `MemAvailable` per step, and the page-cache-vs-CUDA InstantTensor load failure with its eviction fix ([post](https://x.com/AlexanderChopra/status/2102830367119945985)). | Operational posts only; most of the feed is cybersecurity. |
| [@JoesInvestments](https://x.com/JoesInvestments) | Ported GLM-5.3 NVFP4 + DFlash2 to stock vLLM 0.30.0 and published the losses with the wins: all-reduce moves MoE serving about 2%, and 2,304-token prefix blocks miss short agent prompts ([post](https://x.com/JoesInvestments/status/2102862941020176643)). Runs 4 Sparks; most of it transfers. | vLLM-version deltas. |
| [@mrru5s3ll](https://x.com/mrru5s3ll) | Writes the Spark-facing reading of vLLM releases: GB10 FP8 swizzle, NVFP4 default on SM121, Fast Start ([post](https://x.com/mrru5s3ll/status/2102595090934075740)). | Release summaries: a cheap signal that an engine bump matters here. |

**Occasional:** [@mmastrac](https://x.com/mmastrac) and
[@2WildTech](https://x.com/2WildTech) (TP=4 on four Sparks; @2WildTech is
secondhand so far), and [@johncodes](https://x.com/johncodes) (2x Spark
daily-driver anecdotes, no numbers). For llama.cpp's unified-memory mmap fix,
watch [ggml-org/llama.cpp#29030](https://github.com/ggml-org/llama.cpp/pull/29030)
rather than the accounts that pointed at it.

### From the 2026-10-27 contributor scan (#1275)

Found from the contributors and issue/PR authors of the watched DGX repos, a
GitHub search for `dgx spark`, `gb10` and `sm121`, and `xurl` searches on X
(`logs/sweeps/xurl-dgx-sources-20261027T110018Z.txt`).

**An X handle and a GitHub account are paired only where one names the other**:
a GitHub `twitter_username`, an X bio or URL that links the GitHub, or a website
that links both. Where nothing links them, the column is blank, even when a
same-named account exists. `scripts/verify_posts.py` checked the ten cited
posts. Stars and follower counts are as read on 2026-10-27.

**Two-Spark recipe authors** (our topology):

| who | X | GitHub | website | why |
|---|---|---|---|---|
| Joey | [@aijoey](https://x.com/aijoey) | [joeynyc](https://github.com/joeynyc) | [joeyprd.com](https://joeyprd.com/) | Runs frontier open models on 2x Spark and posts configs; ships Spark Doctor and Hermes HUD, and a measured one-Spark MiniMax H3 SM121 recipe ([joeynyc/MiniMax-H3-DGX-Spark](https://github.com/joeynyc/MiniMax-H3-DGX-Spark)). 4,909 X followers. |
| Tech2Wild | [@Tech2Wild](https://x.com/Tech2Wild) | [tonyd2wild](https://github.com/tonyd2wild) | — | [DeepSeek-V4.1-Flash TP2 and TP4](https://github.com/tonyd2wild/DeepSeek-V4.1-Flash-TP2-and-TP4-DGX-Sparks) (150★), [GLM-5.3-Flash NVFP4 + DFlash2 on 2x Spark](https://github.com/tonyd2wild/GLM-5.3-Flash-NVFP4-DFlash2-2x-DGX-Spark) (240★), a DeepSeek-V4 DSpark 2x recipe (494★) and a MiniMax-M3.1 EXL3 TP2. Credits upstream recipes by name ([post](https://x.com/Tech2Wild/status/2114866354612228293)). |
| Luis Catacora | [@lucataco](https://x.com/lucataco) | [lucataco](https://github.com/lucataco) | [lucataco.dev](https://lucataco.dev) | [DeepSeek-V4.1-Flash on 2x Spark](https://github.com/lucataco/DeepSeek-V4.1-Flash-2x-DGX-Spark), the TensorFold TP2 recipe filed as #1163. Models at Cloudflare, before that Replicate; 11,557 X followers. |
| Bertholomus AI | [@bertholomusai](https://x.com/bertholomusai) | [bertholomus](https://github.com/bertholomus) | — | [deepseek-v4.1-tensorfold-tp2-2xgb10](https://github.com/bertholomus/deepseek-v4.1-tensorfold-tp2-2xgb10) (56★, #930) and a Ling-3.1 TP2 port; v0.7.3 doubled to 8 streams, 154 → 168 tok/s, "same replies" ([post](https://x.com/bertholomusai/status/2114078035280597455)). |
| Anders | [@andersjw_](https://x.com/andersjw_) | [ajensenwaud](https://github.com/ajensenwaud) | — | 2x Spark pull-and-run recipes on TensorFold: [MiMo-V2.6](https://github.com/ajensenwaud/MiMo-V2.6-2x-DGX-Spark-TensorFold), [Kolibri-1](https://github.com/ajensenwaud/Kolibri-1-2x-DGX-Spark-TensorFold), plus `spark-bench`. Says an M5 Max vs Spark comparison is next, which would cross both lanes. |
| Wei Wang | [@wei_wang](https://x.com/wei_wang) | [Lesilva](https://github.com/Lesilva) | — | 2x Spark + RTX PRO 6000; [qwen38-flashnext-nvfp4-kv](https://github.com/Lesilva/qwen38-flashnext-nvfp4-kv). Satirized a 702 tok/s, 100%-acceptance claim whose 16 test users each asked the model to print zeros ([post](https://x.com/wei_wang/status/2114917754683678938)): he reads the test script before the number. 7,068 X followers; posts in Chinese. |
| Entrpi | — | [Entrpi](https://github.com/Entrpi) | — | [ds4-on-spark](https://github.com/Entrpi/ds4-on-spark) (411★), [glm-5.3-flash-exl3-2x-spark](https://github.com/Entrpi/glm-5.3-flash-exl3-2x-spark) (43★) and a vLLM branch with GLM-5.3-Flash `glm5_next` and SM121 fixes. GitHub only. |
| raspy135 | — | [raspy135](https://github.com/raspy135) | — | [tideLM](https://github.com/raspy135/tideLM) (26★, #1169): full GLM-5.3 and DeepSeek-1017 on two Sparks by adaptive expert loading. An X @raspy135 exists (created 2026-10-12) but nothing links it. |
| ciprianveg | [@ciprianveg](https://x.com/ciprianveg) | [ciprianveg](https://github.com/ciprianveg) | — | [gb10-vllm](https://github.com/ciprianveg/gb10-vllm) (67★: Kimi-K3 B12X_MLA + DSpark, GLM), [gb10-glm-5.2](https://github.com/ciprianveg/gb10-glm-5.2) (39★) and [gb10-tensorfold](https://github.com/ciprianveg/gb10-tensorfold) (GLM-5.3 EXL3, world 8). His X bio links gb10-vllm. |
| Craig Petronella | [@PetronellaTech](https://x.com/PetronellaTech) | [capetron](https://github.com/capetron) | [petronellatech.com](https://petronellatech.com) | [gb10-cluster-guide](https://github.com/capetron/gb10-cluster-guide) (two nodes, then a three-node ring) with `gb10-cluster-bench` and `gb10-cluster-check`, and a Qwen3.8-Flash-Next dual-Spark repo. Cluster operations, the part of #307 the recipes skip. |

**Engines, kernels and tooling that two-Spark recipes build on:**

| who | X | GitHub | website | why |
|---|---|---|---|---|
| eugr | — | [eugr](https://github.com/eugr) | — | [spark-vllm-docker](https://github.com/eugr/spark-vllm-docker) (2,450★: vLLM on dual DGX Sparks; the most-starred Spark repo the search found, NVIDIA's playbooks included), [llama-benchy](https://github.com/eugr/llama-benchy) (764★), and 95 commits to NVIDIA Flare. The @eugr on X has 2 posts and nothing links it. |
| Spark Arena | [@spark_arena](https://x.com/spark_arena) | [spark-arena](https://github.com/spark-arena) | [spark-arena.com](https://spark-arena.com) | A community org, not a person: [sparkrun](https://github.com/spark-arena/sparkrun) (551★) launches and stops Spark inference workloads, and [recipe-registry](https://github.com/spark-arena/recipe-registry) (66★) collects recipes. A place to catch new recipes before their authors are known. |
| Local Inference Lab | — | [local-inference-lab](https://github.com/local-inference-lab) | — | Its [vLLM fork](https://github.com/local-inference-lab/vllm) (b12x) is what Spark recipes build against (`mlow/vllm-gb10`), with [llm-inference-bench](https://github.com/local-inference-lab/llm-inference-bench) (121★). A Hugging Face org of the same name published the GLM-5.3-Flash NVFP4-MXFP8 checkpoint in the Flare post (2026-10-22 sweep); nothing yet links the two. |
| mr-r0b0t | [@mr_r0b0t](https://x.com/mr_r0b0t) | [r0b0tlab](https://github.com/r0b0tlab) | [HF r0b0tlab](https://huggingface.co/r0b0tlab) | SM121 builds: [GLM-5.3-Flash EXL3 + DFlash2](https://github.com/r0b0tlab/glm53-flash-exl3-dflash2-sm121) with fused EXL3 MoE kernels, [MiMo-V2.6 NVFP4 on 2x GB10](https://github.com/r0b0tlab/mimo26-nvfp4-sm121), MiniCPM-V EXL3, and `r0b0bench`. 11,574 X followers. |
| Jürgen Schmied | — | [jschmied](https://github.com/jschmied) | — | The most active TensorFold issue author (62) with 187 commits, and [qwen38-flash-next-gb10](https://github.com/jschmied/qwen38-flash-next-gb10) (21★). The X @jschmied is someone else. |
| BadAd84 | — | [BadAd84](https://github.com/BadAd84) | — | The most active outside author on MiaAI-Lab's repos (26 issues and PRs); wrote the DSA prompt indexer in CUDA for the GLM zig engine (MiaAI PR #231). |
| ビーズちゃん (Bizuayeu) | [@Bizuayeu](https://x.com/Bizuayeu) | [Bizuayeu](https://github.com/Bizuayeu) | [meguru-construction.com](https://meguru-construction.com/) | 111 commits to TensorFold, [GLM-5.3-Flash-DGX-Sparks-BIZ](https://github.com/Bizuayeu/GLM-5.3-Flash-DGX-Sparks-BIZ), and the `GLM-5.3-Flash-XSFP4` quant on Hugging Face. Posts the full stack with links ([post](https://x.com/Bizuayeu/status/2114490243244261509)); in Japanese. |
| Hector Isaza | [@hecisaza](https://x.com/hecisaza) | [hecisaza](https://github.com/hecisaza) | [networkingdev.com](https://networkingdev.com) | 23 issues and PRs on MiaAI-Lab's repos, and a repo that takes the GLM-5.3 EXL3 TensorFold recipe from two Sparks to three. |
| Techno Tim | [@TechnoTimLive](https://x.com/TechnoTimLive) | [timothystewart6](https://github.com/timothystewart6) | [technotim.com](https://technotim.com) | [vllm-gb10](https://github.com/timothystewart6/vllm-gb10) (57★) and [ubuntu-gb10](https://github.com/timothystewart6/ubuntu-gb10), an Ubuntu 24.04 + NVIDIA stack guide for GB10. A general homelab channel (28,287 X followers), so read the repos first and the feed second. |
| Sebastian Christiansen | [@CerebralCoding_](https://x.com/CerebralCoding_) | [CerebralCoding](https://github.com/CerebralCoding) | [cerebralcoding.com](https://cerebralcoding.com) | 93 commits to TensorFold; also contributes to mlx-serve and ds4, so his posts cross both lanes. |

**Measurers** (they publish the method beside the number):

| who | X | GitHub | website | why |
|---|---|---|---|---|
| Niklas Lenz | [@niklaslenz_ai](https://x.com/niklaslenz_ai) | — | [niklaslenz.com](https://niklaslenz.com) | **Agent wall time, not tok/s**: one prompt, one try, timed to a finished build. DeepSeek-V4.1-Flash-1017 built the same voxel world in 45.7 min on one Mac Studio, 88.1 min on two Sparks and 49.7 min on four ([post](https://x.com/niklaslenz_ai/status/2114658298851205185)). Owns 4 Sparks, an M5 Ultra and a 5090, so both lanes. The closest method to ours we have found on X. |
| Gael | [@Gael_DDOS](https://x.com/Gael_DDOS) | — | — | Runs TensorFold, Flare and vLLM side by side on one Spark: under batched long prompts vLLM answers mid-prefill, while the other two read every prompt first ([post](https://x.com/Gael_DDOS/status/2114809347499086335)). Spec-decode experiments. |
| Steven Kramp | [@SelfHostedSI](https://x.com/SelfHostedSI) | — | [stevenkramp.de/lab](http://stevenkramp.de/lab) | Measures vLLM proposals on one Spark and reports what did not improve: 12-bit BF16 storage gave about 11% faster decode, but a cold 19K-token prompt still took 7.6–7.7 s to first token ([post](https://x.com/SelfHostedSI/status/2114801752784748907)). Publishes raw data. |

**Four Sparks or more** (not our topology, but the engine work transfers):

| who | X | GitHub | website | why |
|---|---|---|---|---|
| Kevin Theo | [@kevintheoo](https://x.com/kevintheoo) | [kevintheo-ai](https://github.com/kevintheo-ai) | [kevintheo.com](http://kevintheo.com) | 4x Spark switchless ring; compares engines at fixed streams: TensorFold 90.6 vs MiaAI's SGLang line 90.5, stock SGLang 61.2 ([post](https://x.com/kevintheoo/status/2115024623867715652)). Repo `DeepSeek-v4.1-Flash-DGX-Sparks`. |
| Zbigniew Majewski | [@majewskizby](https://x.com/majewskizby) | [knapcio](https://github.com/knapcio) | — | [DeepSeek-V4.1-Flash 4x TP4](https://github.com/knapcio/DeepSeek-V4.1-Flash-4x-DGX-Spark-TP4) (81★) and GLM-5.3 on the `gr8` engine at TP4 and TP6, but also **two-Spark runs with the one-Spark baseline beside them** (MiniMax H3: 643 s against 1,399 s, [post](https://x.com/majewskizby/status/2115014189316002260)). |

**Considered, not added:** @hisxo (`DeepSeek-V4.1-Flash-2x-DGX-Spark`, one repo so far), ZackO2o (a TensorFold TP2 recipe, no other output), nkavassalis (`spark-tuning`, no stars yet), @voipmonitor (Martin Vit, 120 Flare commits; the X account is his company's), lukealonso (Flare's top committer; watch the repo, not a person), @tenhkspark, @HandwerkAiLab, @latentone_ and @dextune (thin so far). Re-check these at the next contributor scan.

### Vendor sources for this lane (operator-defined, 2026-09-17)

The DGX Spark runs NVIDIA's own formats, so NVIDIA is a **primary** source here
rather than background noise — the opposite of its standing in the Mac lane.
Each account below was checked to exist on 2026-09-17 (`api.fxtwitter.com`,
follower counts as read that day); a post from any of them is still gathered
and verified like any other.

| source | surface | why it is in this lane |
|---|---|---|
| [`huggingface.co/nvidia`](https://huggingface.co/nvidia) | Hugging Face org | Nemotron and the NVFP4/FP8 builds a CUDA-native engine can load. Already swept: `hf_sweep.py --profile gb10` watches `author=nvidia`. |
| [`huggingface.co/RedHatAI`](https://huggingface.co/RedHatAI) | Hugging Face org | Red Hat requantizes the NVIDIA models for vLLM, and the builds get used: `NVIDIA-Nemotron-3.5-Lightning-30B-A3B-FP8` read **257,782 downloads** on 2026-09-17, against low hundreds for the community NVFP4/W4A16 builds of the same weights. Added to the `gb10` watch list. |
| [@NVIDIAAI](https://x.com/NVIDIAAI) | X (340,435 followers) | the operator's named account: model and recipe announcements, including the DGX Spark recipes. |
| [@NVIDIAAIDev](https://x.com/NVIDIAAIDev) | X (111,556) | the developer-facing half: serving flags, container tags, kernel notes. |
| [@nvidia](https://x.com/nvidia) | X (2,663,678) | the corporate feed; lowest signal per post, read last. |
| [@NVIDIADeveloper](https://x.com/NVIDIADeveloper) | X (6,194) | small and occasional; keep until a sweep shows it adds nothing over @NVIDIAAIDev. |
| the model card in the HF repo | web | NVIDIA ships its **Spark-specific** serve command in the card, not in a repo — the `vllm/vllm-openai` container tag, the `VLLM_*` env vars and the `--speculative-config` for the MTP head all came from the Nemotron-3-Super card (#406). Read the card, not just the release note. |

**The filter still applies.** A vendor account is a marketing feed with recipes
in it: take the flags, the container tags and the model links, and treat every
throughput number as a lead to measure here. NVIDIA's own numbers are quoted
from 4× GB300 or a B200 more often than from one Spark.

---

## Read the repo, not the feed

For these sources the work is in a repository or an issue tracker, and the
feed is chatter. Both have X accounts; check GitHub first and the feed second.

* **Flor1an-B**: GitHub: [@Flor1an-B](https://github.com/Flor1an-B) · [ds4 issues](https://github.com/antirez/ds4/issues?q=author%3AFlor1an-B) · X: [@_LEFBE](https://x.com/_LEFBE) · Repo: [Ka1zen](https://github.com/Flor1an-B/Ka1zen)  
Bertaux Florian, Paris; his X profile links `Flor1an-B/Ka1zen`, which ties the two accounts. **15 authored issues and PRs on `antirez/ds4`, all engine internals**: [#789](https://github.com/antirez/ds4/pull/789) (visible-KV checkpoint fixes for tool turns, ds4#816), [#691](https://github.com/antirez/ds4/issues/691) (KV cache reuse breaking for tool clients that do not replay reasoning), [#695](https://github.com/antirez/ds4/issues/695) (DSpark break-even ignores replay cost), [#750](https://github.com/antirez/ds4/issues/750) (native MTP corrupts output at `--mtp-draft>=2`, #39). **Benchmarks DeepSeek-V4-Flash 0731 on an M5 Max 128 GB**, our machine; #75 came from his temp>0 DSpark table. The feed is mostly replies to @antirez and @ivanfioravanti. 3 GitHub followers and 51 on X: the popularity rule below is about him.

* **Mirai (trymirai)**: X: [@trymirai](https://x.com/trymirai) · GitHub: [trymirai/uzu](https://github.com/trymirai/uzu) · Website: [trymirai.com](https://trymirai.com) · [docs](https://docs.trymirai.com) · PyPI: [uzu](https://pypi.org/project/uzu/)  
**uzu, an Apple-only inference engine written in Rust** (MIT). **Reachable by `uv add uzu`**, with Python, TypeScript and Swift bindings, so it is testable without a build. The claim that brought it here (105 tok/s on a 27B on an M5 Max, 2x MTPLX) is unsourced and secondhand (#134, closed as a stale lead). An aggregator account found it; no sweep surface we run would have. Open question before a measurement: does "M5 Neural Accelerators" mean the ANE (#123) or the GPU matmul units? The company account is a Tier 2 entry above.

## Repositories to watch

**Every repo this project depends on, in one place**, tagged by machine (#307).
X is where the field announces itself; GitHub is where it ships. A sweep from
memory once missed two repos because this file linked authors' profiles, not
their code.

Run it rather than reading it. `--platform` picks a lane -- the Mac's Metal and
MLX engines, the DGX Spark's CUDA serving stack, or both:

```sh
uv run python scripts/upstream_sweep.py --hours 24                   # both lanes
uv run python scripts/upstream_sweep.py --hours 168 --quiet-empty    # a week
uv run python scripts/upstream_sweep.py --hours 24 --platform dgx    # DGX only
```

The script's `WATCHED` dict is the source of truth, and
`benchmarks/agent/test_sources.py` fails if this list drifts from it. It reports
releases and commit subjects, and it says explicitly when a repo is
**unreachable** -- a renamed or private repo otherwise looks exactly like a
quiet one, and "nothing happened upstream" is the wrong conclusion to draw from
an auth failure.

### Both machines

The engine runs on the Mac and on the DGX, or the repo belongs to no machine.

* **[`antirez/ds4`](https://github.com/antirez/ds4)**  
DwarfStar: the M5 Max's DeepSeek-V4-Flash and lighter Flash-Next picks

* **[`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp)**  
the mainline engine pick; `qwen4exp` IS Qwen3.8-Flash-Next

* **[`ollama/ollama`](https://github.com/ollama/ollama)**  
the 31 GB entry point; MLX runtime on the Mac, cuda_v13 on the DGX

* **[`anomalyco/opencode`](https://github.com/anomalyco/opencode)**  
our only client

* **[`evanwtf/local-llm`](https://github.com/evanwtf/local-llm)**  
this project

* **[`evanwtf/gmail-archive`](https://github.com/evanwtf/gmail-archive)**  
the excision tasks' target repository

### Mac only -- Metal and MLX

These have no meaning on the DGX: MLX ships no Linux arm64 runtime (#293), and
Metal is Apple-only.

* **[`ml-explore/mlx`](https://github.com/ml-explore/mlx)**  
the framework everything MLX sits on

* **[`ml-explore/mlx-lm`](https://github.com/ml-explore/mlx-lm)**  
reference MLX server; new architectures land here first

* **[`jundot/omlx`](https://github.com/jundot/omlx)**  
oMLX -- prefill leader, unmeasured here (#737)

* **[`ddalcu/mlx-serve`](https://github.com/ddalcu/mlx-serve)**  
the M5 Max's fastest pick; its author benchmarks on our machine

* **[`beamivalice/sushi`](https://github.com/beamivalice/sushi)**  
Sushi, the mlx-serve fork with EXL3 Flash-Next packs; our fastest Mac stack (#749)

* **[`youssofal/MTPLX`](https://github.com/youssofal/MTPLX)**  
MTP speculative decoding; we hold one unreplicated number

* **[`raullenchai/Rapid-MLX`](https://github.com/raullenchai/Rapid-MLX)**  
the one MLX engine reachable by pip (#57, #60)

* **[`ARahim3/mlx-dspark`](https://github.com/ARahim3/mlx-dspark)**  
DSpark/DFlash ported to MLX (#19, #58, #75)

* **[`Blaizzy/mlx-vlm`](https://github.com/Blaizzy/mlx-vlm)**  
expert offloading, prefix caching, Qwen3.8-Flash-Next MTP

* **[`unslothai/llama.cpp`](https://github.com/unslothai/llama.cpp)**  
the fork with a working qwen4exp MTP graph (#77)

* **[`Layr-Labs/mlxfast-gemma4-26b-a4b-engine`](https://github.com/Layr-Labs/mlxfast-gemma4-26b-a4b-engine)**  
MLX Fast leaderboard harness (#80)

* **[`sudoingX/qwen38-mtp`](https://github.com/sudoingX/qwen38-mtp)**  
61 paired baseline-vs-MTP runs, disciplined method (#19, #39)

* **[`trymirai/uzu`](https://github.com/trymirai/uzu)**  
Apple-only Rust engine, reachable by pip; claims 2x MTPLX (#134)

### DGX Spark only -- the CUDA serving stack

The path to NVFP4/FP8 on Blackwell that Ollama on Linux cannot reach (#293),
plus the two-Spark recipes we run. The cluster's emphasis is aggregate
multi-stream throughput (#307).

* **[`vllm-project/vllm`](https://github.com/vllm-project/vllm)**  
NVFP4/FP8 serving on Blackwell; the two-Spark pick's engine (#1160)

* **[`NVIDIA/TensorRT-LLM`](https://github.com/NVIDIA/TensorRT-LLM)**  
the other CUDA-native path to NVFP4 on the Spark (#293, #299)

* **[`NVIDIA/flare-local-inference`](https://github.com/NVIDIA/flare-local-inference)**  
NVIDIA Flare: a Rust local server for SM121, built for coding agents (#1207)

* **[`sgl-project/sglang`](https://github.com/sgl-project/sglang)**  
aggregate multi-stream serving; the DGX's throughput emphasis (#307)

* **[`ashhart/TensorFold`](https://github.com/ashhart/TensorFold)**  
the engine under the cluster's leading GLM arms (#892, #897)

* **[`sudoingX/dgx-spark-deepseek-v41`](https://github.com/sudoingX/dgx-spark-deepseek-v41)**  
the two-Spark pick's stack: DeepSeek-1017 EXL3 on vLLM (#1160, #1244)

* **[`MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks`](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks)**  
DeepSeek-1017 EAQ on TensorFold, the best hidden re-grade (#1168, #1246)

* **[`MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold)**  
the two-Spark fast option's recipe: GLM-5.3-Flash EXL3 on TensorFold (#892)

* **[`MiaAI-Lab/Qwen3.8-Flash-Next-Dual-DGX-Sparks`](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Dual-DGX-Sparks)**  
the Qwen3.8-Flash-Next two-node recipe we run (#663, #664)

* **[`MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks)**  
the GLM-5.3-Flash EXL3 two-node recipe we run (#648)

* **[`MiaAI-Lab/MiMo-V2.6-Flash-2x-DGX-Sparks`](https://github.com/MiaAI-Lab/MiMo-V2.6-Flash-2x-DGX-Sparks)**  
the MiMo-V2.6-Flash SGLang two-node recipe we run (#672)

* **[`MiaAI-Lab/DeepSeek-v4.1-Flash-EXL3-2x-DGX-Sparks`](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-EXL3-2x-DGX-Sparks)**  
DeepSeek V4.1 Flash EXL3 on two Sparks, measured (#685)

* **[`jayleaton/glm53-tensorfold-spark`](https://github.com/jayleaton/glm53-tensorfold-spark)**  
GLM-5.3-Flash on TensorFold, a kept arm (#840)

* **[`jayleaton/deepseek-v41-tensorfold-spark`](https://github.com/jayleaton/deepseek-v41-tensorfold-spark)**  
DeepSeek-V4.1-Flash on TensorFold, measured (#919)

* **[`kindlingai/glm-5.3-flash-gx10`](https://github.com/kindlingai/glm-5.3-flash-gx10)**  
GLM-5.3-Flash NVFP4 on vLLM, cut by the screen (#896)

**Read commits, not activity counts.** A branch can be busy with vision and
ROCm work that is out of scope here, and a two-commit day can carry the one
change that moves our numbers. This is already recorded as a trap.

## How to run it

**Use the `/source-sweep` skill.** It covers seven surfaces in order — GitHub
inbox, watched repos, branches, upstream issues and PRs, Hugging Face, project
websites, then X — and encodes the order below. What follows is the detail behind it.

### X: gather, judge, file, then verify

**The order is the point.** Verification cost scales with the number of
*relevant* leads, not with the volume grok returns; verifying everything spends
most of the effort on CUDA benchmarks and vision releases.

1. **Gather with grok, and assume every word is unverified.**
2. **Judge relevance to the lane first**: would it change a number on the M5
   Max (Metal), or on the two-Spark cluster (CUDA)? A result on an M3 or M4 is
   a **lead, not noise**, and a result on an M5 Ultra is the closest lead of
   all: same GPU generation, so its kernel work usually reaches the M5 Max.
3. **Say what you found, out loud, before filing anything.** A short summary
   leading with what bears on this machine, marked unverified. A sweep whose
   output only lands in GitHub is one the operator cannot steer.
4. **File or update an issue in our own repo, marked `Unverified`**, with the
   handle and UTC timestamp. Doing it before verifying means the reasoning
   about relevance gets written down while it is fresh.
5. **Then verify, only the posts that earned an issue**:
   `uv run python scripts/verify_posts.py <url-or-id> ...`
6. **Record the result on the issue.** A claim that fails verification is
   itself a finding about the source — note it, do not delete it.

`/grok` reads X. `WebFetch` on an `x.com` URL fails — it returns HTTP 402, not a
login page, so the failure looks like a billing problem and is not one.

**Two tools, two jobs.** `/grok` *searches* — a week of accounts, or a topic.
The **fixers** (`fixupx.com`, `vxtwitter.com`) *read one post* whose URL you
already have. Reach for a fixer whenever someone hands you a link: it is exact,
with no model in the loop to paraphrase or invent.

From an agent, call the API host with curl rather than pointing `WebFetch` at
the fixer — `api.fxtwitter.com` is the same service `scripts/verify_posts.py`
uses, and it returns the **quoted post**, which is often where the substance is:

```sh
curl -s "https://api.fxtwitter.com/status/<POST_ID>" -H 'User-Agent: curl/8'
```

**Why not `WebFetch` on `fixupx.com`.** The fixers serve their embed only to bot
user-agents. `WebFetch` sends a browser one, so `fixupx.com` answers `302` back
to `x.com` and `vxtwitter.com` answers `403` — neither is a sign the post is
gone. In a browser, or with curl and a bot user-agent, both work fine.

Ask for a **structured summary per account**, not a transcript — six accounts over
a week is a firehose. One call with several questions beats many small calls
(each run is 30–180 s). Set the Bash timeout to `400000` and pass
`GROK_CLAUDE_SKILLS_ENABLED=false`.

**Never use `--json-schema`.** It makes grok skip the search and invent posts —
verified twice, once returning a fabricated status ID `1900000000000000000`.

A prompt shape that works:

> Search X for posts and replies from @antirez, @ivanfioravanti, @ddalcu,
> @Spangler3000 and @jundotkim in the last 7 days. For each: UTC timestamp,
> post or reply, full text, post URL. I care about Apple Silicon / Metal local
> inference, MLX, oMLX, mlx-serve, Rapid-MLX, llama.cpp, DwarfStar/ds4,
> quantization recipes, decode or prefill numbers, MTP or speculative decoding,
> and coding agents. Also list every other handle they mention or reply to, with
> one line on what that handle works on. If an account has nothing in the window,
> say so plainly rather than padding with older posts.

## Rules, all learned the hard way

**Verify before repeating.** Run `scripts/verify_posts.py` on every post you
repeat. It checks that the post exists, its real timestamp, its true author,
and whether it is a post or a reply. It has caught a "post" that was a reply,
and grok has fabricated an item outright. **A claim with no post URL is
unusable.**

**Date and version every claim.** #55: blog sources describing OpenCode looked
authoritative and covered **1.1.x–1.14.x** while we ran **1.18.25**. Six months
is several different products in this field.

**These are leads, not results — and "not our hardware" is not a reason to
discard one.** Most developers building these engines are on M3/M4, so a
kernel, a flag or a scheduling change that wins there is the most likely source
of a win here: the mechanism usually transfers even when the number does not.
Say so in the entry rather than writing it off. What does not transfer is
narrower than it looks:

- **A configuration that will not fit.** 165 GB of active memory is unavailable
  in 128 GB. Ask which quant does fit; do not drop the engine.
- **The absolute number.** #58 showed ~4% of throughput moves with thermal
  state alone, and #23 puts a 3-trial task median at ±28%. Quote ratios and
  rankings, not other people's absolutes.
- **CUDA/ROCm kernels, on the Mac lane.** The quantization reasoning transfers;
  the kernels do not.
- **Decode and prefill rates as agent speed.** This project has measured three
  times that a headline rate does not predict agent wall time.

Per #59, nothing from here enters a `RECOMMENDATIONS.md` without our own
controlled measurement. That is a bar for publishing, not a filter for what is
worth reading.

**Ask for the disconfirming cases explicitly** — headless/CI use, local models,
edits failing to apply. General sentiment will not surface them. OpenCode is
widely liked and still failed here, and **not one external source mentioned the
headless problem** because every author was using the TUI (#54).

**Popularity is not the property we need.** Flor1an-B has three GitHub
followers and produced the only DSpark measurement at real sampling on our
exact machine and model (#75). In the 2026-09-06 batch the two closest
accounts had under 200 followers each, and the three largest were all rejected.
**Judge a source by whether its claims are checkable and whether it runs
hardware like ours, not by reach.**

**Pair identities only on evidence.** Link a GitHub or HF account to an X
handle only when one names the other, or a fork or tool ties them (see
@13scoobie). A matching name is not enough.

**A gather is not evidence of a quiet field until the balance is checked.**
On 2026-09-05 a gather returned one post from seven accounts, and the next call
failed with `402 Payment Required: Grok Build usage balance exhausted`: quota,
not quiet. Read the balance before the X surface and record it. A 402 announces
itself; a degraded answer does not.

## Weight the gather by volume, not by follower count

The X gather returns a bounded amount of text for a fixed window. A prolific
account fills that budget and crowds out the low-volume accounts, which often
hold the specific numbers: @0xZKnw and @korale77 are entries the sweep must not
lose. Use either mitigation:

- **Cap per account.** Ask for at most 2-3 items per handle.
- **Split the query.** Gather high-volume accounts in their own call, so a
  quiet week from the small accounts is legible instead of invisible.

**@mweinbach and @no_stp_on_snek are the high-volume accounts.** Name any later
high-volume addition here, so the reason survives.

## Considered and not added

Each was checked for 30 days of X activity, a confirmed GitHub or HF identity,
and the share of output on our hardware (#152, 2026-09-06). Do not re-propose
them without new evidence. The 2026-10-27 DGX rejections are listed under
[that scan](#from-the-2026-10-27-contributor-scan-1275).

* **@Badtheorylabs**: no Apple-silicon content on X; the runtime figure is an
  RTX PRO 6000.
* **@OrcaRouter**: a hosted API router; its 4-bit build needs 200 GB.
* **@trycua**: llama.cpp Metal inside Apple-silicon VMs; virtualization is not
  our regime.
* **@lmstudio**: the feed is mostly cloud agent news; we run neither the app
  nor its engine.
* **@sanchitmonga22**: mostly phone NPU/ANE and cloud agents (#123 holds the
  ANE question).
* **@glaforge**: no MLX, Metal, llama.cpp or Apple-silicon posts.

Seven Tier 2 accounts (@ggerganov, @thdxr, @trymirai, @UnslothAI, @YJesus,
@Eschalabs, @mweinbach) came from grok recommendations on one verified post
each, not from a person reading the feed (#152). Demote any that does not earn
its place.

**Repos considered for the watch list, not added:**
[`TheTom/llama-cpp-turboquant`](https://github.com/TheTom/llama-cpp-turboquant),
[`0xZKnw/mlxl3`](https://github.com/0xZKnw/mlxl3) and
[`Eschalabs/escha-mlx`](https://github.com/Eschalabs/escha-mlx). A `WATCHED`
entry adds cost to every sweep, so decide each one on its own.
