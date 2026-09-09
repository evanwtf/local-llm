# Who to check on X

**Use:** *"Check our influencer list for updates over the past week."*

That means: run the sweep in [How to run it](#how-to-run-it) below, verify every
post before repeating it, and report what changed — not who said what.

This file exists because the Apple-Silicon local-inference field moves faster
than this project measures. Two engines shipped double-digit improvements in a
48-hour window while our own docs still described a three-engine world (#60).

---

## Tier 1 — check every time

* **@antirez**: X: [@antirez](https://x.com/antirez) · GitHub: [@antirez](https://github.com/antirez) · Website: [invece.org](http://invece.org)  
DwarfStar / ds4 author  
**Our primary engine.** Ships models on preview branches and announces them here before the README catches up. `glm-5.3-flash` was on X days before it was documented.

* **@ivanfioravanti**: X: [@ivanfioravanti](https://x.com/ivanfioravanti) · GitHub: [@ivanfioravanti](https://github.com/ivanfioravanti)  
daily benchmarker, M3 Ultra / multi-Mac  
Highest-volume tester on Apple Silicon. #51 came from one of his posts: Q8_0→Q4_K attention+head, **+12.6% decode with a quality gain**.

* **@ddalcu**: X: [@ddalcu](https://x.com/ddalcu) · GitHub: [@ddalcu](https://github.com/ddalcu) · Website: [mlxserve.com](http://mlxserve.com) · [dalcu.com](http://www.dalcu.com)  
mlx-serve, `llmprobe`  
**Benchmarks on a 1x M5 Max 128 GB laptop — our exact machine.** Ported ds4's evals into a cross-engine harness (`npx llmprobe --eval`).

* **@Beamsters1**: X: [@Beamsters1](https://x.com/Beamsters1) · GitHub: **very likely** [@beamivalice](https://github.com/beamivalice) — see below  
mlx-serve qwen4 performance contributor  
**Ships the qwen4 optimizations for our primary model on our co-primary engine.** As `beamivalice`, the 4th-ranked human contributor to mlx-serve (11 commits behind ddalcu's 288), and every recent PR is `perf(qwen4)`: split-K QSA, SSD-first cache admission, MTP/QSA history rollback without copies, allocator-pool reuse between prefills. That is the exact surface [#225](https://github.com/evanwtf/local-llm/issues/225) measures — and concretely, **8 of the 24 commits separating our test build from the shipped v26.9.1 are theirs** (#350, #352, #363, #370, #377, #379, #381, #383), against 10 from ddalcu himself.

  **Told us to build a PR before we knew we needed it.** 2026-09-08 06:59:58Z, verified: *"If you are going to daily drive MLX-serve's Qwen3.8 Flash Next, make sure to include PR383 from main. (and perhaps many more since we are starting the big bug hunts)"* — 7 likes, 789 views. We had benchmarked a five-day-old release in #191 while main was being repaired; that post put main+383 on the machine within hours.

  **The GitHub identity is inferred, not confirmed — read the evidence and judge it yourself.** Neither profile links to the other: the X bio is "Beam" with no website, and the GitHub account has no name, bio, blog, or `twitter_username`. What connects them is circumstantial and same-day: **@beamivalice authored PR383**, the exact PR the post promotes; the commit email is `my.beam@gmail.com`, carrying the same distinctive token as "Beamsters"; and the post says "**we** are starting the big bug hunts", which is a maintainer's first person, not a user's. This is an evidence chain, **not** the matching-name guess the rules below forbid — but it is still inference. Do not cite it as established. If a post ever links the two directly, replace this paragraph with the verification.

  139 followers, 215 posts, Bangkok — low volume, so the cap-per-account rule below is what keeps this account from being crowded out of a gather.

* **@Spangler3000**: X: [@Spangler3000](https://x.com/Spangler3000) · GitHub: [@jonathan308](https://github.com/jonathan308)  
oMLX Metal kernels (QSA, DFlash2)  
Densest Metal-kernel signal in the field. His PRs are why oMLX leads on prefill — **and prefill is our bottleneck** (#14, #50, ds4#816).

* **@jundotkim**: X: [@jundotkim](https://x.com/jundotkim) · GitHub: [@jundot](https://github.com/jundot) · Website: [omlx.ai](https://omlx.ai)  
oMLX author  
oMLX 0.6.4: PP 32k **834 → 1114 tok/s**, TG **40 → 46**. Untested here (#60).

* **@ggerganov**: X: [@ggerganov](https://x.com/ggerganov) · GitHub: [@ggerganov](https://github.com/ggerganov)  
llama.cpp / ggml author  
**We run llama.cpp and its author was not on this list.** Ships backend releases and MTP serve flags — #77 is blocked on mainline having a `qwen4exp` MTP flag with no graph behind it, which is exactly his surface. Verified post 2026-09-01.

* **@korale77**: X: [@korale77](https://x.com/korale77) · GitHub: [@korale77](https://github.com/korale77) · [@eclaire-labs](https://github.com/eclaire-labs) · Website: [eclaire.co](https://eclaire.co)  
ds4 on an M5 Max 128 GB  
**Our engine, our machine, our models.** The only account posting ds4 numbers on an M5 Max 128 GB other than Flor1an-B below, and unlike him he posts them on X rather than in ds4 issues. Verified 2026-09-01: GLM-5.3-Flash Q2 on **ds4 with vision, M5 Max 128 GB**, `ctx=120128 ... avg=17.32 t/s` on Auto power. Verified 2026-09-03: DeepSeek-V4-Flash Vision **18–20 t/s at 200k context**. Verified 2026-08-19: Qwen3.8-27B 4-bit + DFlash2 at **68–89 t/s**, depending on thinking level. His GitHub holds a **`ds4` fork pushed 2026-09-06** and [`mlx-vlm-kv-bench`](https://github.com/korale77/mlx-vlm-kv-bench), independent benchmarks of TurboQuant and TriAttention KV-cache work in MLX-VLM. **Single runs, and "Auto power" is an uncontrolled thermal state** — #58 puts ~4% of throughput on thermal state alone, so these are leads to re-measure, not baselines. ~6 GitHub followers, ~190 on X: exactly the account the volume rule below exists to protect.

## Tier 2 — check weekly

* **@Youssofal_**: X: [@Youssofal_](https://x.com/Youssofal_) · GitHub: [@youssofal](https://github.com/youssofal)  
MTPLX — MTP speculative decoding, custom Metal kernels. We hold an old, unreplicated MTPLX number marked provisional.

* **@Raullen**: X: [@Raullen](https://x.com/Raullen) · GitHub: [@raullenchai](https://github.com/raullenchai)  
Rapid-MLX (#57). Apache 2.0, OpenAI-compatible, claims 12x prefix cache.

* **@rapidmlx**: X: [@rapidmlx](https://x.com/rapidmlx) · Website: [rapidmlx.com](https://rapidmlx.com) · GitHub: [raullenchai/Rapid-MLX](https://github.com/raullenchai/Rapid-MLX)  
**The project account for Rapid-MLX**, distinct from @Raullen above — it ships the release notes, so it is the one to watch for versions. Releases are frequent: 0.13.2 on 2026-08-31, **0.13.3 on 2026-09-01** adding native GLM-5.3-Flash. **Read the memory line before the speed line**: 0.13.3's headline is a 4-bit GLM-5.3 checkpoint using **165 GB of active memory, stated for 192 GB+ Macs**. That specific configuration will not load in 128 GB — so the actionable question is whether the engine serves a GLM-5.3 quant that fits here, which is worth finding out. **The engine is a live lead; only that one configuration is out of reach.** #57, #60.

* **@awnihannun**: X: [@awnihannun](https://x.com/awnihannun) · GitHub: [@awni](https://github.com/awni) · Website: [awnihannun.com](https://awnihannun.com/)  
Co-created MLX. Lower volume since leaving Apple, still framework source-of-truth.

* **@zcbenz**: X: [@zcbenz](https://x.com/zcbenz) · GitHub: [@zcbenz](https://github.com/zcbenz) · Website: [zcbenz.com](https://zcbenz.com)  
mlx-lm maintainer.

* **@N8Programs**: X: [@N8Programs](https://x.com/N8Programs) · GitHub: [@N8python](https://github.com/N8python)  
Quant conversions, custom kernels, training on Silicon.

* **@Prince_Canuma**: X: [@Prince_Canuma](https://x.com/Prince_Canuma) · GitHub: [@Blaizzy](https://github.com/Blaizzy) · Repo: [mlx-vlm](https://github.com/Blaizzy/mlx-vlm)  
mlx-vlm, mlx-audio. **Filed here as multimodal, which is now the wrong reason to skip him.** `mlx-vlm v0.7.0-rc0` (2026-09-01) ships three things that are not about vision at all: **expert offloading**, running larger MoE models straight from disk; a redesigned **prefix cache** claiming up to **166x end-to-end on Gemma 4 31B** from RAM and 96.6x from SSD after a restart; and **Qwen3.8-Flash-Next with MTP**, which is our fast pick and the thing mainline llama.cpp has a flag for and no graph behind it (#77). **Read the 166x carefully** -- it is a cache-hit number, so it measures repeated prefixes rather than first-token work, and this project has measured three times that a headline rate does not predict agent wall time. It is still a lead on our exact model and on the Gemma we just measured at 383s (#16). Expert offloading also bears on #20, where a 12 GB card with 30 GiB of RAM needs exactly that trick. #60, #77.

* **@ShankPeople**: X: [@ShankPeople](https://x.com/ShankPeople)  
GGUF quant surgery. Measured **+20% decode** moving GLM-5.3 KDA proj/head to Q8 — the thread that produced #51. GitHub unresolved: `markshank` matches the name but its repos are macOS/BSD systems work with no LLM presence, so it is **not** linked here.

* **@Kevrsub**: X: [@Kevrsub](https://x.com/Kevrsub)  
Runs real coding benchmarks, regression-tests older models. Found oMLX's **stock MTP depth 3 vs 5 → 50→60 tok/s**. No GitHub found.

* **@0xSero**: X: [@0xSero](https://x.com/0xSero) · GitHub: [@0xSero](https://github.com/0xSero) · Website: [sybilsolutions.io](https://sybilsolutions.io) · YouTube: [@0xSero](https://www.youtube.com/@0xSero)  
Low-bit quantization, on **both of our models**. [`glm-5.3-low-bit-tr3-wiki`](https://github.com/0xSero/glm-5.3-low-bit-tr3-wiki) is a 13-chapter treatment of GLM-5.3 Flash at low bitrate — MoE structure, calibration and sensitivity, EXL3/TR3 trellis encoding, K2/K3/K4 mixed tiers, bitrate arithmetic, validation gates. [`deepseek-v4-flash-0731-spark-sparkinfer`](https://github.com/0xSero/deepseek-v4-flash-0731-spark-sparkinfer) (163★) pins **our exact 0731 checkpoint** with a VALIDATION.md. `turboquant` does 3-bit-key / 2-bit-value KV quantization. **Read the GitHub, not the X feed** — the X presence is DGX-Spark-vs-Mac argument, the repos are the work. Caveat: most of it targets NVIDIA/vLLM, so the quantization reasoning transfers to us and the kernels do not.

* **@sudoingX**: X: [@sudoingX](https://x.com/sudoingX) · GitHub: [@sudoingX](https://github.com/sudoingX) · Repo: [qwen38-mtp](https://github.com/sudoingX/qwen38-mtp)  
Maintains the **qwen38-mtp** board (Apache-2.0, 245★, active 2026-08-31): **61 paired baseline-vs-MTP runs from 47 contributors** on Qwen3.8-27B dense, each run documenting quants, KV setup and serve config in a sweeps folder. The claim is that the MTP head already ships inside the GGUF and one llama.cpp flag is worth **+33–39% decode**. Directly relevant to #19 (does native MTP retire the mtplx stack?) and #39 (ds4's embedded MTP). **Two things to hold in mind.** The board is 48 NVIDIA and 11 AMD against 2 Apple Silicon entries (M4, M3 Ultra) — **a gap, not a disqualification**: a flag worth +33–39% on an M4 is a lead worth testing here, and the MTP head ships in the same GGUF we already hold. And it ranks on **decode rate**, which we have measured three times as non-predictive of agent wall time — so the *flag* is the lead, the *number* is not the claim. The paired-run method and per-hardware config documentation are unusually disciplined and worth copying.

* **@redp314**: X: [@redp314](https://x.com/redp314)  
Paolo Rosson, Head of Applied AI at Dext. **Benchmarks six MLX engines against each other on one machine**, which almost nobody does — mlx-serve, oMLX, Ollama, mlx-dspark, MTPLX, mlx-vlm on an M3 Max 96 GB. His 2026-09-01 result is the one to remember: **speculative decoding's gain depends on what you generate.** Every drafter loses 17–36% moving from code to prose, because acceptance falls (mlx-serve: 2.2 accepted tok/round on code, 1.3 on prose). **This is load-bearing for us: we measure code, the favorable case**, so any MTP figure quoted at us is close to a best case. His own conclusion is the right one — "a single tok/s is really a coding number or a prose number, say which one you measured". Measured on an M3 Max 96 GB; the **ranking between engines is the transferable part**, and mlx-serve winning all three categories is a reason to test it here. #60.

* **@_ARahim_**: X: [@_ARahim_](https://x.com/_ARahim_) · GitHub: [@ARahim3](https://github.com/ARahim3) · Repo: [mlx-dspark](https://github.com/ARahim3/mlx-dspark)  
Abdur Rahim. **mlx-dspark** (MIT, 627★, pushed 2026-09-01): a native MLX port of DeepSeek's DSpark and z-lab's DFlash speculative decoding, claiming up to 4x lossless decode across Gemma-4, Qwen3.8, Nemotron, Ornith-1.0 and others. Relevant to #19 (DFlash2 drafters) and #39. **Read our #58 first**: our own DSpark measurement on ds4 inverted once re-measured on current heads, and ds4#913 reports no net win on M5 Max — so a DSpark speedup claim needs checking on this machine before it is believed, whatever the engine.

* **@TheDavidTai**: X: [@TheDavidTai](https://x.com/TheDavidTai) · GitHub: [@davidtai](https://github.com/davidtai) · Website: [davidt.ai](https://davidt.ai)  
Runner and drafting optimization — MTPLX PR #391 (Qwen 3.8 Flash Next **50 → 85 t/s**), Qwen 3.8 27B at **113 t/s** via adaptive DFlash2 + mlx.fast. **Mostly replies; originals arrive in bursts** — a sweep of his last 12 posts found 10 replies, 2 quotes, 0 originals, so judge him on a week, not a day. Ties @Youssofal_'s MTPLX to @jundotkim's oMLX.

* **@0xkydo**: X: [@0xkydo](https://x.com/0xkydo) · [MLX Fast leaderboard](https://www.yukon.org/mlxfast) · Repo: [engine](https://github.com/Layr-Labs/mlxfast-gemma4-26b-a4b-engine)  
Kydo, Eigen Labs. Runs **MLX Fast**, a public leaderboard for making **Gemma 4 26B A4B** run faster on Apple Silicon — the thing @TheDavidTai's entry above already referenced before we documented it. `mlx.fast` redirects to `yukon.org/mlxfast`; the second is canonical. Top entry is **+130.8% over baseline — 573.7 tok/s decode, 6,940.6 tok/s prefill**, and Google's own @googlegemma amplified it on 2026-09-01. Official runs pair baseline and candidate on the same Mac under a thermal gate, eight prompts in one batch — a better method than most claims we see. **Two things to hold in mind.** It scores `prefill^0.25 · decode^0.75`, so decode carries three quarters of the rank, and decode rate is the one metric this project has measured three times as non-predictive of agent wall time — **`gemma4` is the backend that forced that finding**: it emits fewer tokens than qwen3.8 and still finishes last at 355.4s. And the leaderboard's model is **26B A4B, which we do not hold** — our gemma4 numbers are a 31B mxfp8 build, so nothing we have measured is a baseline for it. Engine is Swift, four stars, first pushed 2026-09-01. **The lead is real and unmeasured here**: #16 has been waiting on a non-Qwen backend since 2026-08-27, and this is a live, Google-endorsed push to make exactly that backend fast. #16, #60.

* **@yukonresearch**: X: [@yukonresearch](https://x.com/yukonresearch) · Website: [yukon.org](https://yukon.org/) · [MLX Fast](https://www.yukon.org/mlxfast)  
**Yukon**, an Eigen Labs project and the platform @0xkydo's leaderboard runs on -- "open frontier research", 1.2k followers, opened 2025-11. It runs several tracks at once (`mlxfast`, `matrices.fast`, `lighter.fast`, `better.codes`) with a weekly $10K pool. **Read it for two things.** First, the Apple Silicon track history: they cite Laguna XS 2.1 and then **Qwen 3.8 27B, which they say gained on day one from MTP and reached 3x in two weeks** -- that is our model class, and MTP is exactly what #77 is blocked on and #83 is asking about, so their claim is checkable here rather than admirable at a distance. Second, the **method**: one shared harness, only verified movement scores, and every submission enters a **public lineage tree that keeps the failures searchable** so later entrants start at the frontier instead of rediscovering dead ends. That is the practice this project reaches for whenever it archives a wrong result, done deliberately and at scale, and it is worth copying. **Their numbers are decode and prefill on a shared harness**, so the standing caveat applies -- we have measured three times that a headline rate does not predict agent wall time. A new frontier-model track was teased for early September. #60, #77, #80.

* **@thdxr**: X: [@thdxr](https://x.com/thdxr) · GitHub: [@thdxr](https://github.com/thdxr)  
Builds **OpenCode** — the client every agent row in this repo is recorded against. #137 was a client-version confound: two opencode versions mixed into one cell before anyone noticed, and the fix was recording `client_version` per row. A release feed for the client is the cheapest way to see the next one coming. Judge on releases, not TUI features. Verified post 2026-09-05.

* **@trymirai**: X: [@trymirai](https://x.com/trymirai) · GitHub: [@trymirai](https://github.com/trymirai) · Repo: [uzu](https://github.com/trymirai/uzu)  
**uzu**, an Apple-only inference engine (#134). Posts M5-series **speculative decoding** numbers against MTPLX and llama.cpp — the comparison #19 and #39 ask for, on our chip generation. Verified 2026-09-03 post announces spec-dec for Qwen3.6 27B with Qwen3.8 27B "coming soon". Their claim, unmeasured here.

* **@UnslothAI**: X: [@UnslothAI](https://x.com/UnslothAI) · GitHub: [@unslothai](https://github.com/unslothai)  
Dynamic GGUF quants and local-run recipes (#119). Verified 2026-09-04 post claims GLM-5.3-Flash "3.3x faster locally", 1.6-3.4x via optimized decoding **plus multi-token prediction**, and a **3-bit build for 128 GB machines**. Both halves are ours: #20 is the memory tier, #77 is MTP. **Their claim, and a decode-rate claim** — measured three times here as non-predictive of agent wall time.

* **@YJesus**: X: [@YJesus](https://x.com/YJesus) · GitHub: [@YJesus](https://github.com/YJesus)  
Cross-engine benchmarker on M3 Ultra 256 GB. **Runs ds4.** Verified 2026-08-29 post puts llama.cpp, mlx-serve, MTP and ds4 side by side on one machine, as a reply to @ddalcu and @ivanfioravanti — already inside our existing sources' conversation. M3 Ultra, not M5 Max: the ranking transfers, the numbers do not.

* **@Eschalabs**: X: [@Eschalabs](https://x.com/Eschalabs) · GitHub: [@Eschalabs](https://github.com/Eschalabs) · Repo: [escha-mlx](https://github.com/Eschalabs/escha-mlx)  
2-bit quants with custom MLX Metal kernels, **prefix caching**, M5 Pro batch numbers. Prefill is our bottleneck (#14, #50) and prefix caching is the lever. Verified post 2026-08-11 — the oldest of this batch, so confirm volume before relying on it.

* **@mweinbach**: X: [@mweinbach](https://x.com/mweinbach) · GitHub: [@mweinbach](https://github.com/mweinbach)  
Max Weinbach. M5 Metal kernel work. Verified 2026-08-25 post covers a **packed-INT4 Metal 4.1 path** and MLX Fast runs. Metal 4 tensor kernels are live for us: #138's arms both auto-enable the tensor route on M5, and `DS4_METAL_ENABLE_TENSOR` accumulate drift is a known hazard (#149). **High volume (~305k followers, posts constantly)** — see the volume rule below; do not let him run inside the same undifferentiated gather as the small accounts.

* **@13scoobie**: X: [@13scoobie](https://x.com/13scoobie) · GitHub: [@13scoobie](https://github.com/13scoobie)  
David White. **Runs our exact model on an M5 Max, on an engine we have never tested.** Verified 2026-08-06: DeepSeek-V4-Flash **0731 on an M5 Max via oMLX** — prompt processing **272 tok/s**, generation **14 tok/s**. oMLX has been the untested prefill leader since #60, and this is the only outside oMLX number on 0731 we hold. The verified post says "M5 max" without a memory size; the 128 GB attribution is grok's, not his. Also ran Qwen3.8-Flash-Next oQ4 **MTP on mlx-serve**, ~19.9 tok/s, OOM after ~3 hours — an MTP failure mode worth knowing before #77. Verified 2026-08-30 post amplifies @ddalcu's `llmprobe`. **Quiet since 2026-08-30**; ~25–35 items in the 30 days to 2026-09-06, effectively no NVIDIA content. GitHub is a fork collection rather than original work, and it forks `ddalcu/llmprobe` — which is what ties the two accounts to one person.

* **@0xZKnw**: X: [@0xZKnw](https://x.com/0xZKnw) · GitHub: [@0xZKnw](https://github.com/0xZKnw) · Repo: [mlxl3](https://github.com/0xZKnw/mlxl3) · HF: [0xzknw](https://huggingface.co/0xzknw) · Website: [0xzknw.tech](https://0xzknw.tech)  
**MLXL3 — EXL3 inference on Apple Silicon in MLX with custom Metal kernels**, shipping fast (v0.4.6 by 2026-09-05, verified). Verified 2026-09-02: LFM2.5-8B-A1B at **3.1 bpw, ~95 tok/s decode in ~4 GB on a 10-core M5**. **The mechanism is the lead, not the number** — an 8B on a base M5 says nothing about a 27B MoE here, but trellis/EXL3 quantization on Metal is the same ground @0xSero writes up for NVIDIA, running natively for once. Also forks `Eschalabs/escha-mlx`. ~60 followers, ~12 on GitHub: another account the volume rule protects.

* **@Brooooook_lyn**: X: [@Brooooook_lyn](https://x.com/Brooooook_lyn) · GitHub: [@Brooooooklyn](https://github.com/Brooooooklyn) · HF: [Brooooooklyn](https://huggingface.co/Brooooooklyn) · Repo: [mlx-node](https://github.com/mlx-node/mlx-node) · Website: [lyn.one](https://lyn.one)  
LongYinan, author of napi.rs. **Publishes MXFP4/MXFP8 dynamic MLX checkpoints** — verified 2026-08-15, [Qwen3.8-27B-MXFP4-mlx](https://huggingface.co/Brooooooklyn/Qwen3.8-27B-MXFP4-mlx) — and **our NEW arm is an MXFP4Down build**, so this is our own quantization axis with someone else's weights on it. Verified 2026-08-25: **"M5Max is already faster than DGX Spark in prefill"**, which is our bottleneck (#14, #50) and our machine. Verified 2026-08-17: **"MTP and concurrent inference cannot both be had"** — a constraint claim, unmeasured here, that bears on #19 and #77. Posts in Chinese and English; a real fraction of the feed is JS/Rust tooling and personal, so read for the MLX posts.

* **@no_stp_on_snek**: X: [@no_stp_on_snek](https://x.com/no_stp_on_snek) · GitHub: [@TheTom](https://github.com/TheTom) · Repo: [llama-cpp-turboquant](https://github.com/TheTom/llama-cpp-turboquant) · [turboquant_plus](https://github.com/TheTom/turboquant_plus)  
Tom Turney. **TurboQuant KV-cache compression, shipped as a llama.cpp fork with Metal and CUDA kernels** (2.3k★, pushed daily) — llama.cpp is our fast pick's engine and KV cache is the prefill lever (#14, #50). Also building `atlas`, a Rust inference engine. **Owns an M5 Max, an RTX 5090 and a DGX Spark, and the window leans NVIDIA — roughly 60/40 against us** (verified 2026-09-03 technical original is DeepSeek-V4-Flash on the Spark). He has said he will want Metal testers. **High volume, ~200–400 items in 30 days and mostly short replies** — the volume rule below applies to him as it does to @mweinbach; read the repo before the feed.

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

* **mlx-community**: HF: [mlx-community](https://huggingface.co/mlx-community)  
Where weights appear first.

---

## Read the repo, not the feed

This file is organised around X because that is where most of this field
announces itself. For the sources below that is the wrong surface: the work is
in a repository or an issue tracker, and the feed is chatter or silence.

**The heading used to say "Not on X", and both entries disproved it** — Flor1an-B
posts as [@_LEFBE](https://x.com/_LEFBE), rarely, and Mirai is in Tier 2 as
[@trymirai](https://x.com/trymirai). The property that puts a source here is
not the absence of an account. It is that checking the feed first would miss
the work, so check GitHub first and read the feed second, if at all.

A follower count is not the property we need either.

* **Flor1an-B**: GitHub: [@Flor1an-B](https://github.com/Flor1an-B) · [ds4 issues](https://github.com/antirez/ds4/issues?q=author%3AFlor1an-B) · X: [@_LEFBE](https://x.com/_LEFBE) · Repo: [Ka1zen](https://github.com/Flor1an-B/Ka1zen)  
Bertaux Florian, Paris. **He does have an X account — @_LEFBE** — which this entry previously said he did not; the two are the same person, and the corroboration is his own X profile linking `Flor1an-B/Ka1zen`, not an assertion. 3 GitHub followers, 51 on X, account opened 2026-02-19 — and **15 authored issues and PRs on `antirez/ds4`, all engine internals**, several landing exactly where we are stuck. [#789](https://github.com/antirez/ds4/pull/789) ports visible-KV checkpoint fixes for tool turns, which is the token-mismatch failure of ds4#816 that blocks #64. [#691](https://github.com/antirez/ds4/issues/691) is KV cache reuse breaking for tool clients that do not replay reasoning — the same bug from the client side. [#695](https://github.com/antirez/ds4/issues/695) argues the DSpark scheduler's break-even model ignores replay cost. [#750](https://github.com/antirez/ds4/issues/750) is native MTP corrupting output at `--mtp-draft>=2` (#39). **Benchmarks on an M5 Max 128 GB with DeepSeek-V4-Flash 0731 — our exact machine and primary model** — which almost nobody else does; #75 came from their temp>0 DSpark table on that setup. **The X feed is worth a pass now that we have it**, though it is mostly replies to @antirez and @ivanfioravanti. Two items already line up with our own work: on 2026-07-20 he saw **no improvement from DSpark** and asked antirez for numbers, which is where #58 and #75 landed months later; and on 2026-07-26 he reported **the same prompt and model giving different answers under Claude Code, OpenCode and ds4**, and said he wanted benchmarks of real fix-and-create work rather than leaderboard tok/s — which is this repo's thesis, arrived at independently. `Ka1zen` (13★) is his offline MLX chat app for Apple Silicon. **Still read the ds4 issue list first**: the feed is chatter, the issues are the work.

* **Mirai (trymirai)**: X: [@trymirai](https://x.com/trymirai) · GitHub: [trymirai/uzu](https://github.com/trymirai/uzu) · Website: [trymirai.com](https://trymirai.com) · [docs](https://docs.trymirai.com) · PyPI: [uzu](https://pypi.org/project/uzu/)  
**uzu — an Apple-only inference engine written in Rust**, MIT, 1726★, iOS and macOS only, release 0.5.23 on 2026-09-03 with commits landing daily. **Reachable by `uv add uzu`**, with Python, TypeScript and Swift bindings, so it is testable here without a build. The claim that brought it to us — 105 tok/s on a 27B model on an M5 Max, **2x MTPLX and 3.5x llama.cpp** — is unsourced and secondhand (#134), but the MTPLX number we hold is itself old and unreplicated, so the comparison is worth owning. **How we found it is the lesson:** an aggregator account on X, not a handle in this file, and no sweep surface we run would have surfaced it — an actively released Apple-only engine was invisible to all seven. Open question before any measurement: whether "M5 Neural Accelerators" means the ANE (#123) or the matmul units in the GPU cores. **The company account is a Tier 2 entry above**; this entry is the engine.

## Repositories to watch

**Every repo this project depends on, in one place.** X is where the field
announces itself; GitHub is where it ships. The 2026-09-01 sweep found the fact
that mattered most that day -- `qwen4exp` is Qwen3.8-Flash-Next, so llama.cpp
commits under that name are work on our own fast pick -- and it nearly missed
two repos because this file linked authors' profiles rather than their code.

Run it rather than reading it:

```sh
uv run python scripts/upstream_sweep.py --hours 24
uv run python scripts/upstream_sweep.py --hours 168 --quiet-empty   # a week
```

The script's `WATCHED` dict is the source of truth and a test fails if this
list drifts from it. It reports releases and commit subjects, and it says
explicitly when a repo is **unreachable** -- a renamed or private repo
otherwise looks exactly like a quiet one, and "nothing happened upstream" is
the wrong conclusion to draw from an auth failure.

* **[`antirez/ds4`](https://github.com/antirez/ds4)**  
our primary engine; the only one that runs DeepSeek-V4-Flash and GLM-5.3

* **[`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp)**  
our fast pick's engine; `qwen4exp` IS Qwen3.8-Flash-Next

* **[`ollama/ollama`](https://github.com/ollama/ollama)**  
the 31 GB entry point, and our only MLX runtime

* **[`anomalyco/opencode`](https://github.com/anomalyco/opencode)**  
our only client

* **[`evanwtf/local-llm`](https://github.com/evanwtf/local-llm)**  
This project

* **[`evanwtf/gmail-archive`](https://github.com/evanwtf/gmail-archive)**  
the excision tasks' target repository

* **[`evanwtf/ds4`](https://github.com/evanwtf/ds4)**  
our ds4 fork (#27 asks whether it can be retired)

* **[`ml-explore/mlx`](https://github.com/ml-explore/mlx)**  
the framework everything MLX sits on

* **[`ml-explore/mlx-lm`](https://github.com/ml-explore/mlx-lm)**  
reference MLX server; new architectures land here first

* **[`jundot/omlx`](https://github.com/jundot/omlx)**  
oMLX -- prefill leader, untested here (#60)

* **[`ddalcu/mlx-serve`](https://github.com/ddalcu/mlx-serve)**  
benchmarked on our exact machine; llmprobe's author

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
2. **Judge relevance to this machine first** — would it change a number on an
   M5 Max, 128 GB, Metal? A result on an M3 or M4 is a **lead, not noise**.
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
the fixer — `api.fxtwitter.com` is the same service `verify-posts.py` already
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

**Verify before repeating.** Pipe the output through
`~/.claude/skills/grok/verify-posts.py`. It checks the post exists, its real
timestamp, its true author, and whether it is a post or a reply. It has caught a
"post" that was a reply, and grok has fabricated an item outright. **A claim with
no post URL is unusable.**

**Date and version every claim.** #55: blog sources describing OpenCode looked
authoritative and covered **1.1.x–1.14.x** while we ran **1.18.25**. Six months
is several different products in this field.

**These are leads, not results — and "not our hardware" is not a reason to
discard one.** Almost every number posted is from an M3 Ultra, M3 Max or M4;
we are a 128 GB M5 Max. **That is where the work happens.** Most developers
building these engines are on M3/M4, so a kernel, a flag or a scheduling change
that wins there is the most likely source of a win here — the mechanism usually
transfers even when the number does not. Treat an improvement on another Apple
Silicon machine as **a promising lead to test locally**, and say so in the entry
rather than writing it off.

What genuinely does not transfer is narrower than it looks:

- **A configuration that will not fit.** 165 GB of active memory is unavailable
  in 128 GB, full stop. The right response is to ask which quant does fit, not
  to drop the engine.
- **The absolute number.** #58 showed ~4% of throughput moves with thermal
  state alone, and #23 puts a 3-trial task median at ±28%. Quote ratios and
  rankings, not other people's absolutes.
- **CUDA/ROCm kernels.** The quantization reasoning transfers; the kernels do
  not.

Per #59, nothing from here enters `RECOMMENDATIONS.md` without our own
controlled measurement. That is a bar for publishing, not a filter for what is
worth reading.

**Ask for the disconfirming cases explicitly** — headless/CI use, local models,
edits failing to apply. General sentiment will not surface them. OpenCode is
widely liked and still failed here, and **not one external source mentioned the
headless problem** because every author was using the TUI (#54).

**Popularity is not the property we need.** A tool nobody posts about may be
unfashionable and correct — and so may a person. Flor1an-B has three GitHub
followers and fifty-one on X, and produced the only DSpark measurement anyone
has taken at real sampling on our exact machine and model (#75). **Judge a source by whether
its claims are checkable and whether it runs hardware like ours, not by reach.**

## Weight the gather by volume, not by follower count

The X gather returns a bounded amount of text for a fixed window. A prolific
account fills that budget with its own posts and crowds out the low-volume
accounts, which are often where the specific numbers are: @0xZKnw has ~60
followers and posts M5 decode figures; @korale77 has ~190 and runs an M5 Max
128 GB. Losing either to a busy account's ordinary output is a bad trade, and
it is the same failure as `ci_activity` burying mentions in surface 1a.

Two mitigations, either acceptable:

- **Cap per account.** Ask for at most 2-3 items per handle, so no single
  account can consume the window.
- **Split the query.** Gather high-volume accounts in their own call, so a
  quiet week from the small accounts is legible instead of invisible.

**Both examples above are now in the file**, which is what makes the rule
concrete: @0xZKnw and @korale77 are entries the sweep must not lose, and the
same 2026-09-06 batch added @no_stp_on_snek at 200-400 items in 30 days.

**@mweinbach and @no_stp_on_snek are the accounts this applies to.** Later
high-volume additions belong in the same bucket, named in this file so the
reason survives.

## Where the 2026-09-05 additions came from

Seven accounts -- @ggerganov, @thdxr, @trymirai, @UnslothAI, @YJesus,
@Eschalabs, @mweinbach -- were recommended by grok, not found by a person
reading feeds. Every representative post was verified for existence and
authorship (19/19, `scripts/verify_posts.py`), but **the descriptions of what
each account habitually posts are grok's characterization, not a read of their
timeline.** Issue #152 holds the full list. Treat these seven as leads
promoted on one verified post each, and demote any that does not earn its place.

## Where the 2026-09-06 additions came from

The twelve accounts #152 left undecided were resolved on 2026-09-06. Each was
checked three ways: **30 days of X activity** to 2026-09-06, **a GitHub or
Hugging Face identity** confirmed against the profile rather than guessed from a
matching name, and **the Apple-silicon share of the output** against NVIDIA,
cloud or phone-NPU work. Sixteen representative posts verified, 16/16
(`scripts/verify_posts.py`, `logs/sweeps/verify-posts-M5-Max-128GB-20260906T043150Z.log`).

**Six added:** @korale77 (Tier 1), @13scoobie, @0xZKnw, @Brooooook_lyn,
@no_stp_on_snek (Tier 2), @atomic_chat_hq (Tier 3).

**Six not added, with the reason, so nobody re-proposes them:**

* **@Badtheorylabs**  
Company in Lagos. **No Apple-silicon content on X in the 30-day window** — the runtime figure they quote is 43 tok/s on an RTX PRO 6000. [`Macaw`](https://github.com/Badtheorylabs/Macaw), an MLX 4-bit macOS agent, is real but last pushed 2026-08-08 and never reaches the feed. Nothing to sweep.

* **@OrcaRouter**  
Company. A **hosted API router** is the product; local MLX weights are maybe 10–20% of the feed, and the 4-bit build they push needs 200 GB. Wrong machine, wrong regime.

* **@trycua**  
Company. llama.cpp Metal **inside Apple-silicon VMs**, on M1 Ultra. Virtualization is not our regime and the numbers would not transfer.

* **@lmstudio**  
Company. It does maintain [`lmstudio-ai/mlx-engine`](https://github.com/lmstudio-ai/mlx-engine) and ships GGUF/MLX quants, but the feed is ~45% Bionic **cloud** agent news and only ~25% local Mac inference. We run neither the app nor the engine.

* **@sanchitmonga22**  
Individual — Sanchit Monga, RunAnywhere. Genuinely technical, but ~35% phone **NPU/ANE**, ~25% cloud coding agents, and only ~15% single-Mac Metal/MLX. At 150–250 items in 30 days he would spend the gather budget on the wrong execution path. #123 stays the place to ask the ANE question.

* **@glaforge**  
Individual — Guillaume Laforge, Google. **Zero** MLX, Metal, llama.cpp or Apple-silicon posts in the window; the feed is Gemini Java and Kotlin SDKs. The single M4 Pro benchmark in #152 was from July and is not a source.

**What the check actually caught.** Two accounts #152 filed as plausible are
the strongest finds in the batch — @korale77 runs **ds4 on an M5 Max 128 GB**
and @13scoobie ran **DeepSeek-V4-Flash 0731 on oMLX** on the same machine — and
one grok called Apple-silicon, @Badtheorylabs, has no Apple content on X at all.
**Follower count predicted nothing**: the two closest accounts have ~190 and
~170 followers, and the three largest in the batch were all rejected.

**Identity is corroborated, not assumed.** @13scoobie's GitHub is a security
engineer's fork list with no LLM work of its own; it is linked because it forks
`ddalcu/llmprobe`, which is the tool his own verified 2026-08-30 post promotes.
`github.com/mweinbach` is a **name match only** — the repos are agent and Swift
tooling, not the Metal kernel work his X account posts.

**No repo was added to the watch list.** [`TheTom/llama-cpp-turboquant`](https://github.com/TheTom/llama-cpp-turboquant),
[`0xZKnw/mlxl3`](https://github.com/0xZKnw/mlxl3) and
[`Eschalabs/escha-mlx`](https://github.com/Eschalabs/escha-mlx) each earn a look,
but that list is rendered from `WATCHED` in `scripts/upstream_sweep.py` and
adding an entry is a change to the sweep's cost, not a note. Decide it on its own.

**A gather is not evidence of a quiet field until the balance is checked.** On
2026-09-05 the X gather returned one post from seven accounts in 24 hours and
a follow-up call failed with `402 Payment Required: Grok Build usage balance
exhausted`. Quota, not quiet. Read the balance before surface 7 and record it:
a 402 announces itself, a degraded answer does not.
