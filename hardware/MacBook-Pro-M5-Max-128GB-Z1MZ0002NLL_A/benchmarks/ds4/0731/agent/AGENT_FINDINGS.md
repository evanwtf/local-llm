# Local DS4 as a coding agent backend — findings

**Machine:** MacBook Pro, Apple M5 Max, 128 GiB, macOS 26.5.2
**Model:** DeepSeek V4 Flash, mixed q2/q4 0731, resident
**Engine:** DwarfStar `ds4-server` @ `b030961`, Metal
**Date:** 2026-08-09
**Scope:** [epic #9](https://github.com/evanwtf/ds4/issues/9) — sub-issues #10, #11 complete

---

## Bottom line

**A local DeepSeek V4 Flash on this laptop drives Claude Code competently.** It
passed a four-rung capability ladder — read-only comprehension, byte-exact
editing, multi-step build-and-verify, and a five-file refactor — with **zero
retries and zero nudges**.

The predicted failure mode did not materialise. The open question is no longer
*"can it do the mechanics"* but *"can it exercise judgement under ambiguity"*,
which is untested.

**Do not yet route real work to it unconditionally.** See
[Limits](#what-this-does-not-establish).

---

## Setup that works

```sh
./ds4-server \
  -m gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf \
  --warm-weights --ctx 100000 \
  --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192 \
  --trace benchmarks/ds4/0731/agent/server_trace.log   # ⚠️ see warning below
```

> ### ⚠️ `--trace` captures full prompts — never commit its output
>
> `--trace` logs the **entire prompt of every request**. With Claude Code that
> means your global `CLAUDE.md` is written to the file on *every turn*, plus the
> contents of any file the agent reads. In this session it produced a 29 MB log
> containing the operator's `CLAUDE.md` ~1692 times, which was committed to a
> public branch before being caught.
>
> It is genuinely useful for cache diagnostics (§ below) — but treat the output
> as secret. `.gitignore` blocks `*_trace.log` and
> `benchmarks/ds4/0731/agent/server*.log`; do not override that. Omit `--trace` entirely
> unless you are actively debugging cache behaviour.
>
> No credentials appear in it — the trace records prompts, not environment
> variables — but the file is still private operational detail.

- **92.77 GiB** planned at `--ctx 100000` (90.88 model + 1.12 KV + 0.76 buffers)
- **4 s** to serving, despite warming 90.88 GiB of tensor pages
- Wrapper: [`claude-ds4`](claude-ds4), also installed at `~/bin/claude-ds4`
  (note `~/bin` is **not** on PATH by default)

### One non-obvious fix

Claude Code assumes a **200k** window for models it does not recognise, while
the server is configured for **100k**. Without correction, auto-compact fires
*after* the server has already truncated — producing confusing mid-session
failures. The wrapper sets:

```sh
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=100000
```

Keep this in sync with `--ctx`.

---

## Wire-format compatibility (#10)

All gates pass. `ds4-server`'s Anthropic endpoint is a faithful implementation:

| gate | result |
|---|---|
| `tool_use` block shape | correct `type`, `toolu_` id, valid JSON input, `stop_reason: tool_use` |
| `thinking` separation | own block, does not leak into output text |
| SSE lifecycle | `message_start` → `content_block_start/delta/stop` → `message_delta` → `message_stop` |
| streamed tool calls | `content_block_start(tool_use)` + incremental `input_json_delta` |
| end-to-end | `claude-ds4 -p` works |

---

## Capability ladder (#11)

Four rungs, cheapest first, run on a scratch branch.

| step | task | wall | result |
|---|---|---|---|
| 1 | summarise `ds4_kvstore.c` (read-only) | 109 s | **PASS** |
| 2 | rename a variable via `Edit` (byte-exact) | **32 s** | **PASS** |
| 3 | add `--version` to ds4-bench, build, verify | 345 s | **PASS** |
| 4 | same across 5 binaries via a shared macro | 437 s | **PASS** |

**Zero retries. Zero nudges.**

### Step 1 — comprehension is real, not plausible-sounding

The summary named `ds4_kvstore_evict`, `ds4_kvstore_entry_eviction_score`,
`ds4_kvstore_store_live_prefix_text`, `ds4_kvstore_try_load_text`, `kv_logf`,
`kv_cache_existing_compatible`, `touch_file`, the `KVC` magic and the embedded
SHA-1. **All seven function names verified present in the source.** It read the
file rather than generating plausible C.

### Step 2 — the predicted failure mode did not occur

This rung was expected to break. `Edit` requires a **byte-exact** `old_string`,
and the argument for failure was that IQ2_XXS quantisation damages precision —
supported by the model's 92-question eval failures clustering in **AIME (6 of
16)**, i.e. arithmetic exactness.

```
diff <(sed 's/acc/checksum/g' original.c) edited.c   →  identical
cc -c edited.c                                       →  compiles clean
```

All 6 occurrences across two scopes, zero collateral change — and the **fastest**
step of the four.

**The inference from AIME failures to editing precision was wrong.** Multiple-
choice arithmetic and byte-exact string reproduction are not the same faculty.

### Steps 3–4 — the code is good, not merely working

All five binaries verified independently (not taken from the agent's report):

```
ds4 ffc082f-dirty · ds4-server ffc082f-dirty · ds4-eval ffc082f-dirty
ds4-agent ffc082f-dirty · ds4-bench ffc082f-dirty
```

Reviewing the diff:

- placement in `parse_options()` **before** any model load, matching the
  adjacent `-h/--help` idiom exactly
- `#ifndef DS4_VERSION` fallback so files still compile standalone
- introduced a **shared** `DS4_VERSION ?= $(shell git describe --tags --always --dirty ...)`
  and refactored its own earlier per-binary macro onto it
- threaded through the `*_cpu.o` variants so `make cpu` stays consistent — a
  detail I would not have thought to check
- explanatory comment on the shared macro
- **no regressions**: `--help` intact on all four binaries

It also volunteered a correct explanation of the `-dirty` suffix and offered a
semantic-version alternative.

---

## Prefix caching works (#13, largely answered)

`claude_code_recommendations.md` called this *"plausibly a bigger win than model
choice"*. It is now measured:

| cache source | count |
|---|---|
| `anthropic-tool-output` | 84 |
| `disk-text` | 8 |
| `none` (miss) | 16 |

**7,272,442 tokens served from memory cache**, 163,840 from disk. Roughly 5:1
hits to misses across both ladders.

*(An earlier revision quoted 37/4/12 and 3.54M — those were mid-ladder counts
read before the run finished. The figures above are the totals from
[`cache_summary.txt`](cache_summary.txt), which is the authoritative extract.)* This matters more than the raw prefill number: agent
turns resend a large unchanged prefix, and it is being reused rather than
re-prefilled.

No server patching was needed — `--trace` emits `--- cache decision ---` blocks
(**but see the warning above: its output must not be committed**)
with `cache_source`, `memory_miss_reason`, `cached_tokens`, `disk_cached_tokens`,
and `/v1/messages` responses carry `cache_read_input_tokens`.

---

## Thermals

Under sustained agent load: **mean 1234 MHz / 26.4 W**, Heavy pressure 51% of
samples, **peak 66 W**.

Consistent with `REPORT.md` §8: the machine throttles to a stable clamp under
load but never loses throughput unpredictably. Agent work is *less* thermally
demanding than benchmark sweeps (26.4 W vs 40–58 W for long-context prefill),
because it alternates compute with waiting on tools.

---

## What this does NOT establish

Four for four means **the rungs were too easy**, not that DS4 is unconditionally
ready. Every task had a clear success criterion, a small diff, and no ambiguity.
Real work has none of those properties.

Untested, and gating [#15](https://github.com/evanwtf/ds4/issues/15):

- **Ambiguous requirements** — where the right change is a judgement call.
- **Debugging** — diagnosing from a stack trace or wrong output, fix location
  unknown.
- **Large context** — these touched small files; real sessions accumulate 50k+
  tokens of transcript and file content.
- **Long sessions** — longest here was 437 s. Does quality hold over an hour?
- **Recovery** — every step succeeded first try, so nothing was learned about
  whether failures are *nudge-recoverable*, which #9 named as the deciding
  property between "usable daily driver" and "toy".
- **Coding benchmark** — [#14](https://github.com/evanwtf/ds4/issues/14) is
  still open. The general-reasoning score (76/92) has not been shown to predict
  coding ability; the ladder is evidence, but n=4.

**Recommended next:** a harder second ladder before #15 writes routing rules.

> **DONE — see [`LADDER2_FINDINGS.md`](LADDER2_FINDINGS.md).** The harder ladder
> found the wall, and it is not where this document predicted. Debugging (PASS)
> and ambiguous judgement (PASS) were strong; the failures are **asserting
> runtime behaviour from reading code without executing it**, and **capitulating
> when a correct claim is challenged**. Routing guidance in that document.

---

## Predictions made, and how they scored

Recorded in #9 and #11 before running, for calibration:

| prediction | outcome |
|---|---|
| step 1 works well | ✅ correct |
| step 2 is a coin flip | ❌ wrong — clean pass, fastest step |
| steps 3–4 unlikely without hand-holding | ❌ wrong — both clean, zero nudges |
| tool-use fidelity is the binding constraint | ❌ wrong — wire format flawless |
| `Edit` exact-match is the first thing to break | ❌ wrong — it never broke |

**One of five.** The pessimism came from reasoning about quantisation damage in
the abstract rather than testing it. Worth remembering next time a capability
question comes up: the measurement was cheap and the intuition was bad.

One prediction did land, from #10: with a **weak** system prompt the model
narrated what it would do instead of calling the tool. Claude Code's real system
prompt is directive enough that it never surfaced in the ladder — but
*"narrates instead of acting"* remains a plausible failure mode for thinner
harnesses.

---

## Reproducing

- Server + wrapper: [`claude-ds4`](claude-ds4)
- Ladder transcripts: [`ladder/`](ladder/)
- Cache evidence: [`cache_summary.txt`](cache_summary.txt) — extracted from the
  raw trace so the numbers stay checkable without publishing prompt contents.
  The trace itself is gitignored and deliberately not in this repo.
- Source changes produced by the agent: branch `agent-ladder-scratch`
  (all five binaries gained `--version`; cherry-pickable)

See [`../REPORT.md`](../REPORT.md) for the model selection this builds on, and
[`../claude_code_recommendations.md`](../claude_code_recommendations.md) for the
setup rationale.
