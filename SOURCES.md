# Who to check on X

**Use:** *"Check our influencer list for updates over the past week."*

That means: run the sweep in [How to run it](#how-to-run-it) below, verify every
post before repeating it, and report what changed — not who said what.

This file exists because the Apple-Silicon local-inference field moves faster
than this project measures. Two engines shipped double-digit improvements in a
48-hour window while our own docs still described a three-engine world (#60).

---

## Tier 1 — check every time

| handle | links | what they are | why |
|---|---|---|---|
| **@antirez** | [X](https://x.com/antirez) · [GitHub](https://github.com/antirez) · [invece.org](http://invece.org) | DwarfStar / ds4 author | **Our primary engine.** Ships models on preview branches and announces them here before the README catches up. `glm-5.3-flash` was on X days before it was documented. |
| **@ivanfioravanti** | [X](https://x.com/ivanfioravanti) · [GitHub](https://github.com/ivanfioravanti) | daily benchmarker, M3 Ultra / multi-Mac | Highest-volume tester on Apple Silicon. #51 came from one of his posts: Q8_0→Q4_K attention+head, **+12.6% decode with a quality gain**. |
| **@ddalcu** | [X](https://x.com/ddalcu) · [GitHub](https://github.com/ddalcu) · [mlxserve.com](http://mlxserve.com) · [dalcu.com](http://www.dalcu.com) | mlx-serve, `llmprobe` | **Benchmarks on a 1x M5 Max 128 GB laptop — our exact machine.** Ported ds4's evals into a cross-engine harness (`npx llmprobe --eval`). |
| **@Spangler3000** | [X](https://x.com/Spangler3000) · [GitHub](https://github.com/jonathan308) | oMLX Metal kernels (QSA, DFlash2) | Densest Metal-kernel signal in the field. His PRs are why oMLX leads on prefill — **and prefill is our bottleneck** (#14, #50, ds4#816). |
| **@jundotkim** | [X](https://x.com/jundotkim) · [GitHub](https://github.com/jundot) · [omlx.ai](https://omlx.ai) | oMLX author | oMLX 0.6.4: PP 32k **834 → 1114 tok/s**, TG **40 → 46**. Untested here (#60). |

## Tier 2 — check weekly

| handle | links | what they are |
|---|---|---|
| **@Youssofal_** | [X](https://x.com/Youssofal_) · [GitHub](https://github.com/youssofal) | MTPLX — MTP speculative decoding, custom Metal kernels. We hold an old, unreplicated MTPLX number marked provisional. |
| **@Raullen** | [X](https://x.com/Raullen) · [GitHub](https://github.com/raullenchai) | Rapid-MLX (#57). Apache 2.0, OpenAI-compatible, claims 12x prefix cache. |
| **@rapidmlx** | [X](https://x.com/rapidmlx) · [rapidmlx.com](https://rapidmlx.com) · [GitHub](https://github.com/raullenchai/Rapid-MLX) | **The project account for Rapid-MLX**, distinct from @Raullen above — it ships the release notes, so it is the one to watch for versions. Releases are frequent: 0.13.2 on 2026-08-31, **0.13.3 on 2026-09-01** adding native GLM-5.3-Flash. **Read the memory line before the speed line**: 0.13.3's headline is a 4-bit GLM-5.3 checkpoint using **165 GB of active memory, stated for 192 GB+ Macs**. That specific configuration will not load in 128 GB — so the actionable question is whether the engine serves a GLM-5.3 quant that fits here, which is worth finding out. **The engine is a live lead; only that one configuration is out of reach.** #57, #60. |
| **@awnihannun** | [X](https://x.com/awnihannun) · [GitHub](https://github.com/awni) · [awnihannun.com](https://awnihannun.com/) | Co-created MLX. Lower volume since leaving Apple, still framework source-of-truth. |
| **@zcbenz** | [X](https://x.com/zcbenz) · [GitHub](https://github.com/zcbenz) · [zcbenz.com](https://zcbenz.com) | mlx-lm maintainer. |
| **@N8Programs** | [X](https://x.com/N8Programs) · [GitHub](https://github.com/N8python) | Quant conversions, custom kernels, training on Silicon. |
| **@Prince_Canuma** | [X](https://x.com/Prince_Canuma) · [GitHub](https://github.com/Blaizzy) | mlx-vlm, mlx-audio — the multimodal side. Out of scope today (coding agents only) but the place vision lands first. |
| **@ShankPeople** | [X](https://x.com/ShankPeople) | GGUF quant surgery. Measured **+20% decode** moving GLM-5.3 KDA proj/head to Q8 — the thread that produced #51. GitHub unresolved: `markshank` matches the name but its repos are macOS/BSD systems work with no LLM presence, so it is **not** linked here. |
| **@Kevrsub** | [X](https://x.com/Kevrsub) | Runs real coding benchmarks, regression-tests older models. Found oMLX's **stock MTP depth 3 vs 5 → 50→60 tok/s**. No GitHub found. |
| **@0xSero** | [X](https://x.com/0xSero) · [GitHub](https://github.com/0xSero) · [sybilsolutions.io](https://sybilsolutions.io) · [YouTube](https://www.youtube.com/@0xSero) | Low-bit quantization, on **both of our models**. [`glm-5.3-low-bit-tr3-wiki`](https://github.com/0xSero/glm-5.3-low-bit-tr3-wiki) is a 13-chapter treatment of GLM-5.3 Flash at low bitrate — MoE structure, calibration and sensitivity, EXL3/TR3 trellis encoding, K2/K3/K4 mixed tiers, bitrate arithmetic, validation gates. [`deepseek-v4-flash-0731-spark-sparkinfer`](https://github.com/0xSero/deepseek-v4-flash-0731-spark-sparkinfer) (163★) pins **our exact 0731 checkpoint** with a VALIDATION.md. `turboquant` does 3-bit-key / 2-bit-value KV quantization. **Read the GitHub, not the X feed** — the X presence is DGX-Spark-vs-Mac argument, the repos are the work. Caveat: most of it targets NVIDIA/vLLM, so the quantization reasoning transfers to us and the kernels do not. |
| **@sudoingX** | [X](https://x.com/sudoingX) · [GitHub](https://github.com/sudoingX) · [qwen38-mtp](https://github.com/sudoingX/qwen38-mtp) | Maintains the **qwen38-mtp** board (Apache-2.0, 245★, active 2026-08-31): **61 paired baseline-vs-MTP runs from 47 contributors** on Qwen3.8-27B dense, each run documenting quants, KV setup and serve config in a sweeps folder. The claim is that the MTP head already ships inside the GGUF and one llama.cpp flag is worth **+33–39% decode**. Directly relevant to #19 (does native MTP retire the mtplx stack?) and #39 (ds4's embedded MTP). **Two things to hold in mind.** The board is 48 NVIDIA and 11 AMD against 2 Apple Silicon entries (M4, M3 Ultra) — **a gap, not a disqualification**: a flag worth +33–39% on an M4 is a lead worth testing here, and the MTP head ships in the same GGUF we already hold. And it ranks on **decode rate**, which we have measured three times as non-predictive of agent wall time — so the *flag* is the lead, the *number* is not the claim. The paired-run method and per-hardware config documentation are unusually disciplined and worth copying. |
| **@redp314** | [X](https://x.com/redp314) | Paolo Rosson, Head of Applied AI at Dext. **Benchmarks six MLX engines against each other on one machine**, which almost nobody does — mlx-serve, oMLX, Ollama, mlx-dspark, MTPLX, mlx-vlm on an M3 Max 96 GB. His 2026-09-01 result is the one to remember: **speculative decoding's gain depends on what you generate.** Every drafter loses 17–36% moving from code to prose, because acceptance falls (mlx-serve: 2.2 accepted tok/round on code, 1.3 on prose). **This is load-bearing for us: we measure code, the favourable case**, so any MTP figure quoted at us is close to a best case. His own conclusion is the right one — "a single tok/s is really a coding number or a prose number, say which one you measured". Measured on an M3 Max 96 GB; the **ranking between engines is the transferable part**, and mlx-serve winning all three categories is a reason to test it here. #60. |
| **@_ARahim_** | [X](https://x.com/_ARahim_) · [GitHub](https://github.com/ARahim3) · [mlx-dspark](https://github.com/ARahim3/mlx-dspark) | Abdur Rahim. **mlx-dspark** (MIT, 627★, pushed 2026-09-01): a native MLX port of DeepSeek's DSpark and z-lab's DFlash speculative decoding, claiming up to 4x lossless decode across Gemma-4, Qwen3.8, Nemotron, Ornith-1.0 and others. Relevant to #19 (DFlash2 drafters) and #39. **Read our #58 first**: our own DSpark measurement on ds4 inverted once re-measured on current heads, and ds4#913 reports no net win on M5 Max — so a DSpark speedup claim needs checking on this machine before it is believed, whatever the engine. |
| **@TheDavidTai** | [X](https://x.com/TheDavidTai) · [GitHub](https://github.com/davidtai) · [davidt.ai](https://davidt.ai) | Runner and drafting optimisation — MTPLX PR #391 (Qwen 3.8 Flash Next **50 → 85 t/s**), Qwen 3.8 27B at **113 t/s** via adaptive DFlash2 + mlx.fast. **Mostly replies; originals arrive in bursts** — a sweep of his last 12 posts found 10 replies, 2 quotes, 0 originals, so judge him on a week, not a day. Ties @Youssofal_'s MTPLX to @jundotkim's oMLX. |

## Tier 3 — occasional

| handle | links | what they are |
|---|---|---|
| **@TeksEdge** | [X](https://x.com/TeksEdge) · [teksed.com](https://teksed.com) | Runnable oMLX/DFlash recipes. No GitHub found. |
| **@digitalix** | [X](https://x.com/digitalix) · [GitHub](https://github.com/alexziskind1) · [YouTube](https://youtube.com/@azisk) | Alex Ziskind — distributed MLX demos. |
| **@MitjaMartini** | [X](https://x.com/MitjaMartini) · [GitHub](https://github.com/mitja) · [mitjamartini.com](https://mitjamartini.com) | DwarfStar Metal numbers on M3 Ultra. GitHub is a **probable** match — name and `llamatunnel` corroborate, not confirmed. |
| **@onthexitter69** | [X](https://x.com/onthexitter69) · [GitHub](https://github.com/onthehub97) | **ANE offload PR, +33.8% GDN projection.** |
| **@angeloskath** | [X](https://x.com/angeloskath) · [GitHub](https://github.com/angeloskath) · [angeloskath.github.io](https://angeloskath.github.io/) | MLX team, Apple. |
| **@DiganiJagrit** | [X](https://x.com/DiganiJagrit) · [GitHub](https://github.com/jagrit06) | MLX team, Apple. |
| **@trebolloc** | [X](https://x.com/trebolloc) · [GitHub](https://github.com/andresy) · [ronan.collobert.com](https://ronan.collobert.com) | Ronan Collobert — OG Torch, MLX team, Apple. |
| **@bleysg** | [X](https://x.com/bleysg) · [GitHub](https://github.com/bleys) | Mac vs DGX Spark arguments — heat, occasionally light. GitHub is a **probable** match — name and `Auto-GPT` corroborate, not confirmed. |
| — | [mlx-community](https://huggingface.co/mlx-community) | Where weights appear first. |

---

## How to run it

`/grok` reads X. `WebFetch` on an `x.com` URL hits a login wall and will not work.

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
unfashionable and correct.
