# What to actually run

**A local coding agent on an Apple Silicon Mac, when you cannot or will not
use a hosted provider.** Every number was measured by `benchmarks/agent/` on
one machine — an **M5 Max, 128 GB, macOS 26**. Nothing here is from a model
card. Paste section 1, pick a row in 2, or run one script in 3; the rest moved
to [`docs/`](#where-the-rest-of-it-went).

**Ledger last read 2026-09-10; ranking re-verified 2026-09-12.** These numbers
are a **pre-macOS-27 baseline**
([#306](https://github.com/evanwtf/local-llm/issues/306)) — re-validate after
that upgrade. A read date more than a couple of weeks old means re-check the
ledger before trusting the rows.

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
| **you want it fastest** | Qwen3.8-Flash-Next `MLX mixed-4/8bit` | **mlx-serve 26.9.2** | ~100 GiB | **60/60** | ~56s† |
| **you want the same model on a lighter engine** | Qwen3.8-Flash-Next `Q4_K imatrix` | ds4, ivanfioravanti's fork | 98 GiB | **196/196** | 95s |
| **you want a mainline engine** | Qwen3.8-Flash-Next `UD-Q3_K_XL` | llama.cpp | 84 GiB | **75/75** | 106s |
| **you want a second lineage** | DeepSeek-V4-Flash | ds4 (DwarfStar) | 91 GB | **30/30** | 115s |

**The fastest verified stack is now mlx-serve 26.9.2, as of 2026-09-10 (#282).**
In a head-to-head agent A/B with **both engines on their latest builds** — ds4
`6c1e8367` vs mlx-serve `26.9.2`, same model (Qwen3.8-Flash-Next), two runs,
60 trials/arm — **mlx-serve took 66% of ds4's wall time (34% less): 818 s
against 1237 s** summed over the 15 tasks, at **equal pass (60/60 both)**,
winning 12 of 15 tasks. Both runs agreed (paired ratio 1.50 and 1.51) and both
position orders agreed. This **reverses #191's 2026-09-08 dead heat**: the
`perf(qwen4)` batch in mlx-serve 26.9.2 roughly halved its own wall since
26.9.1, and ds4's newer head (`6c1e8367`, #228) narrowed but did not close the
gap.

**The 34% is aging — re-run it before trusting it.** That A/B used ds4 at
`6c1e8367`. ds4 has since gained prefill work never re-tested head-to-head:
level-2 MoE tiles (+17–23% appended prefill on the `kimat` pack, now the
promoted default, [#328](https://github.com/evanwtf/local-llm/issues/328)) and
continued [#952](https://github.com/evanwtf/local-llm/issues/952). Treat the
gap as current only until it is re-measured against the current ds4 head.

**It is a full-stack result, and that is the right way to read it.** Engine,
quantization and speculative decoding move together: mlx-serve speculates by
default (Prompt Lookup Decoding on, [#262](https://github.com/evanwtf/local-llm/issues/262)),
ds4 `kimat` does not. The win cannot be credited to the engine alone — but the
speculation is the engine's shipped default, "what you get when you install
it," which is what this file measures. The cost side: mlx-serve holds ~100 GiB
resident against ds4's ~68–80 GiB.

† `~56s` is the median per-task wall from the #282 A/B (ds4 `kimat` was 75 s in
the same A/B); it is not directly comparable to the 95 s in the ds4 row, which
was taken in the earlier 196/196 regime. The rigorous comparison is the
head-to-head ratio above, not the two medians side by side.

**On the lighter engine (ds4 `kimat`) against llama.cpp:** wall ratio 0.84
(95% CI 0.76–0.92), 90/90 both arms, 13 of 15 tasks favoring ds4. It loads
only on ivanfioravanti's trees, and this file has already had one stack
withdrawn by its author.

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
