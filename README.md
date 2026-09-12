# local-llm

> ⚠️ **OpenCode results before 2026-08-31 21:47 EDT are INVALID** — the client was never told which directory to work in. **Other clients are unaffected.** The ledger holds none of these rows now; do not quote them from older documents either: [what happened](docs/archive/results-opencode-pre-dir.md).

Find and document the best **model + engine + harness** combination for running
a coding agent locally, judged on code quality, problem solving, and speed. The
answer is a combination, not a model: the same weights that are slowest under
one client are among the fastest under another, so every result names all three
axes. This is a benchmark harness and a body of measurements, not a product --
a collection of Python scripts and shell wrappers run from a checkout, plus the
`RESULTS.md` files they produce.

Everything is measured on one machine: MacBook Pro M5 Max, 128 GiB, macOS
26.6.2.

| axis | what it means | example |
|---|---|---|
| **model** | the weights, at a specific quantization | Qwen3.8-Flash-Next `UD-Q3_K_XL` |
| **engine** | what serves them | llama.cpp, Ollama, ds4/DwarfStar |
| **harness** | the agent driving the loop | **OpenCode** (primary), Claude Code, Codex |

**Which model should I run?** See
[RECOMMENDATIONS.md](RECOMMENDATIONS.md) -- current picks for this Mac, the
evidence behind them, and the gaps still open.

## Usage

### Run a local coding agent

```sh
# Starts ds4-server if needed (~91 GiB resident, ~26 s).
hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/ds4/0731/agent/ds4-up start

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

**Unset `ANTHROPIC_API_KEY` in that shell** -- if it is set it wins, and the
session silently runs against the hosted API. `run.py` pops it for this reason.
`dsv4-local` is a non-secret local token. The server outlives the client; stop
it with `ds4-up stop`.

Codex on the same weights is supported but **takes 2.14x as long on Swift**
(#44): `CODEX_API_KEY=dsv4-local codex --profile ds4`.

For Qwen via Ollama: `ollama pull qwen3.8:27b-mlx && ./claude-ollama`. If Ollama
rejects Claude Code's request shape, see
[docs/ollama-claude-shim.md](docs/ollama-claude-shim.md).

### Serve ds4 on a chosen Metal route

```sh
scripts/ds4-fast.sh        # Metal 4 TensorOps: ~21% quicker, NOT bit-exact
scripts/ds4-vanilla.sh     # reference kernels: bit-exact, slower
```

Both wrap `scripts/ds4_serve.py` and bind the same port, so the OS makes them
mutually exclusive. The route is **asserted from the server log**, not assumed;
the server is killed if it cannot be confirmed. Use `vanilla` for any
reproducible quality number -- `fast` flips greedy tokens on long prompts
(#149).

### Run the benchmark

```sh
uv run python benchmarks/agent/preflight.py            # always first
uv run python benchmarks/agent/run.py --backend <name> --client opencode --trials 3
```

Selected flags (`--help` for all):

| flag | what it does |
|---|---|
| `--backend`, `--task`, `--trials` | what to run, and how many times |
| `--client {aider,claude,codex,opencode}` | which agent drives the loop |
| `--results PATH` | ledger to append to (per-machine) |
| `--targets {legacy,sandbox}` | use the harness's own checkouts instead of borrowing yours (#146) |
| `--require-harness-head SHA` | refuse trials if the harness commit moved mid-run |
| `--dry-run`, `--no-lock`, `--skip-smoke` | inspection and batch control |

### Preflight: check what is already running

`preflight.py` reports five kinds of machine state that silently change
results: running model servers, the Metal ceiling (`iogpu.wired_limit_mb`, 107.52
-> 112.00 GiB), tool versions, upstream `ds4` preview branches, and GitHub
mentions. It **warns and never refuses**.

This matters more here than on a normal machine. Models are sized to nearly fill
unified memory, so a server left running either fails loudly, or fits and
contends for memory and bandwidth for the whole batch -- and then every number
describes a machine that was busy doing something else. **Check, do not
remember.**

### The scripts

Every script stamps each line with the harness commit and machine
(`[88ed87b@M5-Max-128GB]`), so a line pasted out of context still says what
produced it. Full index: [scripts/README.md](scripts/README.md) (43 scripts).

| script | what it does |
|---|---|
| `benchmarks/agent/run.py` | **The harness.** Trials of model x task x client |
| `benchmarks/agent/preflight.py` | Machine state before a batch |
| `benchmarks/agent/summarize.py` | Per-task table across every backend |
| `benchmarks/agent/sizing.py` | How many trials a claim needs |
| `scripts/report.py` | Summarize or compare cells, with #23's resolution rule |
| `scripts/stack_agent_report.py` | Paired A/B of two whole stacks, with void checks |
| `scripts/upstream_sweep.py` | Commits and releases across the 19 repos we watch |
| `scripts/hf_sweep.py` | New quants of models we run; `--find` picks for a machine |
| `scripts/verify_posts.py` | Verify X posts before repeating a claim |
| `scripts/thermals.py`, `scripts/sensor_windows.py` | Die temperatures; join sensor CSV to sweep windows |
| `scripts/hardware_id.py` | This machine's results-directory name. **Never type one by hand** |
| `scripts/install-metal-ceiling.sh` | Persist the Metal wired limit across reboots |

## Build and run

Requires [uv](https://docs.astral.sh/uv/); `pyproject.toml` sets
`requires-python = ">=3.11"`.

```sh
uv sync
uv run pre-commit install          # wires the commit-time guard into .git/hooks
uv run pytest -q        # the full suite (2,600+ tests)
```

CI (`.github/workflows/test.yml`) runs `uv sync`, `uv run pre-commit install`,
`uv run pytest -q`, `ruff format --check`, `ruff check`, and a `sh -n` syntax
check. **`mypy` is not yet in CI** -- see #154.

`pre-commit` is a dev dependency. `uv run pre-commit install` writes its git
hook; the first hook in `.pre-commit-config.yaml` refuses a commit while a
stack_agent A/B is live, because a commit mid-run moves HARNESS_HEAD and kills
every remaining sweep (#227). A test asserts the hook is installed, so a clone
that skips the install step fails the suite instead of silently losing the
guard.

Engines and weights live outside this checkout; scripts find ds4 via `DS4_ROOT`
and write results here.

## How the benchmark works

A function body is excised from a real repository at a pinned commit; the agent
must restore it. The repository's **own test suite** is the sole oracle -- pass
or fail, no partial credit, no judge.

| target repo | language | tests | oracle | runs on |
|---|---|---|---|---|
| `~/git/gmail-archive` | Python | 71 | `uv run pytest -q`, ~0.85 s | **any platform** |
| `~/git/monitor` | Swift | 215 | `swift test`, ~0.7 s | **macOS only** |

Both are pinned on a `local-llm-benchmark` branch, and **results from the two
are not pooled** -- different repository, language and oracle.

**The Swift half is macOS-only.** `monitor` is a macOS desktop application: it
builds against AppKit and the Apple SDKs, so `swift test` cannot be the oracle
on Linux even where a Swift toolchain exists. A non-Mac machine runs the Python
excision and script tasks and simply has no Swift row to take -- that is a
missing cell, not a failure, and never a zero. **Python is the cross-platform
spine of this suite**; any comparison that spans machines of different
platforms must be Python-only, or it is comparing a subset to a whole.

| criterion | status |
|---|---|
| **problem solving** | measured |
| **speed** | measured -- wall seconds for the whole loop, not tokens/sec |
| **code quality** | **not yet measured.** The tasks are easy enough that nearly every backend passes (#4) |

Reliability turned out to matter more than any of the three, so pass rates carry
confidence intervals: a perfect 21/21 only establishes ">85%".

Why the suite is shaped this way, what the Swift repository exposed that Python
could not, and why the agent client is held to the same open-source standard as
the model and engine: [docs/benchmark-design.md](docs/benchmark-design.md).

**Not in scope:** interactive chat, vendor leaderboards, vision/RAG/embeddings,
and chasing tokens/sec -- raw generation speed is nearly irrelevant to agent wall
time, which prompt re-prefill dominates (#14).

## Project layout

| path | what is there |
|---|---|
| `benchmarks/agent/` | the harness, its tasks and its own tests |
| `scripts/` | measurement, field-watching and machine tools |
| `hardware/<machine>/` | results, logs and `RESULTS.md` for one machine |
| `docs/` | [changelog](docs/changelog.md), [history](docs/history.md), [runbook](docs/m5max-runbook.md), archive |
| `docs/node-exporter-cpufreq-deadlock-arm64.md` | not a benchmark finding: node_exporter deadlocks on aarch64 with `cppc_cpufreq`, and the symptom points away from the cause |
| `logs/sweeps/` | gather archives; the same fact on either machine |

Work is tracked as GitHub issues. [NEXT.md](NEXT.md) holds the order to work in,
[docs/changelog.md](docs/changelog.md) what shipped and why (before v1.0.0:
[docs/history.md](docs/history.md)),
[SOURCES.md](SOURCES.md) who to watch in the field, and
[CONVENTIONS.md](CONVENTIONS.md) the standing rules -- read it before deleting
weights or committing logs.

**Working in this repo as an agent: [AGENTS.md](AGENTS.md).** It holds the loop,
the conventions and the hard-won rules; none of it is repeated here.

## License

[LICENSE](LICENSE).
