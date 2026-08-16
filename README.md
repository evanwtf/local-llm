# local-llm

Running local models as coding agents, and measuring them. Engine-agnostic by
intent: `ds4`/DeepSeek V4 Flash and Ollama/Qwen3.8 are two entries here, not the
subject.

Everything is measured on one machine — MacBook Pro M5 Max, 128 GiB,
macOS 26.5 — so numbers across engines share a hardware baseline.

| | |
|---|---|
| [`ollama_claude_shim.py`](ollama_claude_shim.py), [`claude-ollama`](claude-ollama) | drive Claude Code with an Ollama model |
| [`benchmarks/ollama/`](benchmarks/ollama/RESULTS.md) | Qwen3.8-27B: speed, agentic accuracy, speculative decoding |
| [`benchmarks/ds4/0731/`](benchmarks/ds4/0731/REPORT.md) | DeepSeek V4 Flash quant comparison, thermals, long context |
| [`benchmarks/ds4/coding/`](benchmarks/ds4/coding/RESULTS.md) | HumanEval, mixed q2/q4 vs MXFP4 |
| [`CONVENTIONS.md`](CONVENTIONS.md) | standing rules — read before deleting weights or committing logs |

The benchmark history moved here from `evanwtf/ds4`, with its commits intact.
The ds4 engine and its weights still live in that checkout; scripts find them
via `DS4_ROOT` and write results here.

Headline comparison, same machine, ~12k context:

| | prefill | generation | resident |
|---|---|---|---|
| Qwen3.8-27B via Ollama 0.32.14-rc0 | 789.2 t/s | 57.1 t/s | 18 GB |
| DeepSeek V4 Flash mixed q2/q4 via ds4 | 488.1 t/s | 34.4 t/s | 90.9 GiB |

Prefill is measured differently by the two harnesses — see
[`benchmarks/ollama/RESULTS.md`](benchmarks/ollama/RESULTS.md).

---

# The Ollama shim

> **Not needed on Ollama 0.32.14-rc0 or later.** That release contains the
> upstream fix, verified here: `ollama launch claude --model qwen3.8:27b-mlx`
> works with no proxy and no environment variables. The shim is kept for older
> Ollama, and in case a stable release lands behind the fix.

Ollama exposes an Anthropic-compatible `/v1/messages` endpoint and ships a
`launch claude` integration. On Ollama 0.32.13 and earlier that integration does
not work: Ollama rejects the request shape Claude Code sends.

Tested on macOS 26.5 with Ollama 0.32.13 and `qwen3.8:27b-mlx`.

## Quick start

```sh
ollama pull qwen3.8:27b-mlx
./claude-ollama
```

That starts the shim if it is not running, then runs Claude Code against the
local model. `./claude-ollama stop` stops the shim. `MODEL=... ./claude-ollama`
selects a different model.

## The problem

`ollama launch claude` fails on the first real request:

```
API Error: 500 system message must be at the beginning
```

Ollama logs it as a prompt-build error:

```
level=ERROR source=routes.go:2684 msg="chat prompt error"
    error="system message must be at the beginning"
```

The cause is a request-shape mismatch. Claude Code appends a message with
`role: "system"` to the **end** of the `messages` array, carrying the
agent-type listing for the Agent tool:

```json
{
  "system": [ { "type": "text", "text": "..." } ],
  "messages": [
    { "role": "user",   "content": [ { "type": "text", "text": "..." } ] },
    { "role": "system", "content": [ { "type": "text", "text": "Available agent types: ..." } ] }
  ]
}
```

Ollama accepts system content only in the top-level `system` field, or at index
0 of `messages`. A system message anywhere else is refused before the model
runs.

Nothing else about Claude Code's payload is at fault. These all work against
Ollama unmodified: system as a string, system as blocks, several system blocks,
`cache_control`, tools, `tool_choice`, full `tool_use`/`tool_result` round
trips, echoed `thinking` blocks with signatures, streaming, `?beta=true`, and
prompts of 11.5k tokens over 12 turns.

Small requests succeed because Claude Code omits the agent listing on them.
That is why session-title and statusline calls return 200 while every real turn
returns 500.

## The fix

`ollama_claude_shim.py` sits between Claude Code and Ollama. It moves any
`role: "system"` message into the top-level `system` field and forwards
everything else untouched.

Hoisted blocks are appended **after** the existing system blocks, so the agent
listing still follows the main prompt. `cache_control` is stripped from moved
blocks, because a cache breakpoint that changes position is meaningless.

```sh
uv run ollama_claude_shim.py            # 127.0.0.1:11500 -> 127.0.0.1:11434
uv run ollama_claude_shim.py --port 9000 --upstream http://127.0.0.1:11434
```

Then point Claude Code at the shim:

```sh
ANTHROPIC_BASE_URL=http://127.0.0.1:11500 \
ANTHROPIC_AUTH_TOKEN=ollama \
ANTHROPIC_DEFAULT_SONNET_MODEL=qwen3.8:27b-mlx \
ANTHROPIC_DEFAULT_OPUS_MODEL=qwen3.8:27b-mlx \
ANTHROPIC_DEFAULT_HAIKU_MODEL=qwen3.8:27b-mlx \
CLAUDE_CODE_MAX_CONTEXT_TOKENS=262144 \
claude
```

`claude-ollama` does all of that for you.

`ollama launch claude` cannot be used even with the shim running: it sets
`ANTHROPIC_BASE_URL` to Ollama's own port and overrides anything you export.

## Set the context window

`CLAUDE_CODE_MAX_CONTEXT_TOKENS` must match the model's real context window.
Claude Code assumes 200k for a model it does not recognise, so if the model's
window is smaller, auto-compact fires *after* the server has already truncated.
`claude-ollama` sets it from `CTX`, which defaults to 262144 for Qwen3.8.

## Debugging

`--dump-failures DIR` writes the body of any request Ollama still rejects, and
logs the role and block types of every message.

**These dumps contain the full prompt** — your `CLAUDE.md` and the contents of
every file the agent read. `.gitignore` blocks `fail-*.json`. Delete them when
you are done.

## Tests

```sh
uv sync
uv run pytest
```

The tests cover `hoist_system` directly: requests Ollama already accepts must
pass through byte-identical, and the exact shape Claude Code sends must be
rewritten correctly.

## Upstream: already fixed, not yet released

Ollama fixed this in `87abaa01`, "renderers/qwen: tolerate non-leading system
messages" (#17757). The Qwen renderer no longer rejects the transcript; it
renders non-leading system turns through the raw ChatML path.

The timing is why you may still hit it. The fix was committed at
**2026-08-14 21:12 UTC**; **v0.32.13** was cut at **19:16 UTC** the same day.
It missed the release by under two hours, so the newest released Ollama still
fails.

**This shim is only needed on Ollama 0.32.13 and earlier.** Verified against a
build of `main`: Claude Code drives `qwen3.8:27b-mlx` with no proxy at all.

To drop the shim, either wait for the next release, or build from source:

```sh
git clone https://github.com/ollama/ollama && cd ollama
go build -o ollama-dev .
```

A bare `go build` does not bundle the MLX libraries. On macOS the binary
searches `../lib/ollama` and its own directory, so place it next to the ones
the desktop app installed:

```sh
cp ollama-dev /Applications/Ollama.app/Contents/Resources/
OLLAMA_HOST=127.0.0.1:11439 \
    /Applications/Ollama.app/Contents/Resources/ollama-dev serve
```

Use a spare port so the desktop app keeps working on 11434.

## Why bother — measured

On a MacBook Pro M5 Max, 128 GiB, at ~12k context:

| | prefill | generation | resident |
|---|---|---|---|
| `qwen3.8:27b-mlx` via Ollama | 730.3 t/s | 46.3 t/s | 18 GB |
| DeepSeek V4 Flash mixed q2/q4 via `ds4` | 488.1 t/s | 34.4 t/s | 90.9 GiB |

Caveat: the two are not measured identically. `ds4-bench` reports a
2048-token prefill at a given context; the Qwen figure is a single 11,451-token
prefill, and longer prefills batch better. The generation numbers are directly
comparable; the prefill numbers are indicative only.
