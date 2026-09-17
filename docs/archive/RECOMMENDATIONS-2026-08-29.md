# What to run on this Mac

**Hardware:** MacBook Pro, Apple M5 Max, 128 GiB unified memory, macOS 26.5.
**Purpose:** a working fallback for agentic coding if hosted models become
unavailable — price, policy, or otherwise.
**Evidence:** 558 agent trials, 17 backends, 3 clients, plus a hosted reference.
See [`RESULTS.md`](benchmarks/agent/RESULTS.md). Last corrected **2026-08-29**.

> [!WARNING]
> **Every claim in this document is unproven until re-derived (#59).**
>
> Two published claims inverted under scrutiny in three days: `--dspark` went
> from "23–44% slower" to **+3.8%/+7.5%** (#58), and "do not use OpenCode"
> turned out to be an artifact of the agent reading an un-excised copy of the
> answer (#54). Two inversions is a pattern.
>
> Until #59's triage lands, read as follows:
>
> | tier | what | how to read |
> |---|---|---|
> | **A** | correctness / pass rates from **Claude Code or Codex** | trustworthy — 0 escapes in 106 and 135 retained logs, and pass/fail is robust to thermal drift |
> | **B** | anything resting on **wall time, tok/s, or a speed comparison** | **provisional.** ~4% of throughput is lost to 15 min of sustained load (#58); Claude Code re-prefills ~10k tokens/turn (#50); a 3-trial median carries ±27.9% (#23) |
> | **C** | anything about **OpenCode**, or client-to-client *timing* | **retired.** Do not cite |
>
> This includes the README's headline claim that rankings invert across
> clients: it rests entirely on cross-client wall time gathered while one
> client was working in the wrong directory. It may be true. It is not
> currently supported.

> **Read the series boundary.** Codex moved 0.148 → 0.150.1 and llama.cpp moved
> from PR #27742 to mainline `d7bd3bfca` on 2026-08-28. Numbers either side are
> **not pooled**, per the policy in `AGENTS.md`. Where a figure below is from
> one series only, it says so.

This is a fallback plan, not a daily driver plan. Measured through the same
harness, hosted Claude Opus 5 finishes the task suite in **21% of the time** the
best reliable local pairing needs — 203 s against 975 s. The reason to keep the
local stack working is not speed; it is that it keeps working when nothing else
does.

---

## The short answer

A local coding agent is **two choices, not one**: a model *and* a client. They
interact, so pick them as a pair.

| role | pair | why |
|---|---|---|
| **primary** | `ds4` + **Claude Code** | **55/55** on Python, **9/9** on Swift, and the only pair that wins on both token volume and rate. Lower bound **0.935**. Codex on the same weights is equal on Python but **2.14x slower on Swift** — prefer Claude Code |
| **secondary** | `qwen3.6:27b-coding-mxfp8` + **Claude Code** | 30/30, 31 GB, independent failure surface |
| **do not** | anything + **OpenCode** | 13/29 on the same model both other clients pass. See below |

**Corrected 2026-08-28.** This table used to name Codex "fastest" on a 12% gap
measured over 15 trials. At 76 and 36 trials the gap is 7 seconds across a
five-task suite, and the confidence intervals overlap almost completely. The
honest statement is that on `ds4` the two clients are indistinguishable.

### The local coding agent to run: DS4 + Claude Code

Run these commands from this checkout:

```sh
# Starts ds4-server if it is not already running (~91 GiB resident, up in ~26s).
benchmarks/ds4/0731/agent/ds4-up start

# Claude Code against the local model. Every model alias must be set: the
# client picks a different one per role, and an unset alias silently reaches
# for a hosted model.
ANTHROPIC_BASE_URL=http://127.0.0.1:8000 \
ANTHROPIC_AUTH_TOKEN=dsv4-local \
ANTHROPIC_MODEL=deepseek-v4-flash \
ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-flash \
ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-flash \
ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-flash \
  claude
```

**Unset `ANTHROPIC_API_KEY` in that shell.** If it is set it takes precedence
and the session silently runs against the hosted API — `run.py` pops it for
exactly this reason.

Codex on the same weights remains a supported alternative and is equal on
Python, but it is **2.14x slower on Swift** and its verbosity gap widens
further on harder tasks (#45), so it is no longer the default:

```sh
CODEX_API_KEY=dsv4-local codex --profile ds4
```

The `ds4` Codex profile is `$CODEX_HOME/ds4.config.toml`; it selects
`deepseek-v4-flash` at `http://127.0.0.1:8000/v1` with `wire_api =
"responses"`. `dsv4-local` is only the required non-empty local API token,
not a secret. The server stays up after either client exits; use
`benchmarks/ds4/0731/agent/ds4-up stop` to release its memory.

Two independent stacks is the target. The June 2026 Fable/Mythos suspension was
**model-specific, not vendor-wide** — the failure mode to design against is one
thing going away, so the secondary shares no weights, engine, or maintainer
with the primary.

---

## Choose the client for the backend

The single most surprising result in this project: **the client matters as much
as the model, and no client is best everywhere.**

| backend (engine) | Claude Code | Codex | OpenCode |
|---|---|---|---|
| `ds4` (ds4-server) | 982 s, **75/76** | 975 s, **36/36** | **13/29** |
| `qwen3.6-coding` (Ollama) | **1,054 s, 30/30** | 1,797 s, 14/15 | not tested |
| `qwen38fnq2` (llama.cpp) | 5,236 s, **13/16** | **1,251 s, 15/15** | not tested |
| `qwen38fnq3` (llama.cpp) | not run | **896 s, 15/15** | not tested |
| `glm53` (llama.cpp) | not run | **1,362 s, 15/15** | not tested |

Each cell pools every trial of that pairing; suites are the sum of per-task
medians. The `ds4` row pools both wire protocols (`ds4` and `ds4anthropic` are
the same weights on the same server).

> **Sampler caveat, 2026-08-29 (#28, #36).** Every row in this table was
> measured at whatever sampler its launcher happened to set, and those differ:
> `llamacpp-up` hardcoded `top_p 0.95`, Ollama fell back to its own `0.9`, and
> neither set `repeat_penalty`. A measured "+66% engine difference" collapsed to
> **+5-10%** once all four parameters were matched, and `top_p` alone moves a
> pass rate from **20/21 to 7/15**. **Cross-engine rows here are provisional.**

**The client effect is real, but it is engine-specific, and it is not a ranking
of clients.** On ds4 the two proprietary clients are indistinguishable — 7
seconds apart over a five-task suite, with overlapping intervals. On Ollama
Claude Code is 41% faster. On llama.cpp Codex is **4.2x** faster and Claude Code
times out. Three engines, three different answers.

**The llama.cpp gap is a property of the serving path, not of the client.**
Claude Code reprocesses whole prompts there — one turn re-prefilled 48,972
tokens in 101 s — while Codex, which reaches the same server without the shim in
front of it, does not. All three Claude Code failures on that backend are
timeouts, which follows directly. Read that row as a finding about a stack, and
do not carry it to a backend where it has not been measured. Issue #14.

**Corrected 2026-08-28.** An earlier revision of this table read "Codex beats
Claude Code by 12% on ds4" from 15 trials per cell, and reported `qwen38fnq2`
against Claude Code as 13/13. Three timeouts had been dropped by a reader that
tested `"passed" in row`; a timeout writes no `passed` key. The OpenCode cells
were computed over fourteen rows already marked `confound`. Both readers are
fixed and the numbers above are recomputed.

**Do not use OpenCode against ds4.** It failed 16 of 29 trials, and the failures
returned the test suite *exactly* as the excision left it — the loop stopped,
believing it had finished, having changed nothing. Same model, same server, same
prompts; the other two clients went 75/76 and 36/36 on it.

**That verdict is scoped to ds4, deliberately.** OpenCode has never been run
against any other backend, and this project *proved* that clients invert across
backends. So 13/29 may be a ds4-pairing failure rather than a client defect. A
hand reproduction on a freshly restarted ds4-server passed 5/5, which points the
same way. Do not generalise it to "OpenCode is bad" until it has run somewhere
else. See issue #5.

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

## Primary: `ds4`

**124/141 lifetime across three server builds and three clients** — and 111/112
once OpenCode is set aside. It is the most-tested backend here by a wide margin,
and the only one whose reliability clears 90% at 95% confidence.

| build | trials | median wall |
|---|---|---|
| pre-sync `5be6b6c` | 15/15 | 164.4 s |
| synced `fdcf3aa` | 16/16 | 140.9 s |
| post-merge `399acbb` | 15/15 | 264.3 s |

| | |
|---|---|
| resident | 90.9 GiB |
| output tokens | 2,120 |
| generation | 40.6 t/s |

Dependable across every build it has been run on. **Its wall time is not
dependable**: the medians above range 141 s to 264 s on identical work, and a
single task has run 430.1 s and 145.7 s back to back. Plan around the pass rate,
not the clock, until #26 explains the swing. `ornith:35b` has a better median
(82.3 s) and is disqualified below.

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
| pass rate | **30/30** with Claude Code |
| median wall | 215.2 s |
| spread | 3.4× |
| output tokens | 2,219 |
| resident | **31 GB** |

Slower than ds4 and it leaves ~97 GiB free. Two jobs:

1. **Concurrent use.** When the machine is doing something else.
2. **Independent failure surface.** Ollama and ds4-server share no code. A
   fallback whose two options fail together is one option.

**Use Claude Code with it.** Codex was 70% slower on this backend (1,797 s
against 1,054 s), went 14/15, and produced the only unparseable-file failure
recorded in this project. This is the backend that inverts the llama.cpp
result — which is why neither client is recommended everywhere.

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

**`qwen3.8:27b-mlx` via Ollama** — dominated, **provisionally**. MTPLX was
measured running the identical weights 17% faster on 68% fewer tokens.

> **That claim is now suspect and has not been re-checked.** It is the same
> shape as two results that turned out to be artifacts: a token-count difference
> attributed to the serving stack. #28 found a "+66% engine difference" between
> llama.cpp and Ollama on byte-identical weights that collapsed to +5–10% once
> four sampler parameters were matched — the engines decode at the same rate.
> The MTPLX comparison predates any sampler pinning, and MTPLX's sampler was
> never recorded. **A fewer-tokens-therefore-faster result is a sampler
> hypothesis until the samplers are shown to match.**

**`gemma4:31b-mxfp8`** — last on every task (355.4 s), 45 GB resident. Well
behaved, never failed, no reason to choose it.

---

## Benchmarked: `Qwen3.8-Flash-Next` — run it at 3-bit

Released 2026-08-26 as the preview of the Qwen4 architecture. **With Codex and
the 3-bit quant it is 15/15 and the second-fastest backend measured** — a real
fallback candidate, and the only one whose lineage is independent of both
DeepSeek and GLM.

Full numbers in [`benchmarks/llamacpp/RESULTS.md`](benchmarks/llamacpp/RESULTS.md).

| | `UD-Q3_K_XL` — use this | `UD-Q2_K_XL` — superseded | `AD-4.27bpw-Q4_K_M-M64` — tested, rejected |
|---|---|---|---|
| file size | 83.8 GiB | 77.9 GiB | 88.0 GiB |
| Codex | **15/15, suite 995 s** | 15/15, suite 1,251 s | 16/16, suite **1,276 s (+28%)** |
| Claude Code | not run | 13/16, suite 5,236 s, 3 timeouts | not run |

Engine is llama.cpp PR #27742 for both — mainline does not know `qwen4exp`.

**4-bit was tested and rejected, 2026-08-28.** AtomicChat's `-M64` build splits
the 51B n-gram PLE table into its own shard so it can be paged from SSD (#33).
It loads, it is 16/16, and it is **28% slower** than 3-bit on an identical stack
— four of five tasks +26% to +35%. The memory saving never appeared because
llama.cpp's mmap already makes every weight page evictable: physical footprint
is ~5 GB against 92 GiB of RSS whether or not the table is pinned to CPU. The
q3 suite figure above is the same-stack re-run (995 s), not the older 896 s,
which was measured on a different engine build and client.

**The bigger quant is the faster one, by 28.4%, on all five tasks.** That is not
a rounding artifact and it is not what a tokens/sec reading predicts: Q3 decodes
*slower* per token than Q2. Re-prefill dominates the agent loop (#14), so the
quant that needs fewer turns wins the wall clock even while losing the token
rate. Anything that ranks these models on generation speed gets this backwards.

**Corrected 2026-08-28.** This section previously read "it runs, it passes, and
it is the slowest backend in this project — keep it as evidence about the
architecture, not as a fallback candidate." That verdict was drawn from the
Claude Code column of a single quant. The 2-bit weights were never necessary
either; `UD-Q3_K_XL` always fit.

**The 2-bit quant is an untested risk, and 3-bit only narrows it.** These tasks
are too easy to expose quantisation damage — nearly every backend passes them,
which is issue #4.

**Ollama cannot serve it here at all.** Its only fitting tag is 112 GB nvfp4,
which peaks at **126.51 GiB against a 112.00 GiB Metal budget** and dies on the
first agent-sized prompt with `kIOGPUCommandBufferCallbackErrorOutOfMemory`.
That peak is fixed — unchanged at 262144, 65536 and 32768 context, at
`OLLAMA_NUM_PARALLEL=1`, and with a q8_0 KV cache. It is weights plus the
resident 51B n-gram table, not KV, so no serving knob reaches it, and
`iogpu.wired_limit_mb` cannot close a 14.5 GiB gap on a 128 GiB machine, even
raised to its practical maximum (#30).

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

## Benchmarked: `GLM-5.3-Flash` — on the wrong stack

> **Methodology correction, 2026-08-29.** Everything below was measured with
> **Unsloth's `UD-Q2_K_XL` on llama.cpp PR #27752, through the shim.** That is
> not how this model is meant to be run: antirez ships GLM for Mac through **ds4
> (DwarfStar)** with his own GGUF layout, and ds4 is explicitly *not* a general
> GGUF loader. The two artifacts are not interchangeable — his GGUF declares
> `glm5-next`, Unsloth's declares `glm5next`, and neither engine reads the
> other's. **Treat the numbers below as a property of that stack, not of the
> model.** Re-test is #38.
>
> The result that exposed it: **`glm53 x claude` times out at 3,600 s** on the
> task Codex does in 133.1 s. Measured cause is 12.11 t/s decode with 5–7k
> tokens per turn — not re-prefill; the prompt cache was working.



320B total, 18B active, MIT, released 2026-08-26. **15/15 with Codex, zero
patches, zero warnings.** It is the slowest of the passing backends and it is
the third independent model lineage in the fallback plan, which is the reason to
keep it.

| | |
|---|---|
| what fits | Unsloth `UD-Q2_K_XL`, **100.6 GiB resident** |
| engine | llama.cpp PR #27752 — declares `glm5next`, ships the converter |
| Codex | **15/15**, suite 1,362 s |
| Claude Code | not run |

**It only fits because the Metal ceiling was raised.** At 100.6 GiB resident it
had under 7 GiB of headroom against the stock 107.52 GiB budget.
`sudo sysctl iogpu.wired_limit_mb=114688` lifts that to 112.00 GiB (#30). The
setting does **not** survive a reboot and is not yet persisted, so a fresh boot
will fail to load this model.

**The quant and the engine are a matched pair.** Unsloth's `UD-Q2_K_XL` with
PR #27752 works first time. antirez's own `GLM-5.3-Flash-Q2.gguf` (90 GB) loads
in no engine here, and PR #27773 loads a model that emits noise at temperature 0
— it ships no converter, so its loader reads metadata keys nothing in its own
tree writes. That cost hours; see #25. **Coherence-check at temperature 0 before
trusting any new load.**

**Corrected 2026-08-28.** This model was filed under "too big for this machine"
on the basis of a 177.5 GB 4-bit MLX quant, which is neither the smallest nor
the recommended one.

---

## GLM-5.3-Flash on the supported stack — better, still blocked

Re-run on **ds4 (DwarfStar)**, which is how antirez actually ships GLM for Mac
(#38). The stack change is dramatic and does not make the model usable:

| | llama.cpp + Unsloth | **ds4 + antirez** |
|---|---|---|
| same prompt, temp 0 | ~76 s, 854 tokens | **3.2 s, 47 tokens** |
| decode | 12.11 t/s | **27.8 t/s** |
| `mbox-strip-envelope` × Claude Code | timeout at 3,600 s | **PASS in 166.9 s** |

**18x fewer tokens and 2.3x the decode rate.** The reasoning explosion that
timed out Claude Code belongs to the llama.cpp+Unsloth path, not the model. The
two GGUFs are not interchangeable and one hyphen says so — antirez's declares
`glm5-next`, Unsloth's `glm5next`, and neither engine reads the other's.

**It is still unusable, for two reasons that are upstream and already reported:**

- [ds4#569](https://github.com/antirez/ds4/issues/569) — the GLM tool-call
  parser stringifies every argument, so Codex sees `"false"` where a boolean is
  declared. **78 parse errors in one trial**, 53 minutes, no recovery. Open
  since 2026-07-17 and not specific to 5.3.
- [ds4#816](https://github.com/antirez/ds4/issues/816) — stateless clients never
  reuse the KV session, so every turn re-prefills. At 40k context that is ~110 s
  per turn: **529 cache stores, 0 hits.**

Neither is ours to fix. **Do not put GLM in the plan until both land.**

### Upstream got there first, on this exact machine (2026-08-29)

[ds4#892](https://github.com/antirez/ds4/pull/892) brings GLM-5.3 Flash up on an
**M5 Max 128 GB** — the same hardware this document describes — and publishes
measurements. Q2 GGUF, ctx 8192, greedy `--temp 0`:

| mode | prefill | decode |
|---|---|---|
| serial | 76–80 t/s (474 t/s @ 4500-tok prompt) | 33.0 t/s |
| `--mtp` (width 2) | same | **40.5 t/s** |

**MTP acceptance 89.6%**, greedy goldens byte-identical across serial, `--mtp`
and widths 3/4/6. Their serial decode of 33.0 t/s is in the same neighbourhood
as the 27.8 t/s we measured, which is a useful cross-check on our own numbers.

Three things this changes:

- **`--mtp` is worth +23% decode and it works on this hardware.** That is the
  only lever anyone has demonstrated against decode rate here, and decode rate is
  what the "too big" tier below says is the real wall.
- **Do not spend time on speculative width above 2.** #892 measured it: depth-2
  acceptance collapses to ~45% and every reject costs a KDA restore plus prefix
  replay. W=3 → 30.6 t/s, W=4 → 20.8, W=6 → 16, all worse than width 2.
- **A 4500-token prompt succeeded at ctx 8192**, above the 4096 boundary
  [ds4#890](https://github.com/antirez/ds4/issues/890) describes and we recorded
  as a hard blocker. Either #890 is narrower than we wrote down or the branch
  already fixes it. **Unreconciled — do not treat 4096 as settled either way.**

**None of this makes GLM usable as an agent here.** #569 and #816 are untouched,
and they are what break the agent loop. What changed is that GLM-5.3 is now worth
measuring for *decode rate* on this machine, which is a different question from
whether it can drive Claude Code.

### Merge status: not on main. Use the branch. (verified 2026-08-30)

**GLM-5.3 is not merged to ds4 main.** Verified locally rather than assumed:
`upstream/main` carries exactly one GLM-5.3 commit — `8db89fe download: add GLM
5.3 Flash models` — while `glm-5.3-flash` is **13 commits ahead of main and 0
behind**. Everything that runs the model is on the branch, which is a clean
fast-forwardable superset rather than a divergent experiment.

```sh
git clone https://github.com/antirez/ds4 && cd ds4
git checkout glm-5.3-flash
./download_model.sh glm53-q2        # ~90 GB, fits a 128 GB Mac
make
./ds4 -m gguf/GLM-5.3-Flash-Q2.gguf --ctx 32768
```

Q4 on one Mac needs `--ssd-streaming`; Q4 across two 128 GB Macs needs the RDMA
tensor-parallel path (~37 t/s generate, ~500 t/s prefill) and is not this
machine's configuration.

Vision, vector steering, ROCm and better Metal support are promised for the real
merge and are **not shipped**. Vision is out of scope here in any case — this
document is about the coding-agent use case — so **branch activity is a poor
proxy for progress on anything measured here.**

**`--ctx 32768` in the maintainer's own recipe** is a third datapoint against
[ds4#890](https://github.com/antirez/ds4/issues/890)'s ">4096 fails": #892 ran a
4500-token prompt at ctx 8192, and this allocates 32k. **4096 is not a settled
boundary.**

### The primary may be leaving decode on the table (#48)

**Our own GGUF does not satisfy the rule antirez states below**, and the gap is
measurable. Read with `scripts/gguf_meta.py`; the computed 90.31 GiB matches the
file on disk.

| | GiB per token | share of per-token traffic |
|---|---|---|
| dense weights | 8.20 | 81.0% |
| routed experts (**82.11 GiB on disk**) | 1.92 | 19.0% |
| **of the dense: F16 tensors** | **2.04** | **20.2%** |

**91% of the file is routed experts and they are 19% of the traffic. The F16
tensors are 2.3% of the file and 20.2% of the traffic.** The filename advertises
`AProjQ8-SExpQ8-OutQ8` and those *are* Q8 — but the compressor, indexer and
hyper-connection paths were left at F16 (`attn_compressor_*`,
`indexer_compressor_*`, `indexer.proj`, `indexer.attn_q_b`, `hc_attn_fn`,
`hc_ffn_fn`, `token_embd`).

**Requantizing those 359 tensors to Q8_0 would cut per-token traffic by 9.5%.**
[@ShankPeople](https://x.com/ShankPeople/status/2093826778011676775) measured
**+20% decode at the same quality** from exactly this change on GLM-5.3, and
antirez replied that the BF16 choice was *"kinda of an inefficient choice"*.

**Do not treat that as a promised 9.5% speedup.** ds4#892, on this same hardware,
finds decode is **dispatch-bound rather than bandwidth-bound** — a 2-token
forward costs only 1.23x a 1-token forward. The two claims can both be true, and
which one dominates here is unmeasured. **Either result is worth having:** a
faster primary, or the bandwidth hypothesis dies and speculative decoding (#39)
becomes the only remaining lever on decode rate.

### The quant principle — and we are already running it

antirez's quant notes (2026-08-30) describe where GLM's bits should go:

- the official "FP8" files keep the **KDA projection and head in BF16**; those
  can go to **Q8** and save ~4 GB
- **routed experts can stay extremely low-bit**, because most traffic does not
  flow through those tensors — but the trick only works **if everything else
  stays Q8**

**That is the exact recipe our primary already uses.** The DeepSeek GGUF we run
is:

```
Layers37-42Q4KExperts · OtherExpertLayersIQ2XXSGateUp · Q2KDown
                      · AProjQ8 · SExpQ8 · OutQ8
```

Attention projection, shared experts and output at **Q8**; routed experts down at
**IQ2_XXS** gate/up and **Q2_K** down. Low-bit where the traffic is not, Q8
everywhere it is — and that model scores **55/55** here.

**So the principle is not speculative on this machine; it is what makes the
primary work.** Two consequences:

- A GLM-5.3 quant built this way is the one worth testing, not a uniform q2 or a
  uniform q4. This **replaces** the "q2 resident vs q4 streamed" ladder in #40,
  whose q4-resident arm is dead anyway on the 110 GiB budget below.
- It is a reason to expect a mixed-precision GLM to behave better than our
  earlier uniform-quant results suggested — those were measuring the quant as
  much as the model.

### Measured on the branch, 2026-08-30 — it runs, and it is coherent

Built at branch tip `767e517` and ran antirez's own `GLM-5.3-Flash-Q2.gguf`:

| run | ctx | prefill | generation |
|---|---|---|---|
| short prompt | 32768 | 78.88 t/s | **35.92 t/s** |
| ~7–8k token prompt | 32768 | **460.21 t/s** | 29.57 t/s |
| coherence prompt | 8192 | 101.90 t/s | 35.86 t/s |

93.21 GiB planned at ctx 32768. Coherent at `--temp 0`. This corroborates
ds4#892's M5 Max figures closely (474 t/s prefill, 33.0 t/s decode).

**Two things this project recorded were wrong and are now retired:** that the
antirez GGUF was "unusable, no engine loads it" (it was tested on the wrong
engine build), and that ds4#890's ">4096 tokens fails" applies here (a ~30 KB
prompt prefills at 460 t/s, having genuinely crossed onto the compact indexed
path).

**The raised Metal ceiling is required, not optional.** `b0c31af` budgets from
Metal's `recommendedMaxWorkingSetSize`, and its 128 GiB-host branch tests
`base_gib >= 120.0` — which a 128 GiB Mac never reaches, because its working set
is 107.52–112.00 GiB. So the budget comes from the sysctl override: **75.5 GiB
stock against an 89.87 GiB model (refusal), 110 GiB raised (runs).**

**None of this makes GLM-5.3 usable as a coding agent, and the recommendation
does not move.** Everything above is one-shot CLI generation. ds4#569 and
ds4#816 are untouched by this branch, and they are what break the agent loop.
**Do not promote GLM on this evidence** — it clears "does it run", not the bar
this document is about.

**And the memory arithmetic is now fixed.**
[ds4#893](https://github.com/antirez/ds4/pull/893) keeps a fixed **110 GiB**
GLM-5.3 budget for 128 GiB-class hosts and relaxes it only for 256/512 GiB
machines. Our raised Metal ceiling is **112.00 GiB** — *above* ds4's own budget.
**Superseded:** ds4#893 is closed and `b0c31af` now reads the sysctl and lets it
*override* the heuristic. At our 112.00 GiB the override yields exactly 110 GiB,
so the old "buys nothing" conclusion held by coincidence rather than for the
reason given. **A resident q4 (177 GiB) remains unreachable on this machine.**

## Too big for this machine — reopened 2026-08-28

**This tier is now a queue, not a verdict — but speed is the new wall.**
GLM-5.2 was run on 2026-08-29 (#35): **196.6 GiB of file streamed into 30.8 GiB
resident, −84%**, coherent, and it **passed a real agent task**. It also took
**2,585 s on the easiest task in the suite against ds4's 184.8 s — 14x** — and
`--ssd-streaming-cache-experts 80GB` fails outright, so the spare headroom
cannot be spent on speed. **The Metal ceiling is not a hard wall; decode rate
is.** A model belongs here only if it streams *and* decodes within a few times
of the resident primary. ds4's expert streaming was measured
(#34): **91.0 → 36.7 GiB resident, −60%, for +76% suite wall time and no
correctness cost** (16/16 across 31 trials). It does not make a fitting model
faster — ds4 fits here with 21 GiB to spare, so streaming only costs. It makes a
**non-fitting model possible.**

All three models below are MoE, so their weights read at the block size this
NVMe is good at — random 1 MiB at 6.32 GiB/s against 4 KiB at 0.10 GiB/s
(`benchmarks/disk/RESULTS.md`). A 60% resident reduction moves them from
impossible to slow. They should be re-examined against `--ssd-streaming` rather
than left ruled out on file size.

A verdict on 128 GiB, not on the models. All are ruled out on published quant
sizes, not on a failed attempt; see #35 for the standing queue.

| model | smallest published quant | verdict |
|---|---|---|
| `Kimi K3` | nothing under 108 GiB | no headroom for KV **resident** — re-examine with streaming |
| `MiniMax M3` | nothing under 108 GiB | same |
| `GLM-5.2` | 196.6 GiB IQ2_XXS | **RUN 2026-08-29: streams in 30.8 GiB and passes an agent task — but at ~4 tok/s, 14x slower than ds4. Possible, not practical.** |

Revisit either if something under ~100 GiB appears. GLM-5.3-Flash moved out of
this section by exactly that route, so the tier is worth watching.

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

## Measured on a second codebase, in a second language (2026-08-29)

45 trials on `~/git/monitor` — 11,265 Swift lines, 215 tests (#44). A new
series; not pooled with the Python figures elsewhere in this file.

| pair | pass | suite | median out_tok | s per 1k tok |
|---|---|---|---|---|
| **`ds4` + Claude Code** | **9/9** | **522 s** | **3,835** | 47.6 |
| `ornith-1.5` + Codex | 8/9 | 844 s | 20,788 | **14.7** |
| `qwen38fnq3` + Codex | 9/9 | 1,086 s | 5,932 | 61.5 |
| `ds4` + Codex | 9/9 | 1,115 s | 9,082 | 39.6 |
| `qwen3.6-coding` + Claude Code | 9/9 | 1,393 s | 5,232 | 84.3 |

**This strengthens the primary pick and changes one thing about it.** On the
Python repo, `ds4` with Claude Code and with Codex were indistinguishable — 982 s
against 975 s. **On Swift they separate by 2.14x**, and `ds4 + Claude Code` wins
the whole field by 1.6x over second place.

It is the only pair that wins on *both* terms — fewest tokens **and** a
respectable rate. Every other pair trades one against the other: `ornith-1.5` is
3.2x faster per token and writes 5.4x more; `qwen3.6-coding` writes little and is
slowest per token.

**So on the current evidence, prefer Claude Code with `ds4` rather than picking
on habit.** That advice was "pick either" while only Python was measured.

### How gracefully a pair handles an unfamiliar language

Every pair writes more Swift than Python. **How much more varies 2.3x:**

| pair | Python | Swift | inflation |
|---|---|---|---|
| **`ds4` + Claude Code** | 3,234 | 3,835 | **1.19x** |
| `ds4` + Codex | 4,662 | 9,082 | 1.95x |
| `qwen3.6-coding` + Claude Code | 2,268 | 5,232 | 2.31x |
| `ornith-1.5` + Codex | 7,618 | 20,788 | **2.73x** |

Token volume is the dominant term in agent wall time, so this is a practical
number: it says how much a pair costs you on code that is not its comfort zone.
**Caveat:** the Swift tasks are not difficulty-matched to the Python ones, so
the ordering is sound and the absolute ratios are not.

**Correctness did not discriminate — 44 of 45.** A 6x larger codebase in a less
familiar language did not make these tasks harder to pass. It made the pairs
distinguishable on *how* they get there.

**The gap is not fixed — it widens with difficulty (#45, 2026-08-29).** Two
harder Swift tasks, run against the two extremes above:

| pair | easier set | harder set | scaling |
|---|---|---|---|
| **`ds4` + Claude Code** | 3,835 | 5,152 | **1.34x** |
| `ornith-1.5` + Codex | 20,788 | 42,545 | **2.05x** |

The gap between the two pairs goes **5.42x -> 8.26x on tokens** and 1.77x ->
2.93x on wall time. Harder tasks cost the terse pair 34% more output and the
verbose one 105% more.

**Read the inflation table above as a floor, not an estimate.** It was measured
on the easier tasks, and the spread on hard work is larger. This is the second
reason to prefer Claude Code with `ds4`: it is not merely ahead, it pulls further
ahead exactly where the work gets harder — which is the scenario this whole
document exists for.

Throughput is not the mechanism. `ornith-1.5` + Codex decoded at **15.3 s/1k on
the easier set and 15.2 on the harder one**; time scaled 2.03x and tokens 2.05x.
The model does not get slower on hard tasks, it gets wordier.

**Screening run — 2 trials per cell**, under the 10-trial bar in AGENTS.md. The
token ratios are large and consistent; no pass-rate claim rests on it.

## What the 2026-08-29 overnight run changed

Seven evaluations, 190 trials. Full detail in
[`RESULTS.md`](benchmarks/agent/RESULTS.md); the parts that change a decision:

**Nothing in the primary/secondary picks moved.** `ds4` remains the recommendation
and got stronger — 55/55 with Claude Code and 45/45 with Codex pre-upgrade,
31/31 with Codex after. No candidate displaced it.

**Two techniques were tested and rejected, which is worth as much as an
adoption:**

| technique | verdict |
|---|---|
| 4-bit `-M64` quant with the n-gram table split for SSD (#33) | **+28% slower than 3-bit, saves nothing.** mmap already makes every weight page evictable |
| GLM-5.2 at 196.6 GiB via expert streaming (#35) | **Runs in 30.8 GiB and passes — at 14x ds4.** Possible, not practical |

**One technique is real but narrow:** ds4 expert streaming is **−60% memory for
+76% wall time**, lossless across 31 trials. It does not make a fitting model
faster; it makes a non-fitting model possible. Use it only when memory is the
binding constraint.

**Two engine/speed claims in this file turned out to be sampler artifacts** (#28,
#36). The engines are equivalent on identical weights; a "+66% difference" was
four sampler defaults nobody chose. And `top_p 0.90` without a repetition penalty
scores **7/12** against **17/18** at 0.95 — sampler settings move **pass rate**,
not just the clock. Cross-engine rows here are provisional until both sides are
sampler-matched, and **`ds4-server` still reports no sampler at all** (#37).

**The benchmark itself is at its ceiling for the wrong reason.** The harder tasks
went 18/18 (#4), and the target repository turns out to be the limit: 1,833
source lines, median function 13 lines, and exactly one function carrying the
surface that produced the only defect found. The next move is a second
repository, not harder tasks.

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
5. **Quality is measured and good; discrimination now depends on the second
   repository.** Updated 2026-08-29: #4 traced the ceiling to the *repository*,
   not the task set — gmail-archive is 1,833 source lines with exactly one
   function carrying defect surface. `~/git/monitor` (11,265 Swift lines, 215
   tests) is wired as a second target (#42) and the first runs against it are
   #44. Updated 2026-08-28. Three harder tasks (a withheld
   docstring, a two-file change, a two-symbol convention) ran 18 trials against
   `ds4` under both clients: **18/18 passed**, ruff clean, **0/18 recalled**,
   18 distinct solutions. `BlobStore.put` came back with `fsync` + atomic
   rename + directory fsync in every trial — a durability property no test
   checks. The one defect found is real and reproducible: 5 of 6 trials on the
   multi-file task annotate a callback `re.Match` instead of `re.Match[bytes]`,
   adding 2 `mypy --strict` errors while passing all 71 tests. **The ceiling is
   not an artifact of easy tasks.** Issue #4 stays open, but its premise has
   changed: harder tasks bought +39% wall time and no additional failures.
6. **The Ollama rows predate the installed engine.** Ollama went 0.32.15 →
   **0.33.1** on 2026-08-26 to get MLX support for Qwen3.8-Flash-Next, which
   then did not fit. Every Ollama row in `results.jsonl` was recorded under
   0.32.x. The per-row env capture keeps them readable, but `qwen36coding` — the
   secondary — has not been re-run under the engine now installed.

Item 5 is answered for `ds4`: the code it writes is good, not merely passing.
What remains unanswered is whether that distinguishes it from the other
backends, which is a different and harder question. The rest is logistics.

## `--dspark` is now a small win, not a loss (2026-08-31)

**This reverses published guidance.** The "settings to avoid" table said
`--dspark` was *lossless but 23–44% slower at every confidence setting tried*.
That was measured **2026-08-08**, the build SHA was never recorded, and it no
longer reproduces.

Re-measured this morning against two heads, `--temp 0`, three prompts per
configuration, 512 tokens each:

| model | baseline | `--dspark` d1 | gain |
|---|---:|---:|---:|
| `IQ2XXS-w2Q2K-AProjQ8` (q2) | 43.39 t/s | 46.64 | **+7.5%** |
| `Layers37-42Q4K…imatrix-fixed` (our primary) | 45.71 t/s | 47.44 | **+3.8%** |

`main` @ `8db89fe`; PR #915 @ `88bd78a` gives +6.6% and +3.0% — the same within
drift.

**Draft depth is irrelevant.** d1 through d4 sit within ~1% on both models, so
the gain comes from first-token acceptance, not from speculating deeper. That
matches ds4#892's finding on GLM, where every width above 2 lost.

### The control that stopped a false finding

Run back to back, PR #915's *baseline* came in ~8% below `main`'s, which reads
as a regression. **A-B-A says it is not.** Re-running `main` on the now-warm
machine:

| model | A `main` 07:15 | B PR#915 07:22 | A `main` 07:30 |
|---|---:|---:|---:|
| q2q4 baseline | 45.71 | 42.09 | **43.81** |
| q2 baseline | 43.39 | 40.96 | **41.65** |

**~4% of absolute throughput is lost to fifteen minutes of continuous load**,
and PR #915 sits between the two `main` runs. The *ratio* is stable across all
three sweeps while the absolute sags — the same conclusion #52 reached for
`ds4-bench`: **quote the ratio, never the absolute.**

Reported upstream at
[ds4#913](https://github.com/antirez/ds4/issues/913#issuecomment-5477787083).

**Caveat:** three prompts per configuration, one machine. Enough to retire a
claim that pointed the other way; not enough to promote `--dspark` into the
recommended run commands.
