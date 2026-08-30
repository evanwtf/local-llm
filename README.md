# local-llm

**Purpose: find and document the best model + engine + harness combination for
running a coding agent locally, judged on code quality, problem solving, and
speed.**

The answer is a *combination*, not a model. That is the central finding here and
it is why every result is reported on three axes:

| axis | what it means | example |
|---|---|---|
| **model** | the weights, at a specific quantization | Qwen3.8-Flash-Next `UD-Q3_K_XL` |
| **engine** | what serves them | llama.cpp, Ollama, ds4/DwarfStar |
| **harness** | the agent driving the loop | Claude Code, Codex, OpenCode |

The harness matters as much as the model, and the ranking **inverts** across
backends — the same weights that are slowest under one client are among the
fastest under another. A benchmark that reports a model without naming its engine
and harness is not describing something you can reproduce.

## What this is for

A working fallback for when hosted inference is unavailable or unaffordable.
Concretely: something that can be handed a real repository, told to fix a real
failure, and left to run an implement-test-verify loop to a green test suite.

## What this is NOT for

- **Interactive chat.** Chatbot quality, conversational feel, persona, and
  response latency in a REPL are all out of scope.
- **Benchmark leaderboards.** Vendor scores (SWE-bench and friends) are recorded
  as context, never reproduced or defended here.
- **Vision, RAG, embeddings, creative writing.** Multimodal weights are used when
  a model ships them, but nothing measures those capabilities.
- **Chasing the fastest tokens/sec.** Raw generation speed is nearly irrelevant
  to agent wall time — prompt re-prefill and context handling dominate. See
  issue #14.

## What is actually measured today

Being precise, because the three criteria are not equally covered:

| criterion | status |
|---|---|
| **problem solving** | **measured.** A function body is excised from a real repo at a pinned commit; the agent must restore it. The repo's **own test suite** is the sole oracle — `uv run pytest          # 199 tests: the harness's own, not the benchmark's` for the Python target, `swift test` for the Swift one. Pass or fail, no partial credit, no judge. |
| **speed** | **measured.** Wall seconds for the whole agent loop, which is the number a human waits on — not tokens/sec. |
| **code quality** | **not yet measured.** The tasks are easy enough that nearly every backend passes, so the suite cannot currently distinguish good code from code that merely passes. This is the project's biggest known gap — issue #4. |

Reliability turned out to matter more than any of the three: most
backend×harness pairs fail a meaningful share of these easy tasks, and a
combination that fails one task in five is not usable however fast it is.
Pass rates are reported with confidence intervals for that reason — a perfect
21/21 only establishes ">85%".

Everything is measured on one machine — MacBook Pro M5 Max, 128 GiB,
macOS 26.6.2 — so numbers across engines share a hardware baseline.

| | |
|---|---|
| [`ollama_claude_shim.py`](ollama_claude_shim.py), [`claude-ollama`](claude-ollama) | drive Claude Code with an Ollama model |
| [`benchmarks/ollama/`](benchmarks/ollama/RESULTS.md) | Qwen3.8-27B: speed, agentic accuracy, speculative decoding |
| [`benchmarks/ds4/0731/`](benchmarks/ds4/0731/REPORT.md) | DeepSeek V4 Flash quant comparison, thermals, long context |
| [`benchmarks/ds4/coding/`](benchmarks/ds4/coding/RESULTS.md) | HumanEval, mixed q2/q4 vs MXFP4 |
| [`benchmarks/llamacpp/`](benchmarks/llamacpp/RESULTS.md) | Qwen3.8-Flash-Next at 2-bit: runs, passes, slowest measured |
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

Both rows were measured on Ollama 0.32.14-rc0. The installed engine is now
**0.33.1**, so they predate it; re-baselining the Ollama rows under 0.33.1 is
an open loose end.

**The ceiling on this machine is a resident-weight ceiling, not a speed one.**
Qwen3.8-Flash-Next is 125B total but only 6B active, and the quant decides
whether it runs at all: Ollama's 112 GB nvfp4 tag peaks at 126.51 GiB and dies on
the first agent-sized prompt, while Unsloth's 2-bit GGUF serves the same model in
77.9 GiB. Read "A6B" as a throughput claim, never a memory one.

That ceiling is **tunable, and this project got it wrong for a long time**. The
"107.0 GiB Metal budget" quoted in older notes is `recommendedMaxWorkingSetSize`
on a stock machine — a macOS default, not hardware. `iogpu.wired_limit_mb` now
raises it to **112.00 GiB** (issue #30). Several "too big for this machine"
verdicts rested on the old number and are being revisited.

`GLM-5.3-Flash` is one of them: it *fits* (antirez's Q2 is 89.9 GiB, Unsloth's
`UD-Q2_K_XL` is 101.3 GiB), but no working engine/weights pair exists for it yet
— see issues #25 and #32. See
[`benchmarks/llamacpp/RESULTS.md`](benchmarks/llamacpp/RESULTS.md).

---

# The Ollama shim

> **Not needed on Ollama 0.32.14-rc0 or later** — but still needed elsewhere.
> That release contains the upstream fix, verified here: `ollama launch claude
> --model qwen3.8:27b-mlx` works with no proxy and no environment variables.
>
> The bug is not Ollama's, though; it is a property of the request Claude Code
> sends, so any server whose chat template rejects a trailing `system` message
> hits it. **llama.cpp does**, as of 2026-08-26: its `/v1/messages` passes the
> stray message through and the Qwen template raises. The shim fixed it
> unchanged, pointed at llama-server with `--upstream`, and
> [`benchmarks/llamacpp/llamacpp-up`](benchmarks/llamacpp/llamacpp-up) now
> starts it as part of that stack. It is named for the bug it was written for,
> not for the only server that has it.

Ollama exposes an Anthropic-compatible `/v1/messages` endpoint and ships a
`launch claude` integration. On Ollama 0.32.13 and earlier that integration does
not work: Ollama rejects the request shape Claude Code sends.

Tested on macOS 26.5 with Ollama 0.32.13 and `qwen3.8:27b-mlx`.

## Which model should I run?

See [**RECOMMENDATIONS.md**](RECOMMENDATIONS.md) — the current picks for this
Mac (M5 Max, 128 GiB), with the evidence behind them and the gaps still open.

It is **two choices, not one**: a model and an agent client. They interact, and
no client is fastest on every backend. The
[agent benchmark](benchmarks/agent/README.md) measures both axes over 238
trials.

## How this project is run

Work is tracked as GitHub issues; [`NEXT.md`](NEXT.md) holds the order to work
in and the machine state that is not in git;
[`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) holds the current top picks and how
to start them. Measurements land in the `RESULTS.md` beside the benchmark that
produced them. The full loop, and what belongs in which file, is in
[`AGENTS.md`](AGENTS.md).

## Two target repositories, two languages

The benchmark excises a function from a real repository and asks the agent to
restore it. There are two:

| repo | language | size | tests | oracle |
|---|---|---|---|---|
| `~/git/gmail-archive` | Python | 1,833 lines | 71 | `uv run pytest -q`, ~0.85 s |
| `~/git/monitor` | Swift | 11,265 lines | 215 | `swift test`, 0.705 s |

**The second exists because the first ran out of things to find.** gmail-archive
has 52 functions, a median of 13 lines, and exactly *one* function carrying the
surface that produced the only code-quality defect in 18 trials. A larger
codebase in a language these models see less often is the test of whether that
was the task set or the repository (#4, #42).

Both are pinned on a `local-llm-benchmark` branch. That is not ceremony: on
gmail-archive, `origin/main` had moved **73 commits** ahead of the pinned base
while the working checkout sat held back, and a routine `git pull` would have
silently changed what every trial measures.

**Results from the two are not pooled.** Different repository, language and
oracle — a new series.

**What the second repository actually taught, which was not the question asked.**
Swift did **not** make the tasks harder to pass — 44/45 on the first set, 8/8 on
a harder second set (#44, #45). The repository was not the limit on correctness.
What it exposed instead is a measurement Python cannot produce here:

- **`swift test` has a build step**, so an agent can fail by emitting code that
  does not compile. A Python syntax error is a pytest collection error; there is
  no separate build to fail. One trial in 53 has failed this way.
- **How much more a pair writes on unfamiliar ground varies 2.3x**, from 1.19x
  to 2.73x moving Python → Swift. Wall time tracks output tokens at r=0.98, so
  this is a practical number, not a curiosity.
- **That gap widens with difficulty** (#45): between the terse and verbose pairs
  it went 5.42x → 8.26x on tokens when the tasks got harder. Measuring inflation
  on easy tasks *under*-estimates the spread on hard work.

**Caveat carried on every one of those numbers:** the Swift tasks are not
difficulty-matched to the Python ones, so the ordering is sound and the absolute
ratios are not.

## Preflight: always check what is already running

**Do this before starting a server, and before every benchmark batch.**

```sh
uv run python benchmarks/agent/preflight.py
```

It reports **five kinds of machine state that silently change results**, each
added after it caused a real problem:

| check | why |
|---|---|
| running model servers | a server left up contends for memory and bandwidth all run |
| **Metal ceiling** | `iogpu.wired_limit_mb` raises it 107.52 → 112.00 GiB and **does not survive a reboot**; it decides whether a large model loads |
| tool versions | Codex, Ollama, OpenCode and llama.cpp ship several times a day |
| **sherpa branches** | antirez ships models on preview branches; one existed for GLM-5.3 while this project benchmarked it on an unsupported stack |
| GitHub notifications | mentions on `antirez/ds4` and `ggml-org/llama.cpp`, CI noise excluded |

The headline line answers the first two:

```
INFO preflight: 0.0 GiB held by model servers, 112.0 GiB headroom under a
     112.00 GiB Metal ceiling (RAISED by sysctl -- does not survive a reboot)
WARNING preflight: llama-server (pid 43967) is listening on :8030 and holding
        77.6 GiB, but no selected backend uses that port. Stop it, or this batch
        measures a contended machine.
WARNING preflight: ds4 has a recent branch 'glm-5.3-flash' you are not on
```

**The ceiling line is not decoration.** A disagreement with upstream about
whether a GLM-5.3 bug reproduced turned entirely on it: ds4 plans a 108.01 GiB
working set that fits under a raised 112 GiB ceiling and fails under the 107.52
GiB default. Neither side had checked, because nothing reported it.

**Why it matters more here than on a normal machine.** These models are sized to
nearly fill unified memory: `glm53` is 100.6 GiB resident against a 112 GiB
ceiling. A server left running from an earlier session does one of two things,
and only one of them is obvious.

* The next model **does not fit and fails to load**. Loud, immediate, cheap.
* The next model **does fit**, and the two contend for memory and bandwidth for
  the whole run. Quiet, and expensive: every number in that batch describes a
  machine that was busy doing something else. An hour was already lost this way
  once, to a 96 GB download that overlapped a timing batch — and a resident
  77.6 GiB model is the same mistake with nothing as visible as a download to
  notice it.

A server does not stop when its client exits. `./claude-ollama stop` and
`ds4-up stop` release theirs; a bare `llama-server` has to be killed. **Check,
do not remember** — the memory is held whether or not anyone is using it.

`run.py` runs this check itself before the first trial, so a stale server is
named at the top of the log instead of being inferred from odd numbers a week
later. It **warns and never refuses**: running two servers on purpose is
legitimate, and a harness that will not start because it disapproves of the
process table is worse than one that says what it sees.

The ceiling is a stated assumption, not a reading. `sysctl iogpu.wired_limit_mb`
reports `0` whether or not a limit is in force, so it cannot be trusted as a
source; verify a changed ceiling with the Metal probe in issue #30. **The
sysctl does not survive a reboot.**

## Quick start

### The local coding agent to run: DS4 + Claude Code

```sh
# Run from this checkout. Starts ds4-server if needed (~91 GiB resident, ~26 s).
benchmarks/ds4/0731/agent/ds4-up start

# Every alias must be set: the client picks a different model per role, and an
# unset alias silently reaches for a hosted model.
ANTHROPIC_BASE_URL=http://127.0.0.1:8000 \
ANTHROPIC_AUTH_TOKEN=dsv4-local \
ANTHROPIC_MODEL=deepseek-v4-flash \
ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-flash \
ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-flash \
ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-flash \
  claude
```

**Unset `ANTHROPIC_API_KEY` in that shell** — if it is set it wins, and the
session silently runs against the hosted API. `run.py` pops it for this reason.

Codex on the same weights is a supported alternative, equal on Python but
**2.14x slower on Swift** (#44), so it is no longer the default:

```sh
CODEX_API_KEY=dsv4-local codex --profile ds4
```

`dsv4-local` is a non-secret local API token. The server continues running
after either client exits; release its memory with
`benchmarks/ds4/0731/agent/ds4-up stop`.

### Qwen + Claude Code

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

The ds4 generation figure is from build `b030961` (2026-08-08). The synced
build `fdcf3aa` measures **40.6 t/s** — see
[`benchmarks/agent/RESULTS.md`](benchmarks/agent/RESULTS.md).

Caveat: the two are not measured identically. `ds4-bench` reports a
2048-token prefill at a given context; the Qwen figure is a single 11,451-token
prefill, and longer prefills batch better. The generation numbers are directly
comparable; the prefill numbers are indicative only.
