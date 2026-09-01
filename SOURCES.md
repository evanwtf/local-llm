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
| **@0xkydo** | [X](https://x.com/0xkydo) · [MLX Fast leaderboard](https://www.yukon.org/mlxfast) · [engine](https://github.com/Layr-Labs/mlxfast-gemma4-26b-a4b-engine) | Kydo, Eigen Labs. Runs **MLX Fast**, a public leaderboard for making **Gemma 4 26B A4B** run faster on Apple Silicon — the thing @TheDavidTai's entry above already referenced before we documented it. `mlx.fast` redirects to `yukon.org/mlxfast`; the second is canonical. Top entry is **+130.8% over baseline — 573.7 tok/s decode, 6,940.6 tok/s prefill**, and Google's own @googlegemma amplified it on 2026-09-01. Official runs pair baseline and candidate on the same Mac under a thermal gate, eight prompts in one batch — a better method than most claims we see. **Two things to hold in mind.** It scores `prefill^0.25 · decode^0.75`, so decode carries three quarters of the rank, and decode rate is the one metric this project has measured three times as non-predictive of agent wall time — **`gemma4` is the backend that forced that finding**: it emits fewer tokens than qwen3.8 and still finishes last at 355.4s. And the leaderboard's model is **26B A4B, which we do not hold** — our gemma4 numbers are a 31B mxfp8 build, so nothing we have measured is a baseline for it. Engine is Swift, four stars, first pushed 2026-09-01. **The lead is real and unmeasured here**: #16 has been waiting on a non-Qwen backend since 2026-08-27, and this is a live, Google-endorsed push to make exactly that backend fast. #16, #60. |

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

## Not on X — check GitHub directly

This file is organised around X because that is where most of this field
announces itself. Some of the most useful people do not post there at all, and
a follower count is not the property we need.

| who | links | what they are |
|---|---|---|
| **Flor1an-B** | [GitHub](https://github.com/Flor1an-B) · [ds4 issues](https://github.com/antirez/ds4/issues?q=author%3AFlor1an-B) · [X @_LEFBE](https://x.com/_LEFBE) · [Ka1zen](https://github.com/Flor1an-B/Ka1zen) | Bertaux Florian, Paris. **He does have an X account — @_LEFBE** — which this entry previously said he did not; the two are the same person, and the corroboration is his own X profile linking `Flor1an-B/Ka1zen`, not an assertion. 3 GitHub followers, 51 on X, account opened 2026-02-19 — and **15 authored issues and PRs on `antirez/ds4`, all engine internals**, several landing exactly where we are stuck. [#789](https://github.com/antirez/ds4/pull/789) ports visible-KV checkpoint fixes for tool turns, which is the token-mismatch failure of ds4#816 that blocks #64. [#691](https://github.com/antirez/ds4/issues/691) is KV cache reuse breaking for tool clients that do not replay reasoning — the same bug from the client side. [#695](https://github.com/antirez/ds4/issues/695) argues the DSpark scheduler's break-even model ignores replay cost. [#750](https://github.com/antirez/ds4/issues/750) is native MTP corrupting output at `--mtp-draft>=2` (#39). **Benchmarks on an M5 Max 128 GB with DeepSeek-V4-Flash 0731 — our exact machine and primary model** — which almost nobody else does; #75 came from their temp>0 DSpark table on that setup. **The X feed is worth a pass now that we have it**, though it is mostly replies to @antirez and @ivanfioravanti. Two items already line up with our own work: on 2026-07-20 he saw **no improvement from DSpark** and asked antirez for numbers, which is where #58 and #75 landed months later; and on 2026-07-26 he reported **the same prompt and model giving different answers under Claude Code, OpenCode and ds4**, and said he wanted benchmarks of real fix-and-create work rather than leaderboard tok/s — which is this repo's thesis, arrived at independently. `Ka1zen` (13★) is his offline MLX chat app for Apple Silicon. **Still read the ds4 issue list first**: the feed is chatter, the issues are the work. |

## How to run it

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
