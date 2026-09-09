# What to actually run

**A local coding agent on an Apple Silicon Mac, when you cannot or will not
use a hosted provider.** Every number was measured by `benchmarks/agent/` on
one machine — an **M5 Max, 128 GB** — and re-read from the ledger 2026-09-08.
Nothing here is from a model card. Paste section 1, pick a row in 2, or run
one script in 3; the rest moved to [`docs/`](#where-the-rest-of-it-went).

---

## 1. Paste this

Smallest download, easiest install, leaves your Mac usable while it runs.

```sh
# 1. The agent (the thing you type at)
curl -fsSL https://opencode.ai/install | bash

# 2. The server + model (31 GB)
brew install ollama
ollama serve &
ollama pull qwen3.6:27b-coding-mxfp8

# 3. Tell the agent about the server
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/qwen3.6:27b-coding-mxfp8",
  "provider": {
    "ollama": {
      "name": "ollama (local)",
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://127.0.0.1:11434/v1", "apiKey": "ollama" },
      "models": { "qwen3.6:27b-coding-mxfp8": { "tool_call": true } }
    }
  }
}
JSON

# 4. Use it
cd ~/some/project
opencode run --dir "$PWD" "add a --verbose flag to the CLI and a test for it"
```

A complete working local coding agent. **24/24 on our benchmark**, 31 GB.

**`--dir` is not optional.** `opencode run` talks to a background server with
its own working directory, so it ignores where you launched it. Leave `--dir`
out and it solves your task and writes the files somewhere else. That cost us
two weeks and 130 wasted trials.

---

## 2. Or pick a row

**Not a ranking.** Each row is here for the reason in its first column.
Section 1 installs row 1.

| pick this if | model | server | download | pass rate | median task |
|---|---|---|---|---|---|
| **you are starting out** | Qwen3.6-27B-coding `mxfp8` | Ollama | 31 GB | **24/24** | 167s |
| **you want it fast** | Qwen3.8-Flash-Next `Q4_K imatrix` | ds4, ivanfioravanti's fork | 98 GiB | **196/196** | **95s** |
| **you want a mainline engine** | Qwen3.8-Flash-Next `UD-Q3_K_XL` | llama.cpp | 84 GiB | **75/75** | 106s |
| **you want a second lineage** | DeepSeek-V4-Flash | ds4 (DwarfStar) | 91 GB | **30/30** | 115s |

**Row 2 is 16% faster than row 3 and costs you a fork** — wall ratio 0.84
(95% CI 0.76–0.92), 90/90 both arms, 13 of 15 tasks favoring ds4. It loads
only on ivanfioravanti's trees, and this file has already had one stack
withdrawn by its author. Row 3 is slower and will still be there.

**Do not add ds4's MTP flags.** Until 2026-09-08 they never speculated at all:
ds4 reaches its Qwen MTP path only at temperature 0 and no agent client sends
one ([#151](https://github.com/evanwtf/local-llm/issues/151)).

---

## 3. Or run one script

```sh
scripts/local-agent.sh <stack> [opencode|claude]
scripts/local-agent.sh starter                     # row 1, OpenCode
scripts/local-agent.sh fast claude                 # row 2, Claude Code
scripts/local-agent.sh mainline                    # stock llama.cpp, no fork
scripts/local-agent.sh lineage                     # the ds4 fork our rows come from
scripts/local-agent.sh fast --check                # report and stop, change nothing
```

It fetches weights, builds the engine, starts the server and any shim, waits
until the endpoint answers, then hands you the agent. It asks before every
download (`LOCAL_AGENT_YES=1` skips that) and will not start a second engine
on a busy port. Logs in `~/.local-llm-agent/`.

Four things it gets right that are easy to miss by hand: `fast`'s 30 GB PLE
sidecar, the tool-format shim (worth **23 points of pass rate**,
[#112](https://github.com/evanwtf/local-llm/issues/112)), the Anthropic wire
for Claude Code, and OpenCode's provider block — an undeclared model exits in
0.6s looking exactly like a model failure
([#69](https://github.com/evanwtf/local-llm/issues/69)). `scripts/README.md`
lists every other script.

---

## Where the rest of it went

| | |
|---|---|
| running each stack by hand, and why this ranking | [`docs/stacks.md`](docs/stacks.md) |
| every backend's numbers, and what they cannot say | [`docs/results.md`](docs/results.md) |
| what the benchmark does, task by task | [`benchmarks/agent/METHODOLOGY.md`](benchmarks/agent/METHODOLOGY.md) |
| the machine, and what a comparison must do | [`docs/m5max-runbook.md`](docs/m5max-runbook.md) |
| traps that have cost a measurement | [`AGENTS.md`](AGENTS.md) · what to do next: [`NEXT.md`](NEXT.md) |

> ⚠️ **OpenCode results before 2026-08-31 21:47 EDT are INVALID** — the client was never told which directory to work in. **Other clients are unaffected.** The ledger holds none of these rows now; do not quote them from older documents either: [what happened](docs/archive/results-opencode-pre-dir.md).
