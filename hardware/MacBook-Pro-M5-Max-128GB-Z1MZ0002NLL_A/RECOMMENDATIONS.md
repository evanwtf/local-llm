# What to actually run on the M5 Max

**A local coding agent on an Apple Silicon Mac (M5 Max, 128 GB, macOS 27), when
you cannot or will not use a hosted provider.** Every number was measured by
`benchmarks/agent/` on this machine. Nothing here is from a model card. Paste
section 1, pick a row in 2, or run one script in 3; the rest moved to
[`docs/`](#where-the-rest-of-it-went). The map of all machines is the root
[`RECOMMENDATIONS.md`](../../RECOMMENDATIONS.md).

**Ledger last read 2026-09-19 ([#524](https://github.com/evanwtf/local-llm/issues/524)).**
Section 2's pass and median columns come from `scripts/reco_rows.py`, pooled
across engine builds and macOS 26 and 27. A read date more than a couple of
weeks old means re-check the ledger before trusting the rows.

**macOS 27 (2026-09-18, [#306](https://github.com/evanwtf/local-llm/issues/306))
did not break a pick:** mlx-serve, llama.cpp and ds4 main each passed 45/45 on
it ([`docs/macos-26-vs-27.md`](../../docs/macos-26-vs-27.md)). The macOS 26
side is one trial per task, so its wall times are not an OS effect.

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
| **you want it fastest** | Qwen3.8-Flash-Next `MLX mixed-4/8bit` | **mlx-serve** (26.9.1–26.9.4) | ~100 GiB | **372/376** | 52s |
| **you want the same model on a lighter engine** | Qwen3.8-Flash-Next `qwen38-q4k` | ds4 upstream main | 165 GiB | **119/120** | 109s |
| **you want a mainline engine** | Qwen3.8-Flash-Next `UD-Q3_K_XL` | llama.cpp | 84 GiB | **135/135** | 121s |
| **you want a second lineage** | DeepSeek-V4-Flash | ds4 (DwarfStar) | 91 GB | **30/30** | 115s |

Every row counts passed / usable OpenCode trials and the median passing
excision task. The rows come from different weeks; the head-to-head below is
the rigorous comparison.

**The ds4 row left the `kimat` fork for upstream main on 2026-09-19** (#158:
59/60 against 60/60, paired wall 0.96, 95% CI 0.87–1.06). It needs no sidecar;
95 GiB of its 165 GiB file stays on disk. Steps: [`docs/stacks.md`](../../docs/stacks.md).

**Measured and not picked:** Ternary Bonsai 2 27B on mlx-serve main passed 27
of 44 trials (95% CI 47–74%). Its median passing excision task took 326 s
([#479](https://github.com/evanwtf/local-llm/issues/479)).

**The fastest verified stack is mlx-serve, re-confirmed at the current ds4
upstream head on 2026-09-20
([#577](https://github.com/evanwtf/local-llm/issues/577)).** In an interleaved
agent A/B on **macOS 27.0** — ds4 upstream main `8db1d1d1` (the
[#158](https://github.com/evanwtf/local-llm/issues/158)-settled stack, rebuilt
clean on the 27 toolchain) vs mlx-serve `26.9.4`, same model
(Qwen3.8-Flash-Next), 4 sweeps, 60 trials/arm — **mlx-serve took 65% of ds4's
wall time (35% less): 3770 s against 5822 s** summed over the 15 tasks. The
paired wall ratio (geometric mean of per-task ratios) is **1.84 (95% CI
1.59–2.13)**, with mlx-serve faster on 14 of 15 tasks and one tie; all four
sweeps agreed on the direction. Pass rates were **indistinguishable —
mlx-serve 60/60, ds4 59/60** (one multi-turn death; sign test p=1.00).

This **refreshes the earlier #282 figure** (34% less at ds4 `6c1e8367`,
2026-09-10): the gap holds at the current upstream head `8db1d1d1`, which has
since taken level-2 MoE tiles
([#328](https://github.com/evanwtf/local-llm/issues/328)) and
[#952](https://github.com/evanwtf/local-llm/issues/952) — none of it closed the
agent-wall gap at screen resolution. This A/B is a pre-registered SCREEN
(n=60/arm resolves ~17–26% paired wall), so it establishes the direction and
rough size, not a precise superiority margin.

**It is a full-stack result, and that is the right way to read it.** Engine,
quantization and speculative decoding move together: mlx-serve speculates by
default (Prompt Lookup Decoding on, [#262](https://github.com/evanwtf/local-llm/issues/262)),
ds4 `kimat` does not. The win cannot be credited to the engine alone — but the
speculation is the engine's shipped default, "what you get when you install
it," which is what this file measures. The cost side: mlx-serve holds ~100 GiB
resident against ds4's ~68–80 GiB.

**On ds4 against llama.cpp:** wall ratio 0.84 (95% CI 0.76–0.92), 90/90 both
arms, 13 of 15 tasks favoring ds4. That A/B ran the `kimat` fork, before the
row moved to upstream main. Upstream main matched the fork in #158, but it has
not run against llama.cpp itself.

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
| running each stack by hand, and why this ranking | [`docs/stacks.md`](../../docs/stacks.md) |
| every backend's numbers, and what they cannot say | [`docs/results.md`](../../docs/results.md) |
| what the benchmark does, task by task | [`benchmarks/agent/METHODOLOGY.md`](../../benchmarks/agent/METHODOLOGY.md) |
| the machine, and what a comparison must do | [`docs/m5max-runbook.md`](../../docs/m5max-runbook.md) |
| traps that have cost a measurement | [`AGENTS.md`](../../AGENTS.md) · what to do next: `scripts/make_next.py --platform macos` |

