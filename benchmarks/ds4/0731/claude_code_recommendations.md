# Backing Claude Code with a local DeepSeek V4 Flash

Practical setup guide derived from the benchmarks in [REPORT.md](REPORT.md).
Machine: MacBook Pro M5 Max, 128 GiB, macOS 26.5.2. Engine: DwarfStar `ds4`
built from `main @ b030961`.

---

> **Routing rules now live in [`agent/ROUTING.md`](agent/ROUTING.md)** — which
> work to send to the local model, which to keep on a frontier model, and why.
> Derived from two capability ladders. Read that first if you are deciding what
> to run where; this document covers *how* to set it up.

## TL;DR

Use the **mixed q2/q4 0731** build, **resident**, served by **`ds4-server`**
with **`--warm-weights`**.

One command does the whole thing — it starts the server if it is not already
up, waits for it, then hands off to Claude Code:

```sh
benchmarks/ds4/0731/agent/ds4-up
```

`ds4-up stop` frees the ~91 GiB again for GPU work; `ds4-up status` reports what
is running and warns if `--trace` is armed. Runtime state lives in `~/.ds4`.

The rest of this section explains what that script does, and stays correct if
you prefer to run the pieces by hand.

```sh
./ds4-server \
  -m gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf \
  --warm-weights \
  --ctx 100000 \
  --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192
```

`--kv-disk-dir` spills the prefix cache to disk, so a cache entry survives an
eviction instead of forcing a full re-prefill. See §4.

> **Do not add `--trace` casually.** It logs the full prompt of every request —
> with Claude Code that means your `CLAUDE.md` and the contents of every file
> the agent reads, on every turn. Useful for cache diagnostics, but the output
> is private and must never be committed. See
> [`agent/AGENT_FINDINGS.md`](agent/AGENT_FINDINGS.md) for the incident that
> prompted this note.

Then point Claude Code at it (wrapper below).

**Do not** use the MXFP4 build for agent work, despite it scoring higher on the
eval harness. Reasoning in §2.

---

## 1. Why this model

Four candidates were measured on the full 92-question eval harness:

| model | eval | prefill @8192 | gen steady | resident on 128 GiB? |
|---|---|---|---|---|
| **mixed q2/q4 0731** | 76/92 (82.6%) | **488.5 t/s** | **35.5 t/s** | yes (90.9 GiB) |
| MXFP4 0731 | **80/92 (87.0%)** | 115.7 t/s | 18.1 t/s | no — streamed |
| q2 0731 | 68/92 (73.9%) | 465.1 t/s | ~32 t/s | yes (80.8 GiB) |
| pre-0731 q2 (old default) | 68/92 (73.9%) | 465.1 t/s | ~32 t/s | yes (80.8 GiB) |

The mixed build beats both q2 variants outright — same speed class, +8
questions. That part is not a trade-off.

## 2. Why not MXFP4, even though it scores higher

**A coding agent is prefill-dominated.** Each turn resends a large system
prompt, tool definitions, file contents, and a growing transcript. Generation is
a few hundred tokens; prefill is tens of thousands. Streaming wrecks exactly
that:

| | prefill @8192 | a 30,000-token turn |
|---|---|---|
| mixed q2/q4 (resident) | 488.5 t/s | **~61 s** |
| MXFP4 (streamed) | 115.7 t/s | **~260 s** |

Four minutes of latency per turn makes an agent unusable. Agents amortise
accuracy over many cheap turns rather than winning single hard problems, so
MXFP4's +4 questions out of 92 does not repay a 4× prefill cost.

Keep MXFP4 on disk for hard one-off questions where you will wait. It is the
better *model*; it is the wrong *tool* for an agent loop.

## 3. Why `--warm-weights` here specifically

On a benchmark sweep this flag is worth only +3.3% on average, and it is
slightly *negative* at long context. A server is the exception:

| ctx | without | with | delta |
|---|---|---|---|
| 2048 | 468.8 | 639.5 | **+36.4%** |
| 8192 | 488.5 | 591.7 | +21.1% |
| 16384 | 478.0 | 509.2 | +6.5% |
| 32768 | 439.2 | 436.0 | −0.7% |

It eliminates first-use page stalls. A benchmark sweep touches every page
naturally within a minute, so the gain washes out; a long-lived server pays the
cost once at startup and keeps the benefit for early requests. It also makes
throughput resistant to page-cache eviction when other processes touch the disk
(held ~744 t/s where unwarmed configs fell to ~480 under I/O pressure).

## 4. Prefix caching — probably the biggest win

`ds4-server` keeps a rax-backed KV store (`ds4_kvstore.c`) so an unchanged
system prompt and file context are reused rather than re-prefilled on every
turn. Given that prefill dominates agent latency, this plausibly matters more
than the choice of model. Worth verifying with real traffic that cache hits are
actually occurring before tuning anything else.

## 5. Claude Code wrapper

`ds4-server` exposes an Anthropic-compatible `/v1/messages` that returns proper
`tool_use` blocks and streams thinking separately from text.

**The wrapper is [`agent/claude-ds4`](agent/claude-ds4)** — the committed copy is
byte-identical to the installed `~/bin/claude-ds4`. Run `claude-ds4` instead of
`claude`. Note that `~/bin` is not on `PATH` by default.
[`agent/ds4-up`](agent/ds4-up) calls it for you and starts the server first.

Two settings in it are easy to miss:

- `CLAUDE_CODE_MAX_CONTEXT_TOKENS=100000` must match the server's `--ctx`.
  Claude Code assumes 200k for a model it does not recognise, so without this
  auto-compact fires *after* the server has already truncated. Change both
  numbers together.
- `ANTHROPIC_DEFAULT_OPUS_MODEL` and `..._HAIKU_MODEL` are set alongside
  `..._SONNET_MODEL`, so background and summarisation calls do not try to reach
  a model the server does not serve.

Other agents are documented in README ~line 1258: Codex CLI uses the Responses
wire API (`/v1/responses`), Pi uses `~/.pi/agent/settings.json`.

**Ollama is not an option here.** It speaks the OpenAI chat API, not
`/v1/messages`, so Claude Code cannot drive it without a proxy that correctly
round-trips `tool_use`/`tool_result` — and these GGUFs are built for `ds4`, a
V4-Flash-specific engine.

## 6. Settings to avoid

| setting | why not |
|---|---|
| ~~`--dspark`~~ | **RETIRED 2026-08-31 — no longer reproduces.** This said "lossless but 23–44% slower at every confidence setting tried". Re-measured on current ds4 (`main` @ `8db89fe`, PR #915 @ `88bd78a`), the same models on the same machine give **+3.8%** (this mixed q2/q4 build) and **+7.5%** (q2) at `--temp 0`. The 2026-08-08 run recorded no build SHA, so it is retired rather than reconciled. See `RECOMMENDATIONS.md` and [ds4#913](https://github.com/antirez/ds4/issues/913#issuecomment-5477787083). |
| `--quality` | ~48% prefill cost (371.7 vs 720.2 t/s) for exact kernels. |
| `--ssd-streaming` (for agents) | See §2. Fine for one-off hard problems, wrong for an agent loop. |
| `--mtp-draft` | Inert with DSpark — it drives the legacy one-stage MTP path that DSpark replaces. |

---

## Caveats — read before trusting this

**A coding benchmark has now been run — see
[`../coding/RESULTS.md`](../coding/RESULTS.md).** HumanEval 164,
pass@1, both models: mixed **96.3%**, MXFP4 **98.2%**. The 3-problem gap is
**not significant** (paired McNemar, exact, p = 0.453), and neither model wrote
a single logically incorrect program — every failure in both is the model
running past the token cap without finishing.

That is a null result, not a confirmation: HumanEval saturates at 96–98% and
cannot rank these two. It does remove the older worry that coding might fall off
a cliff relative to the general-reasoning score. Ranking them on code would need
SWE-bench Lite or a repo-local suite.

**The latency argument is solid and remains the deciding factor.** §2's
conclusion rests on measured prefill throughput. End-to-end, mixed finished the
same 164 problems in 118 min against MXFP4's 164 min, and used *less* total
energy (54.8 vs 57.9 Wh) despite drawing 24% more power — race to idle again.

**Cap generation and treat a cap-hit as a retry, not a result.** Mixed fails to
terminate on ~4.9% of prompts (8/164 hit an 8192-token cap; MXFP4 4/164). Its
median output is normal (783 tokens) — the problem is a fat right tail, so
latency is usually fast and occasionally stalled.

**Long context is now measured to 256k (issue #5) — `--ctx 100000` is
validated.** At 98304 the mixed build runs 340 t/s prefill / 25.5 t/s
generation, just past the knee where the steepest decay ends. Both resident
models reached 262144 without failure.

Memory is *not* the constraint: KV is ~13.8 KB/token, so 256k costs ~3.4 GiB and
the mixed build totals ~94 of 128 GiB. Even 512k would fit. An earlier draft
warned that the mixed build's smaller KV headroom might force a switch to
`q2_0731` at long context — that concern was unfounded.

`q2_0731` is 2–16% faster at long context (bandwidth, not capacity — it moves
fewer bytes per token), but it is 8 questions worse on the eval. Not worth the
trade. Use the mixed build at any context length.

If your agent regularly exceeds ~100k, raise `--ctx` freely; decay is graceful
(prefill −49%, generation −28% from 64k to 256k) with no cliff.

**Long-context *quality* is unmeasured.** Only speed was tested. A model can
stay fast while degrading at recall over long inputs.

**The machine throttles under sustained load.** GPU clamps to ~1274–1295 MHz
against a 1620 MHz ceiling under Heavy thermal pressure. Performance is stable
and predictable, but absolute numbers here are steady-state, not peak.

---

*Generated 2026-08-08 from `benchmarks/ds4/0731/`. See [REPORT.md](REPORT.md) for full
methodology, raw data and the measurements behind every number above.*
