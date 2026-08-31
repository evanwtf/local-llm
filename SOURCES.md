# Who to check on X

**Use:** *"Check our influencer list for updates over the past week."*

That means: run the sweep in [How to run it](#how-to-run-it) below, verify every
post before repeating it, and report what changed — not who said what.

This file exists because the Apple-Silicon local-inference field moves faster
than this project measures. Two engines shipped double-digit improvements in a
48-hour window while our own docs still described a three-engine world (#60).

---

## Tier 1 — check every time

| handle | what they are | why |
|---|---|---|
| [@antirez](https://x.com/antirez) | DwarfStar / ds4 author | **Our primary engine.** Ships models on preview branches and announces them here before the README catches up. `glm-5.3-flash` was on X days before it was documented. |
| [@ivanfioravanti](https://x.com/ivanfioravanti) | daily benchmarker, M3 Ultra / multi-Mac | Highest-volume tester on Apple Silicon. #51 came from one of his posts: Q8_0→Q4_K attention+head, **+12.6% decode with a quality gain**. |
| [@ddalcu](https://x.com/ddalcu) | mlx-serve, `llmprobe` | **Benchmarks on a 1x M5 Max 128 GB laptop — our exact machine.** Ported ds4's evals into a cross-engine harness (`npx llmprobe --eval`). |
| [@Spangler3000](https://x.com/Spangler3000) | oMLX Metal kernels (QSA, DFlash2) | Densest Metal-kernel signal in the field. His PRs are why oMLX leads on prefill — **and prefill is our bottleneck** (#14, #50, ds4#816). |
| [@jundotkim](https://x.com/jundotkim) | oMLX author | oMLX 0.6.4: PP 32k **834 → 1114 tok/s**, TG **40 → 46**. Untested here (#60). |

## Tier 2 — check weekly

| handle | what they are |
|---|---|
| [@Youssofal_](https://x.com/Youssofal_) | MTPLX — MTP speculative decoding, custom Metal kernels. We hold an old, unreplicated MTPLX number marked provisional. |
| [@Raullen](https://x.com/Raullen) | Rapid-MLX (#57). Apache 2.0, OpenAI-compatible, claims 12x prefix cache. |
| [@awnihannun](https://x.com/awnihannun) | Co-created MLX. Lower volume since leaving Apple, still framework source-of-truth. |
| [@zcbenz](https://x.com/zcbenz) | mlx-lm maintainer. |
| [@N8Programs](https://x.com/N8Programs) | Quant conversions, custom kernels, training on Silicon. |
| [@Prince_Canuma](https://x.com/Prince_Canuma) | mlx-vlm, mlx-audio — the multimodal side. Out of scope today (coding agents only) but the place vision lands first. |
| [@ShankPeople](https://x.com/ShankPeople) | GGUF quant surgery. Measured **+20% decode** moving GLM-5.3 KDA proj/head to Q8 — the thread that produced #51. |
| [@Kevrsub](https://x.com/Kevrsub) | Runs real coding benchmarks, regression-tests older models. Found oMLX's **stock MTP depth 3 vs 5 → 50→60 tok/s**. |

## Tier 3 — occasional

@TeksEdge (runnable oMLX/DFlash recipes) · @digitalix (Alex Ziskind — distributed MLX demos) ·
@MitjaMartini (DwarfStar Metal numbers on M3 Ultra) · @onthexitter69 (**ANE offload PR, +33.8% GDN projection**) ·
@0xSero, @bleysg (Mac vs DGX Spark arguments — heat, occasionally light) ·
@angeloskath, @DiganiJagrit, @trebolloc (MLX team) · [mlx-community](https://huggingface.co/mlx-community) (where weights appear first)

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

**These are leads, not results.** Almost every number posted is from an **M3
Ultra 512 GB**; we are a **128 GB M5 Max**. #58 showed ~4% of throughput moves
with thermal state alone. Per #59, nothing from here enters `RECOMMENDATIONS.md`
without our own controlled measurement.

**Ask for the disconfirming cases explicitly** — headless/CI use, local models,
edits failing to apply. General sentiment will not surface them. OpenCode is
widely liked and still failed here, and **not one external source mentioned the
headless problem** because every author was using the TUI (#54).

**Popularity is not the property we need.** A tool nobody posts about may be
unfashionable and correct.
