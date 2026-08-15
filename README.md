# local-llm

Run Claude Code against a local model served by Ollama.

Ollama exposes an Anthropic-compatible `/v1/messages` endpoint and ships a
`launch claude` integration. That integration does not currently work: Ollama
rejects the request shape Claude Code sends. This repo is a small proxy that
fixes the shape, plus a launcher.

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

## Upstream

This is an Ollama bug, not a misconfiguration. It affects Claude Code against
*every* Ollama model, not just Qwen3.8. The fix upstream is to hoist stray
system messages, or accept them at any index, in the Anthropic middleware.

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
