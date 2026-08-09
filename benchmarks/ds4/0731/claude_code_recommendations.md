# Backing Claude Code with a local DeepSeek V4 Flash

Practical setup guide derived from the benchmarks in [REPORT.md](REPORT.md).
Machine: MacBook Pro M5 Max, 128 GiB, macOS 26.5.2. Engine: DwarfStar `ds4`
built from `main @ b030961`.

---

## TL;DR

Use the **mixed q2/q4 0731** build, **resident**, served by **`ds4-server`**
with **`--warm-weights`**.

```sh
./ds4-server \
  -m gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf \
  --warm-weights \
  --ctx 100000
```

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
`tool_use` blocks and streams thinking separately from text. Per README ~line
1258, save as `~/bin/claude-ds4`:

```sh
#!/bin/sh
unset ANTHROPIC_API_KEY

export ANTHROPIC_BASE_URL="http://127.0.0.1:8000"
export ANTHROPIC_AUTH_TOKEN="dsv4-local"
export ANTHROPIC_MODEL="deepseek-v4-flash"

export ANTHROPIC_CUSTOM_MODEL_OPTION="deepseek-v4-flash"
export ANTHROPIC_CUSTOM_MODEL_OPTION_NAME="DeepSeek V4 Flash local ds4"
export ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION="ds4.c local GGUF"
export ANTHROPIC_DEFAULT_SONNET_MODEL="deepseek-v4-flash"

exec claude "$@"
```

Other agents are documented in the same README section: Codex CLI uses the
Responses wire API (`/v1/responses`), Pi uses `~/.pi/agent/settings.json`.

## 6. Settings to avoid

| setting | why not |
|---|---|
| `--dspark` | Lossless but **23–44% slower** generation at every confidence setting tried. The cost is speculation itself, not instrumentation (`--dspark-strict` matches baseline). |
| `--quality` | ~48% prefill cost (371.7 vs 720.2 t/s) for exact kernels. |
| `--ssd-streaming` (for agents) | See §2. Fine for one-off hard problems, wrong for an agent loop. |
| `--mtp-draft` | Inert with DSpark — it drives the legacy one-stage MTP path that DSpark replaces. |

---

## Caveats — read before trusting this

**No coding benchmark was run.** The 92-question set is GPQA Diamond,
SuperGPQA, AIME2025 and one COMPSEC category; only the last touches code, and it
is security analysis of C snippets rather than code generation. The 82.6% vs
87.0% ranking measures *general reasoning*. This document extrapolates to coding
on the assumption the two correlate, which is untested here.

**The latency argument is solid; the quality ranking is inherited.** §2's
conclusion rests on measured prefill throughput and holds regardless of coding
ability. The claim that the mixed build is the best *resident* model rests on
the general-reasoning eval.

**Long-context behaviour is unmeasured beyond 64k** (issue #5). Agents
accumulate context aggressively, and the mixed build uses 90.9 of 128 GiB,
leaving less KV headroom than the q2 builds. `--ctx 100000` above is a
reasonable starting point, not a validated ceiling.

**The machine throttles under sustained load.** GPU clamps to ~1274–1295 MHz
against a 1620 MHz ceiling under Heavy thermal pressure. Performance is stable
and predictable, but absolute numbers here are steady-state, not peak.

---

*Generated 2026-08-08 from `bench-0731/`. See [REPORT.md](REPORT.md) for full
methodology, raw data and the measurements behind every number above.*
