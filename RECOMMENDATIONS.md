# What to run on this Mac

**Hardware:** MacBook Pro, Apple M5 Max, 128 GiB unified memory, macOS 26.5.
**Purpose:** a working fallback for agentic coding if hosted models become
unavailable — price, policy, or otherwise.
**Evidence:** 243 agent trials, 8 backends, 3 clients, plus a hosted reference.
See [`RESULTS.md`](benchmarks/agent/RESULTS.md).

This is a fallback plan, not a daily driver plan. Measured through the same
harness, hosted Claude Opus 5 finishes the task suite in **18% of the time** the
best local pairing needs — 203 s against 1,116 s. The reason to keep the local
stack working is not speed; it is that it keeps working when nothing else does.

---

## The short answer

A local coding agent is **two choices, not one**: a model *and* a client. They
interact, so pick them as a pair.

| role | pair | why |
|---|---|---|
| **primary** | `ds4` + **Claude Code** | 15/15, predictable, the safe default |
| **fastest** | `ds4` + **Codex** | 15/15 and 12% quicker, tightest spread measured |
| **secondary** | `qwen3.6:27b-coding-mxfp8` + **Claude Code** | 31 GB, independent failure surface |

### Fastest local coding agent: DS4 + Codex

Run these commands from this checkout:

```sh
# Starts ds4-server if it is not already running (~91 GiB resident).
benchmarks/ds4/0731/agent/ds4-up start

# Codex reaches DS4 directly through its native Responses endpoint.
CODEX_API_KEY=dsv4-local codex --profile ds4
```

The `ds4` Codex profile is `$CODEX_HOME/ds4.config.toml`; it selects
`deepseek-v4-flash` at `http://127.0.0.1:8000/v1` with `wire_api =
"responses"`. `dsv4-local` is only the required non-empty local API token,
not a secret. The server stays up after Codex exits; use
`benchmarks/ds4/0731/agent/ds4-up stop` to release its memory.

Two independent stacks is the target. The June 2026 Fable/Mythos suspension was
**model-specific, not vendor-wide** — the failure mode to design against is one
thing going away, so the secondary shares no weights, engine, or maintainer
with the primary.

---

## Choose the client for the backend

The single most surprising result in this project: **the client matters as much
as the model, and no client is best everywhere.**

| backend | Claude Code | Codex | OpenCode |
|---|---|---|---|
| `ds4` | 1,110 s, 15/15 | **978 s, 15/15** | 1,235 s, **6/15** |
| `qwen3.6-coding` (Ollama) | **1,041 s, 15/15** | 1,699 s, 14/15 | not tested |
| `qwen38fnq2` (llama.cpp) | 5,236 s, **13/16** | **1,251 s, 15/15** | not tested |

Every cell is one pass through the five tasks. Same binaries, same day, same
tasks. Codex beats Claude Code by 12% on ds4, loses to it by 63% on Ollama, and
beats it by **4.2x** on llama.cpp — where it also went 15/15 against three
timeouts. Three engines, three different answers, one of them not close.

**Claude Code was the consistent choice on the first two backends**, which is
why it is still the default recommendation. The llama.cpp result is the first
that argues against it, and the cause looks like the serving path rather than
the client: Claude Code reprocesses whole prompts there — one turn re-prefilled
48972 tokens in 101 s — while Codex, which reaches the same server without the
shim in front of it, does not. Read that row as a finding about a stack, and do
not carry it to a backend where it has not been measured.

**Do not use OpenCode against ds4.** It failed 9 of 15 trials, and every failure
returned the test suite *exactly* as the excision left it — the loop stopped
believing it had finished, having changed nothing. Same model, same prompts;
Claude Code went 14/15 on the identical setup.

**That verdict is scoped to ds4, deliberately.** OpenCode has never been run
against any other backend, and this project *proved* that clients invert across
backends — Codex was 12% faster than Claude Code on ds4 and 63% slower on
Ollama. So OpenCode's 6/15 may be an ds4-pairing failure rather than a client
defect. A hand reproduction on a freshly restarted ds4-server also passed 5/5,
which points the same way. Do not generalise it to "OpenCode is bad" until it
has run somewhere else. See issue #5.

### The uncomfortable part

Both clients that work well are **proprietary and unmaintainable by you**:

| client | licence | re-installable if the vendor is cut off? |
|---|---|---|
| Claude Code | proprietary (Anthropic) | **no** |
| Codex | proprietary (OpenAI) | **no** |
| OpenCode | open source | yes — and it is the one that fails |

Both work offline once installed. Neither can be *re*-installed if its vendor
becomes unavailable, which is the exact scenario this plan hedges against. The
only client you could keep alive indefinitely is the one that performed worst.

**Practical consequence:** keep the installed binaries backed up alongside the
weights. A client you cannot reinstall is as perishable as a model you cannot
re-download. See "Not done yet" below.

---

## Primary: `ds4` (synced, `fdcf3aa`)

| | |
|---|---|
| pass rate | **15/15** |
| median wall | **140.9 s** |
| spread | 2.6× |
| output tokens | 2,120 |
| generation | 40.6 t/s |
| resident | 90.9 GiB |

Quick and dependable. Predictability is the reason it wins: a 2.6× spread means
you can plan around it. `ornith:35b` has a better median (82.3 s) and is
disqualified below.

**Start it:**

```sh
benchmarks/ds4/0731/agent/ds4-up restart
```

`ds4-server` resolves its Metal shaders relative to the working directory, so
the launcher must `cd "$DS4_ROOT"` first. It does. Do not "simplify" that away —
it has broken once already.

**It speaks all three protocols** — `/v1/messages`, `/v1/chat/completions` and
`/v1/responses` — so every client drives it natively, with no shim in the path.
That is unusual and it is why ds4 is the best-tested backend here.

**The cost is the machine.** 90.9 GiB of 128 means ds4 owns the laptop while
loaded. Fine when the model *is* the job; not fine if you also need Xcode or a
Docker stack.

**The risk worth knowing.** `ds4` is a single-architecture engine built from a
personal fork of [`antirez/ds4`](https://github.com/antirez/ds4). It runs
DeepSeek V4 Flash and nothing else — the architecture gate at `ds4.c:5809`
accepts only `glm-dsa` and `deepseek4`. If a rebuild breaks, this backend is
gone. That is precisely why there is a second entry.

---

## Secondary: `qwen3.6:27b-coding-mxfp8`

| | |
|---|---|
| pass rate | **15/15** |
| median wall | 213.5 s |
| spread | 3.4× |
| output tokens | 2,219 |
| resident | **31 GB** |

51% slower than ds4 and it leaves ~97 GiB free. Two jobs:

1. **Concurrent use.** When the machine is doing something else.
2. **Independent failure surface.** Ollama and ds4-server share no code. A
   fallback whose two options fail together is one option.

**Use Claude Code with it.** Codex was 63% slower on this backend and produced
the only unparseable-file failure recorded in this project.

**Start it:**

```sh
./claude-ollama start
```

---

## Watch: `Qwen3.8-27B-MTPLX-Optimized-Speed`

| | |
|---|---|
| pass rate | **16/16** |
| median wall | 226.4 s |
| output tokens | **2,026** — lowest of any backend |
| spread | 3.5× |
| resident | 26.8 GiB |

The best speed-per-GB in the field, and the same weights run 17% faster with
68% fewer tokens under MTPLX than under Ollama. Not promoted because its trials
got faster across rounds (1,780 → 1,253 → 1,021 s) for reasons that remain
**unexplained** — the two obvious mechanisms were both disproved by the server's
own counters. If the trend is real its warm median is ~193 s, which would place
it second.

One cold-restart run settles it. Until then its placement is provisional.

```sh
mtplx quickstart --model Youssofal/Qwen3.8-27B-MTPLX-Optimized-Speed \
    --profile turbo --port 8010 --yes
```

---

## What not to bother with

**`ornith:35b`** — fastest median in the field (82.3 s) and the only backend
that has ever failed under Claude Code: 13/15, twice on the *easiest* task, with
a **30.4×** spread and one run of 20.4 minutes. Fast-on-average and occasionally
broken is the worst possible profile for something you depend on. Superseded by
1.5, below.

**`ornith-1.5:35b`** — the successor, 54 trials, same quant and engine so only
the generation moves. **It fixed the tail and not the reliability.**

| | 1.0 | 1.5 Claude Code | 1.5 Codex |
|---|---|---|---|
| pass | 13/15 | 23/27 (85%) | 25/27 (93%) |
| median | 82.3 s | 100.3 s | 107.5 s |
| spread | **30.4×** | **6.6×** | 7.9× |
| suite | 488 s | 680 s | 622 s |

The catastrophic tail is gone — no 20-minute runs, spread under 8×, no
timeouts — and the suite total *beats ds4* (774 s / 975 s). The failure that blacklisted 1.0 —
`mbox-strip-envelope` — is fixed outright: 8/8 across both clients. But 85% is not 100%, and `ds4` is 31/31.

What did change is the shape of failure. Four of six are near misses — `1
failed, 54 passed`, `1 failed, 16 passed` — code written and one test wrong.
1.0 failed by giving up; that mode still appears twice (`mbox-scan` left at the
control state, Codex quitting `parser-date` after 27.5 s) but it is no longer
the rule. Near misses are a quality signal this suite is normally too easy to
produce, which makes it the most interesting backend here for issue #4.

**Use it when speed matters and a retry is cheap. Do not put it in the fallback
slot.** A fallback exists for the day nothing else works, and 85% is the wrong
number for that day. See also the lineage caveat: it is `qwen35moe`.

**`qwen3.8:27b-mlx` via Ollama** — strictly dominated. MTPLX runs the identical
weights 17% faster on 68% fewer tokens. If you want Qwen3.8, use MTPLX.

**`gemma4:31b-mxfp8`** — last on every task (355.4 s), 45 GB resident. Well
behaved, never failed, no reason to choose it.

---

## Benchmarked: `Qwen3.8-Flash-Next` at 2-bit

Released 2026-08-26 as the preview of the Qwen4 architecture; measured the same
day. **It runs, it passes, and it is the slowest backend in this project.** Keep
it as evidence about the architecture, not as a fallback candidate.

Full numbers in [`benchmarks/llamacpp/RESULTS.md`](benchmarks/llamacpp/RESULTS.md).

| | |
|---|---|
| what fits | Unsloth `UD-Q2_K_XL`, 78.9 GB, **77.9 GiB resident** |
| engine | llama.cpp PR #27742 — mainline does not know `qwen4exp` |
| Codex | **15/15**, suite 1,251s |
| Claude Code | **13/16**, suite 5,236s, 3 timeouts |
| the field | last of six local backends; `ds4` medians 174.2s |

**Ollama cannot serve it here at all.** Its only fitting tag is 112 GB nvfp4,
which peaks at **126.51 GiB against a 107.0 GiB Metal budget** and dies on the
first agent-sized prompt with `kIOGPUCommandBufferCallbackErrorOutOfMemory`.
That peak is fixed — unchanged at 262144, 65536 and 32768 context, at
`OLLAMA_NUM_PARALLEL=1`, and with a q8_0 KV cache. It is weights plus the
resident 51B n-gram table, not KV, so no serving knob reaches it, and
`iogpu.wired_limit_mb` cannot close a 19.5 GiB gap on a 128 GiB machine.

**A 6B-active MoE is not a small model.** Active parameters set the speed; total
resident weight sets whether it runs at all, and this architecture adds a 51B
lookup table that is resident and does no arithmetic. Read "A6B" as a throughput
claim, never as a memory one.

**Codex is 4.2x faster than Claude Code against it, on every one of five
tasks** — far beyond the 12% and 63% client gaps measured on ds4 and Ollama.
Both clients emit comparable output tokens, so this is not the model working
harder for one of them. The cost is re-prefill: Claude Code spends a median of
84.1 seconds per turn, and the server log shows single turns reprocessing 48972
tokens from scratch in 101.3s while neighbouring turns reprocess 2800 in five.
The prompt cache works, then something invalidates it. `--cache-reuse` is the
obvious thing to try and has not been tried. All three Claude Code failures are
timeouts, which follows: 18 turns at 84s is 25 minutes before anything hard
starts.

**The transferable finding is that KV cache is nearly free on this
architecture.** The first configuration served 65536 context across llama.cpp's
default four KV slots, and Claude Code thrashed autocompact — three compactions
in three turns. Dropping to `-np 1` bought **131072 context for 1.7 GiB**. The
GDN + sparse-attention hybrid keeps almost no per-token KV state, so on a
memory-bound Mac the trade is fewer slots and a much longer window. Worth
testing against the other backends here.

**The 2-bit quant is an untested risk.** These tasks are too easy to expose it —
nearly every backend passes them, which is issue #4. If a harder benchmark shows
this backend failing where others pass, suspect the quant before the model.
Unsloth's "outperforms Claude-Opus-4.6 (Max)" claim is not something this suite
can speak to either way.

---

## Too big for this machine

A verdict on 128 GiB, not on the model.

**`GLM-5.3-Flash`** — 320B total, 18B active, MIT, released 2026-08-26, and
strong on paper (Terminal-Bench 2.1 84.3, DeepSWE 63.4). Never got as far as a
download. The smallest published local quant is a 4-bit MLX at **177.5 GB**;
Ollama lists only a `glm-5.3-flash:cloud` tag, with no local weights at all.

Two efforts are in flight and neither helps this machine. **DFlash 2** for the
model is announced but unreleased, and DFlash is speculative decoding — it makes
a model faster, not smaller. **antirez** is converting GGUF quants from the FP8
release and reports picking Q4_K over MXFP4 on conversion error; a 4-bit quant
of a 320B model lands near the 177.5 GB already measured. Revisit only if
something under ~100 GB appears — the Qwen3.8-Flash-Next result above shows a
2-bit quant is a serving path, so that is the tier to watch for.

---

## Verified

**Claude Code works fully offline.** Tested 2026-08-17 with all outbound traffic
blackholed and only loopback reachable: it read a file, made an edit, and exited
0 against a local server. The client is not the weak link — no auth callout
blocks it.

It prints two harmless notices: a warning that connectors are disabled, and
`[claude-code:unrecognized_model]`. Both are cosmetic.

**Not yet verified offline: Codex and OpenCode.** Both were only ever run with
the network up. Codex in particular is worth checking, since it is the fastest
pairing on ds4 — and it warns on every run that it has no metadata for local
models, which suggests it expects to reach a catalogue somewhere.

---

## Not done yet

The gaps between "benchmarked" and "actually a fallback":

1. **Backups are configured but unverified.** Time Machine has two destinations
   and excludes none of the relevant paths — `~/.ollama`, `~/.mtplx/models`,
   `~/git/ds4/gguf`, `~/.codex`, `~/.opencode`, `~/.local/bin` all report
   `[Included]`. But `tmutil latestbackup` could not mount a destination, so no
   completed backup is confirmed. A direct NAS copy of the essential ~143 GB is
   planned: the 91 GB ds4 quant actually in service, ~31 GB for
   `qwen3.6:27b-coding-mxfp8`, 20 GB for MTPLX, and ~900 MB of clients and
   toolchain. Issue #6.
2. **Neither is the toolchain, and that now includes the clients.** Ollama,
   MTPLX, the MLX wheels, the ds4 source, *and* the Claude Code and Codex
   binaries are all reacquired from the network today. Weights you cannot serve
   are not a fallback; nor is a model you cannot drive.
3. **Codex and OpenCode are unverified offline.** One test each, ~10 minutes.
4. **No cold-start runbook.** The Metal-shader trap above was rediscovered
   under pressure once. Write the procedure down while it is calm.
5. **Quality is unmeasured at real difficulty.** Nearly every backend and client
   scores near-perfect, so these tasks cannot tell you which is *better* — only
   that each can restore one deleted function. Issue #4.
6. **The Ollama rows predate the installed engine.** Ollama went 0.32.15 →
   **0.33.1** on 2026-08-26 to get MLX support for Qwen3.8-Flash-Next, which
   then did not fit. Every Ollama row in `results.jsonl` was recorded under
   0.32.x. The per-row env capture keeps them readable, but `qwen36coding` — the
   secondary — has not been re-run under the engine now installed.

Item 5 decides whether this plan is any good. The rest is logistics.
