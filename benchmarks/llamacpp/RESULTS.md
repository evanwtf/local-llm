# Qwen3.8-Flash-Next as a coding agent backend

Released 2026-08-26 as the preview of the Qwen4 architecture. Measured here on
2026-08-26/27, the day it shipped, through the same agent benchmark as every
other backend: 31 trials, 5 tasks, 2 clients, the repository's own test suite
as the only oracle.

**Verdict: it runs, it passes, and it is the slowest backend measured.** Use it
as evidence about the architecture, not as a fallback candidate.

| | |
|---|---|
| model | `Qwen3.8-Flash-Next`, 125B total / 6B active, + 51B n-gram + 4B MTP |
| quant | Unsloth `UD-Q2_K_XL`, 78.9 GB |
| engine | llama.cpp PR #27742, `035e22731` (mainline does not know `qwen4exp`) |
| resident | 77.9 GiB, all layers on Metal |
| context | 131072, one slot |

---

## Results

Wall seconds are the median of passing runs.

| task | Claude Code | Codex |
|---|---|---|
| `mbox-strip-envelope` | 4/4, 551.7s | 3/3, **83.7s** |
| `parser-mbox-quoting` | 3/3, 855.7s | 3/3, **163.0s** |
| `storage-blob-put` | 3/3, 1261.5s | 3/3, **188.3s** |
| `parser-date` | **1/3**, 1590.4s | 3/3, **501.8s** |
| `mbox-scan` | **2/3**, 976.4s | 3/3, **313.8s** |
| **suite total** | **13/16**, **5,236s** | **15/15**, **1,251s** |

The suite total is the sum of the per-task medians, the unit the other
backends are quoted in in `RECOMMENDATIONS.md`. Per-trial medians are 920.2s
for Claude Code and 188.3s for Codex.

All three Claude Code failures are timeouts at the 2400s limit: `parser-date`
twice and `mbox-scan` once. No run cheated -- `touched_tests` is false
everywhere -- and no run left its sandbox.

Against the rest of the field, pooled across clients:

| backend | pass | median |
|---|---|---|
| hosted Opus 5 (reference) | 5/5 | 35.6s |
| `ornith:35b` | 13/15 | 82.3s |
| `ds4` | 40/44 | 174.2s |
| `qwen36coding` | 44/45 | 231.3s |
| `gemma4:31b-mxfp8` | 16/16 | 355.4s |
| **`qwen38fnq2`** | **28/31** | **472.0s** |

It is last, and it is the only backend in the project with any timeouts.

---

## Codex is 4.2x faster than Claude Code here, on every task

4.2x over the suite, 4.9x on per-trial medians. Not one task is an exception,
and the effect is far larger than the client gaps measured on other backends
(12% on ds4, 63% on Ollama). Both clients do
comparable work -- 5087 output tokens median for Claude Code against 4161 for
Codex -- so the gap is not the model thinking harder for one of them.

**The cost is re-prefill, not generation.** Claude Code spends a median of
**84.1 seconds per turn** (min 59.8, max 106.0) and the server log says where it
goes. One turn processed its prompt from scratch:

```
prompt processing, n_tokens = 48972, progress = 1.00, t = 101.30 s / 483.43 tokens per second
```

while the turns on either side of it processed 2863 and 2478 tokens in about
five seconds each. The prompt cache is working, and then something invalidates
it and the whole conversation is re-prefilled at ~500 t/s. At 49k tokens that
is 100 seconds of wall time buying no new output.

That is the number to attack, and it makes the timeouts legible too: a task
needing 18 turns at 84s each is 25 minutes before the model has done anything
hard. `parser-date` is the largest task in the suite -- 49 broken tests -- and
it is the one that timed out twice.

**Not yet tested:** `--cache-reuse N`, which lets llama.cpp reuse cache chunks
after a prefix mismatch instead of discarding everything. If the invalidation is
a small edit near the head of a 49k-token prompt, that flag is the fix. Also
untested is whether the shim causes it: it hoists Claude Code's trailing
`system` message into the top-level `system` field, which moves content toward
the front of the prompt, and Codex -- which does not go through the shim -- does
not show the effect.

---

## What had to be fixed to get a number at all

Three obstacles, none of them the model. Each is written up where it lives, and
recorded here so the next person does not rediscover them.

**Ollama cannot serve this model on 128 GiB.** Its only fitting tag is 112 GB
nvfp4, which peaks at 126.51 GiB against a 107.0 GiB Metal budget and dies on
the first agent-sized prompt. The peak is fixed -- unchanged at 262144, 65536
and 32768 context, at `OLLAMA_NUM_PARALLEL=1`, and with a q8_0 KV cache -- so no
serving knob reaches it. See RECOMMENDATIONS.md.

**Codex 0.148.0 removed `wire_api = "chat"`.** The profile uses `"responses"`;
llama-server serves `/v1/responses` as well, so this was config, not a
translation layer.

**Claude Code needed this repo's shim, which the README said was obsolete.**
Claude Code appends a `role="system"` message to the END of the messages array
and the Qwen template raises `System message must be at the beginning`. Ollama
fixed that upstream at 0.32.14-rc0; llama.cpp's `/v1/messages` does not. The
shim is generic in its upstream, so pointing it at `:8020` fixed it unchanged.
It is named for the bug it was written for, not for the only server that has it.

---

## The finding worth taking elsewhere: KV cache is nearly free here

The first configuration served 65536 context and llama.cpp's default four KV
slots. Claude Code reported autocompact thrashing -- "the context refilled to
the limit within 3 turns of the previous compact, 3 times in a row" -- and spent
842s on a task Codex finished in 109.5s. A window the agent keeps overrunning
measures the window, not the model.

Dropping to `-np 1` and spending the freed memory on context:

| | before | after |
|---|---|---|
| slots | 4 | 1 |
| context | 65536 | 131072 |
| resident | 76.2 GiB | 77.9 GiB |

**Double the window for 1.7 GiB.** The GDN + Qwen Sparse Attention hybrid keeps
almost no per-token KV state, so the usual memory arithmetic does not apply. On
a memory-bound Mac the trade is fewer slots and a much longer window, and it is
worth testing against the other backends here.

The three trials taken at 65536 are in `results.jsonl` marked `excluded` with
that reason. They measure the window, not the model.

---

## The claim not tested

Unsloth's announcement says this model "outperforms Claude-Opus-4.6 (Max)".
Nothing here speaks to that. These tasks restore one deleted function against a
test suite, and nearly every backend in this project passes them -- that is
issue #4, and it is why the suite cannot rank quality. What this run measured is
whether the model can drive an agent loop to a green test suite on this machine,
and how long it takes.

The 2-bit quant is also a real quality risk that this suite is too easy to
expose. If a harder benchmark ever shows this backend failing where others pass,
suspect the quant before the model.

---

## Reproducing

```sh
# Serves llama-server on :8020 and the Claude Code shim on :11500.
benchmarks/llamacpp/llamacpp-up start

CODEX_API_KEY=llamacpp-local uv run benchmarks/agent/run.py \
    --backend qwen38fnq2 --client claude --client codex \
    --trials 3 --timeout 2400 --client-log ~/bench-logs

benchmarks/llamacpp/llamacpp-up stop      # frees ~78 GiB
```

`--client-log` is opt-in and belongs outside the repo: transcripts carry file
contents the agent read, and this repo does not commit prompts. It is also how
the autocompact finding above was made -- a results row records that the agent
failed, never why.
