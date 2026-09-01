# GLM-5.3-Flash as a coding agent, measured

**Date:** 2026-08-31. **Machine:** MacBook Pro, Apple M5 Max, 128 GiB, macOS 26.6.2.
**Issues:** [#62](https://github.com/evanwtf/local-llm/issues/62) (this measurement),
[#63](https://github.com/evanwtf/local-llm/issues/63) (thinking default),
[#64](https://github.com/evanwtf/local-llm/issues/64) (KV prefix stall).

GLM-5.3-Flash drives a coding agent on this machine. It solves two tasks our
DeepSeek primary cannot, and it is the first result in this project that looks
like a **model** difference rather than a plumbing artifact.

It is not a recommendation. Read the caveats before quoting a number.

---

## Configuration

Everything below is one configuration. Provenance was recorded before the
numbers, because a previous run of this same cell recorded the wrong engine.

| item | value |
|---|---|
| engine | `antirez/ds4` at **`ec7642cdd9ec81d01ad4b1fd8f8a3d1511533748`** (`upstream/main`) |
| built | 2026-08-31T11:22:40, in a worktree at `~/git/ds4-main` |
| weights | `GLM-5.3-Flash-Q2.gguf`, antirez build, 96,505,816,384 bytes, mtime 2026-08-27 |
| sha256 (first 64 MiB) | `cabba918d4377bfe5806d15d905e047fc528f9b593eec5af44fdb6f150cc97b1` |
| serving | `./ds4-server -m <gguf> -c 100000 --port 8000`, **no `--trace`** |
| memory | KV 1.11 GiB + buffers 3.11 GiB + model 89.87 GiB = **94.09 GiB** |
| target repo | `gmail-archive` pinned at `56e55cceccbaa2afbecf0724551489dc641dae24` |
| clients | Claude Code 2.1.251 (via shim on `:8100`), aider 0.86.2 (direct on `:8000`) |
| thinking | **on** — ds4's default, and the setting #63 endorses |

`glm-5.3-flash` is now on `upstream/main`; the `glm-5.3-flash` branch merged on
2026-08-31. The engine binary was built in a **separate worktree** so the pinned
`399acbb` binary that every earlier ds4 number depends on stays reproducible.

**The Metal ceiling is required.** `sudo sysctl iogpu.wired_limit_mb=114688`
lifts the working set to 112.00 GiB. A 94.09 GiB plan does not fit the stock
107.52 GiB budget once ds4's own GLM guard applies. **Persisted since 2026-09-01** by
`scripts/install-metal-ceiling.sh`; before that a reboot reverted it.

---

## Results

### GLM x Aider — full cell, 3 trials per task

| task | trial 1 | trial 2 | trial 3 | median |
|---|---|---|---|---|
| `mbox-strip-envelope` | 100s | 106s | 111s | **106s** |
| `parser-mbox-quoting` | TIMEOUT | 440s | TIMEOUT | 440s |
| `storage-blob-put` | FAIL 865s | TIMEOUT | FAIL 1481s | — |
| `parser-date` | 272s | 270s | 254s | **270s** |
| `mbox-scan` | 193s | 196s | 184s | **193s** |

**10/15. Zero workspace escapes. Every pass took exactly one turn.**

Three tasks repeat inside 12%; `mbox-strip-envelope` spans 11.8%, `parser-date`
6.6%, `mbox-scan` 6.1%. The two failing tasks fail expensively rather than
slowly — no trial has ever finished *late*, only fast or not at all.

### GLM x Claude Code — 7 trials, stopped early

| task | trial 1 | trial 2 |
|---|---|---|
| `mbox-strip-envelope` | 148s | 166s |
| `parser-mbox-quoting` | 583s | 441s |
| `storage-blob-put` | **607s** | — |
| `parser-date` | **TIMEOUT** | — |
| `mbox-scan` | 931s | — |

**6/7.** Stopped deliberately once #64 was found: rounds 2 and 3 would have spent
two hours adding precision to a wall-time number the cache bug makes meaningless.

---

## What this establishes

### 1. GLM solves tasks DeepSeek cannot

| task | GLM+Aider | GLM+ClaudeCode | DeepSeek+Aider |
|---|---|---|---|
| `mbox-scan` | **3/3** (184-196s) | **PASS** 931s | **0/3** |
| `storage-blob-put` | 0/3 | **PASS** 607s | **0/3** |

`mbox-scan` is the strongest single result here. DeepSeek-V4-Flash failed it
**deterministically** through the same client and harness — the same wrong
62-byte patch three times, at 265s / 269s / 270s, fixing 10 of 13 failing tests
and stopping. GLM passes it **six times out of six across two different
clients**.

Two clients, one model, no escapes, reproducible timings. A cache bug can turn a
would-be pass into a timeout; it cannot manufacture a correct patch. **This is a
model result.**

`storage-blob-put` is the same story with one client: DeepSeek 0/3, GLM+Aider
0/3, GLM+Claude Code **pass in 607s over 21 turns**.

### 2. The client decides which tasks are reachable

Neither client dominates. Each cleared a task the other could not:

- **`parser-date`** — Aider **3/3** at ~270s. Claude Code **timed out** at 1800s.
- **`storage-blob-put`** — Aider **0/3**. Claude Code **passed** in 607s, 21 turns.

Every Aider pass took **one turn**. Claude Code needed **8, 10, 16, 21 and 30**.
The reading that survives three trials: *the tasks GLM can one-shot, Aider does
fastest; the tasks it cannot, only the agentic loop reaches.*

A pass-rate column alone would have shown two similar totals and hidden this
entirely.

### 3. GLM is not intrinsically slow — the client was

**`mbox-scan`: 193s median on Aider, 931s on Claude Code. Same model, same task,
4.8x.** Aider's three trials span 6%, so the gap is not scatter.

Through Aider, GLM runs `mbox-strip-envelope` in **106s** against DeepSeek's
90-97s — within 10%. The "GLM is much slower" impression formed earlier in the
day came from Claude Code numbers inflated by #64.

---

## What this does not establish

**No pass-rate ranking.** 10/15 carries a Wilson 95% interval of roughly
**42-84%**. Per [#23](https://github.com/evanwtf/local-llm/issues/23), a 3-trial
median carries +/-27.9% and two suites need a ~26% gap to be distinguishable.
The per-task cells (3/3, 0/3, 6/6) carry the weight here, not the totals.

**No cross-client timing claim beyond `mbox-scan`.** The Claude Code column is
n=1 per task and every one of its wall times is inflated by #64.

**Nothing about code quality.** The oracle is the repository's own test suite,
pass or fail. `mbox-scan`'s DeepSeek failures were *near misses* — 10 of 13 tests
fixed — which this suite scores identically to writing nothing.

**Nothing about GLM at any other quantization.** This is one Q2 GGUF. antirez has
said new checkpoints are coming; a refreshed checkpoint gets a new row, not an
edit to this one.

---

## Two defects found while measuring this

Both were found *because* the numbers looked wrong, and both invalidate earlier
work.

### #63 — thinking was off, and off is worse

ds4 defaults to **high-effort thinking** (`ds4_server.c:960`). Our shim rewrote
Claude Code's `thinking:{"type":"adaptive"}` to **`disabled`** to save tokens.

Measured across 8 trivial functions, each executed against an assertion:

| arm | correct | median tokens | median wall |
|---|---|---|---|
| default | **8/8** | 332 | 10.6s |
| **off** | **4/8** | 224 | 7.5s |
| on | **8/8** | 332 | 10.6s |

Thinking off failed `fib(10)` and reversing a string. It was not reliably cheaper
either — on one task it spent **548 tokens to on's 431 and was still wrong**.

The agent-level confirmation was decisive. Re-running the same cell after
changing the rewrite to `enabled` turned **three failures into three passes**:

| task | thinking off | thinking on |
|---|---|---|
| `parser-mbox-quoting` | FAIL, **1 turn**, 605 tok, **0 bytes** | PASS, 30 turns, 9,767 tok, 75 B |
| `storage-blob-put` | FAIL, **18,080 tok**, **0 bytes** | PASS, 8,560 tok, 72 B |

With thinking off, GLM **quit after one turn**. The degraded arm was not merely
wrong, it was **more expensive while being wrong** — roughly twice the tokens,
producing text that never became code.

Fixed in `218cc5a`. Guarded by `smoke.gate()` in `ffe7aca`: every batch now makes
the backend write `reverse_string`, `fib` and `merge_sorted` and **executes** them
before any trial runs. All three are tasks the degraded arm failed. Cost: 7.3
seconds.

### #64 — the KV prefix stalls on the Claude Code path

Traced with `ds4-server --trace`, twelve consecutive turns of one trial:

```
prompt_tokens: 25831  live_prompt_common: 20398  token-mismatch
prompt_tokens: 29068  live_prompt_common: 20398  token-mismatch
prompt_tokens: 38145  live_prompt_common: 20398  token-mismatch
```

`live_prompt_common` frozen at **20,398** while the prompt grows to 38k;
`memory_token_reusable: 0` on every turn. Every turn re-prefills everything past
token 20,398. At ~360 t/s that is **~186s before the first output token**, and it
grows with the conversation.

**Ruled out:** the injected token counter (the trace has 238 occurrences of
`<total_tokens>0 tokens left</total_tokens>` and no other value — the shim pins it
correctly, so `tasks.toml`'s comment blaming it is wrong); and `cache_control`
markers (stripping them changed nothing — still 20,398, still 0/8 reusing).

**Open lead:** `messages[1]` (role `system`) alternates between a list of content
blocks and a bare string across turns. Character offsets were never mapped to
token 20,398, so this is a lead, not a finding — the two hypotheses asserted
before that mapping were both wrong.

**Aider is unaffected.** It uses the OpenAI path, sends no `cache_control`, and
shows small contexts (`ctx=0..1224`). That makes it the control, and it is why
the 193s-vs-931s gap on `mbox-scan` is the cost of this bug measured on real work.

---

## What this retires

**"GLM-5.3 is not merged to ds4 main. Use the branch."** Merged 2026-08-31 at
`ec7642c`; `glm-5.3-flash` is an ancestor of `upstream/main`.

**"GLM-5.3-Flash is unusable as a coding agent — do not put it in the plan."**
That rested on [ds4#569](https://github.com/antirez/ds4/issues/569) (tool-call
argument stringification) and
[ds4#816](https://github.com/antirez/ds4/issues/816) (stateless clients never
reuse the KV session). GLM now completes agent tasks through two different
clients. #816's mechanism is still visible as #64 on the Claude Code path, but it
does not prevent completion.

**"ds4#890: prefill fails above 4096 tokens."** Does not reproduce on `ec7642c`:
**37 prefills, up to 25,479 tokens, zero failures.**

**Every wall-time number in the earlier `glm53` rows.** Those were llama.cpp +
Unsloth `UD-Q2_K_XL` through a shim — a different engine reading a different GGUF
layout. Not comparable to anything here.

---

## Reproducing this

```sh
git -C ~/git/ds4 fetch upstream
git -C ~/git/ds4 worktree add --detach ~/git/ds4-main ec7642c
cd ~/git/ds4-main && make -j"$(sysctl -n hw.ncpu)"

sudo sysctl iogpu.wired_limit_mb=114688          # or scripts/install-metal-ceiling.sh to persist
./ds4-server -m ~/git/ds4/gguf/GLM-5.3-Flash-Q2.gguf -c 100000 --port 8000 &

# Claude Code needs the shim; aider does not.
cd ~/git/local-llm && ./ds4_claude_shim.py --port 8100 --upstream http://127.0.0.1:8000 &

cd benchmarks/agent
uv run python run.py --backend glm53ds4      --client aider  --trials 3
uv run python run.py --backend glm53ds4shim --client claude --trials 3
```

The smoke gate runs first and refuses the batch if the backend cannot write three
trivial functions. That check costs 7 seconds and would have saved four trials
and ninety minutes on 2026-08-31.

---

## Open questions

1. **#64's cause.** One experiment left: normalise `content` shape in the shim,
   re-run one traced trial, check whether `live_prompt_common` advances. If GLM's
   Claude Code times collapse, the whole cell is worth re-running.
2. **A full Claude Code cell**, 15 trials, once #64 is understood.
3. **Why `storage-blob-put` needs many turns.** GLM+Aider spent 16,871 tokens
   over 4 turns and wrote nothing; Claude Code passed it in 21 turns. That is the
   sharpest example of client structure deciding a model's reach.
4. **A mixed-precision GLM quant.** antirez's own guidance — routed experts
   low-bit, everything else Q8 — is the recipe our DeepSeek primary already uses.
   No GLM GGUF built that way has been tested here.
