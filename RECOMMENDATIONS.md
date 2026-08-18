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

Same binaries, same day, same tasks. Codex beats Claude Code by 12% on ds4 and
loses to it by 63% on Ollama. **Claude Code is the only client that was
consistent on both**, which is why it is the default recommendation even though
it is not the fastest anywhere.

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
broken is the worst possible profile for something you depend on.

**`qwen3.8:27b-mlx` via Ollama** — strictly dominated. MTPLX runs the identical
weights 17% faster on 68% fewer tokens. If you want Qwen3.8, use MTPLX.

**`gemma4:31b-mxfp8`** — last on every task (355.4 s), 45 GB resident. Well
behaved, never failed, no reason to choose it.

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

Item 5 decides whether this plan is any good. The rest is logistics.
