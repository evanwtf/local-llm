# The Ollama shim

`ollama_claude_shim.py` lets Claude Code drive a server whose chat template
rejects the request shape Claude Code sends. It is named for the bug it was
written for, not for the only server that has it.

**Where it is still needed:**

- **llama.cpp's `/v1/messages`** (as of 2026-08-26): it passes the stray
  system message through and the Qwen template raises. Point the shim at
  llama-server with `--upstream`.
  [`llamacpp-up`](../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/llamacpp/llamacpp-up)
  starts it as part of that stack.
- **Ollama 0.32.13 and earlier.** Ollama fixed it upstream in `87abaa01`
  ("renderers/qwen: tolerate non-leading system messages", ollama#17757), first
  released in 0.32.14-rc0. On a later Ollama, `ollama launch claude --model
  qwen3.8:27b-mlx` works with no proxy and no environment variables.

## The problem

The first real request fails:

```
API Error: 500 system message must be at the beginning
```

Ollama logs it as a prompt-build error:

```
level=ERROR source=routes.go:2684 msg="chat prompt error"
    error="system message must be at the beginning"
```

Claude Code appends a message with `role: "system"` to the **end** of the
`messages` array. It carries the agent-type listing for the Agent tool:

```json
{
  "system": [ { "type": "text", "text": "..." } ],
  "messages": [
    { "role": "user",   "content": [ { "type": "text", "text": "..." } ] },
    { "role": "system", "content": [ { "type": "text", "text": "Available agent types: ..." } ] }
  ]
}
```

The server accepts system content only in the top-level `system` field or at
index 0 of `messages`, and refuses anything else before the model runs.

Nothing else in Claude Code's payload is at fault. These all work unmodified:
system as a string or as blocks, several system blocks, `cache_control`, tools,
`tool_choice`, full `tool_use`/`tool_result` round trips, echoed `thinking`
blocks with signatures, streaming, `?beta=true`, and prompts of 11.5k tokens
over 12 turns. Small requests succeed because Claude Code omits the agent
listing on them, which is why session-title and statusline calls return 200
while every real turn returns 500.

## The fix

The shim moves any `role: "system"` message into the top-level `system` field
and forwards everything else untouched. It appends hoisted blocks **after** the
existing system blocks, so the agent listing still follows the main prompt. It
strips `cache_control` from moved blocks, because a cache breakpoint that
changes position is meaningless.

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

`claude-ollama` (repo root) starts the shim if needed and does all of that.
`claude-ollama start` and `claude-ollama stop` manage the shim alone.

Do not use `ollama launch claude` with the shim: it sets `ANTHROPIC_BASE_URL`
to Ollama's own port and overrides anything you export.

## Set the context window

`CLAUDE_CODE_MAX_CONTEXT_TOKENS` must match the model's real context window.
Claude Code assumes 200k for a model it does not recognize, so on a smaller
window auto-compact fires *after* the server has already truncated.
`claude-ollama` sets it from `CTX`, which defaults to 262144 for Qwen3.8.

## Debugging

`--dump-failures DIR` writes the body of any request the server still rejects,
and logs the role and block types of every message.

**The dumps contain the full prompt**: your `CLAUDE.md` and the contents of
every file the agent read. `.gitignore` blocks `fail-*.json`. Delete the dumps
when you are done.

## Tests

```sh
uv run pytest -q test_ollama_claude_shim.py
```

The tests cover `hoist_system` directly: requests the server already accepts
must pass through byte-identical, and the exact shape Claude Code sends must be
rewritten correctly.
