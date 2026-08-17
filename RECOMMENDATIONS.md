# What to run on this Mac

**Hardware:** MacBook Pro, Apple M5 Max, 128 GiB unified memory, macOS 26.5.
**Purpose:** a working fallback for agentic coding if hosted models become
unavailable — price, policy, or otherwise.
**Evidence:** 122 agent trials, 8 backends. See [`RESULTS.md`](benchmarks/agent/RESULTS.md).

This is a fallback plan, not a daily driver plan. None of these is faster than
hosted Claude. The reason to keep them working is that they keep working when
nothing else does.

---

## The short answer

| role | model | why |
|---|---|---|
| **primary** | `ds4` synced (`fdcf3aa`) | fastest backend that never fails or stalls |
| **secondary** | `qwen3.6:27b-coding-mxfp8` | fits alongside other work; shares no code with ds4 |
| **watch** | `Qwen3.8-27B-MTPLX-Optimized-Speed` | best speed per GB, but placement unresolved |

Two models is the target, not one. The June 2026 suspension was
**model-specific, not vendor-wide** — the failure mode to design against is a
single thing going away, so the second entry exists to have a different
everything: different weights, different engine, different maintainer.

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

The only backend that is both quick and dependable. Predictability is the
reason it wins: a 2.6× spread means you can plan around it. `ornith:35b` has a
better median (82.3 s) and is disqualified below.

**Start it:**

```sh
benchmarks/ds4/0731/agent/ds4-up restart
```

`ds4-server` resolves its Metal shaders relative to the working directory, so
the launcher must `cd "$DS4_ROOT"` first. It does. Do not "simplify" that away —
it has broken once already.

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
68% fewer tokens under MTPLX than under Ollama. Not promoted yet because its
trials got faster across rounds (1,780 s → 1,253 s → 1,021 s) for reasons that
remain **unexplained** — the two obvious mechanisms were both disproved by the
server's own counters. If the trend is real its warm median is ~193 s, which
would place it second.

One cold-restart run settles it. Until then its placement is provisional.

```sh
mtplx quickstart --model Youssofal/Qwen3.8-27B-MTPLX-Optimized-Speed \
    --profile turbo --port 8010 --yes
```

---

## What not to bother with

**`ornith:35b`** — fastest median in the field (82.3 s) and the only backend
that has ever failed: 13/15, twice on the *easiest* task, with a **30.4×**
spread and one run of 20.4 minutes. Fast-on-average and occasionally broken is
the worst possible profile for something you depend on.

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

---

## Not done yet

These are the gaps between "benchmarked" and "actually a fallback":

1. **Weights are not backed up.** Everything lives on the working disk. One
   drive failure is currently zero fallback, and re-downloading assumes the
   distribution channel still exists — which is the scenario being hedged
   against. This is the highest-value open item.
2. **The toolchain is not archived.** Ollama, MTPLX (brew/pip), the MLX wheels
   and the ds4 source are all reacquired from the network today. Weights you
   cannot serve are not a fallback.
3. **No cold-start runbook.** The Metal-shader trap above was rediscovered
   under pressure once. Write the procedure down while it is calm.
4. **Quality is unmeasured at real difficulty.** Seven of eight backends scored
   100%, so these tasks cannot tell you which model is *better* — only that
   each is competent at restoring one deleted function. Issue #4.

Item 4 is the one that decides whether this plan is any good. The rest is
logistics.
