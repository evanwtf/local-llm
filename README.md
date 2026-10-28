# local-llm

[![CI](https://github.com/evanwtf/local-llm/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/evanwtf/local-llm/actions/workflows/test.yml)
[![no session IDs](https://github.com/evanwtf/local-llm/actions/workflows/no-session-ids.yml/badge.svg)](https://github.com/evanwtf/local-llm/actions/workflows/no-session-ids.yml)
[![latest release](https://img.shields.io/github/v/release/evanwtf/local-llm?sort=semver)](https://github.com/evanwtf/local-llm/releases)
[![last commit](https://img.shields.io/github/last-commit/evanwtf/local-llm)](https://github.com/evanwtf/local-llm/commits/main)
[![commit activity](https://img.shields.io/github/commit-activity/w/evanwtf/local-llm)](https://github.com/evanwtf/local-llm/pulse)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/github/license/evanwtf/local-llm)](LICENSE)

Find and document the best **model + engine + harness** combination for running
a coding agent locally, judged on code quality, problem solving, and speed. The
answer is a combination, not a model: the same weights that are slowest under
one client are among the fastest under another, so every result names all three
axes. This is a benchmark harness and a body of measurements, not a product:
Python scripts run from a checkout, plus the `RESULTS.md` files they produce.

| axis | what it means | example |
|---|---|---|
| **model** | the weights, at a specific quantization | DeepSeek-V4.1-Flash-1017 EXL3 2.7 bpw |
| **engine** | what serves them | vLLM, llama.cpp, Ollama, mlx-serve, ds4 |
| **harness** | the agent that drives the loop | **OpenCode** (primary); Claude Code and Codex as references |

**Which model should I run?** See [RECOMMENDATIONS.md](RECOMMENDATIONS.md): a
map to each machine's picks, the evidence behind them, and the commands.

The active machines are the **M5 Max MacBook Pro** (128 GB, macOS 27) and a
**two-node DGX Spark cluster** (2 × GB10, 2 × 128 GiB, 200 Gb/s RoCE). The
cluster serves one model across both nodes to a coding agent on another
machine. Every number belongs to the machine that produced it and is never
pooled. The full registry is [hardware/MACHINES.md](hardware/MACHINES.md).

## Bootstrap

From a bare machine to a checked harness. You need `git` and `curl`.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh            # uv; it fetches its own Python
git clone https://github.com/evanwtf/local-llm.git
cd local-llm
uv sync                                                     # .venv from uv.lock
uv run pre-commit install                                   # commit, commit-msg and pre-push hooks
uv run pytest -m fast -q                                    # the push gate, seconds
uv run python scripts/machines.py --check                   # exit 1: not a managed machine
uv run python scripts/sync_sandbox_targets.py \
  --origin gmail-archive=https://github.com/evanwtf/gmail-archive.git \
  --origin monitor=https://github.com/evanwtf/monitor.git   # the target repos, into sandbox/
uv run python benchmarks/agent/preflight.py                 # what is running, and are the engines current
```

`--origin` is needed only where `~/git/<name>` is not already a checkout. A
test asserts the hooks are installed, so a clone that skips
`pre-commit install` fails the suite. The first hook refuses a commit while a
stack A/B runs, because a commit mid-run moves the harness commit and voids the
remaining sweeps (#227).

The harness does not contain the engines or the weights. Clone and build the
engines per [docs/external-repos.md](docs/external-repos.md) (`DS4_ROOT` and
each backend's `engine_tree` or `recipe_dir`). Weights go in the model trees in
[docs/model-locations.md](docs/model-locations.md). To stand up one measured
stack by hand, follow [docs/howto.md](docs/howto.md).

## Run the benchmark

```sh
uv run python benchmarks/agent/preflight.py            # always first
uv run python benchmarks/agent/run.py --backend <name> --client opencode --trials 3
```

Selected flags (`--help` for all):

| flag | what it does |
|---|---|
| `--backend`, `--task`, `--trials` | what to run, and how many times |
| `--replay`, `--replay-hard` | add the replay and hard task sets (need `--targets sandbox`) |
| `--client {aider,claude,codex,opencode,unreal}` | which agent drives the loop (default `opencode`) |
| `--results PATH` | ledger to append to (per machine) |
| `--targets {legacy,sandbox}` | build trial checkouts from `sandbox/` instead of `~/git` (#146) |
| `--require-harness-head SHA` | refuse to start unless the harness is at this commit, clean |
| `--dry-run`, `--no-lock`, `--skip-smoke` | inspection and batch control |

A live run must start from a commit on `main` (#1281); `--dry-run` may run
from a branch.

**Preflight** reports the machine state that silently changes results: running
model servers, the memory ceiling (on the M5 Max, the Metal
`iogpu.wired_limit_mb`), tool versions, CI status, and GitHub mentions. Models
are sized to nearly fill unified memory, so a server left running either fails
loudly or contends for memory and bandwidth for the whole batch. Most checks
warn. Preflight **refuses** when an engine is behind its latest release (#1049),
and `--acquire-lock` refuses when the machine is taken.

### ds4 on a chosen Metal route (M5 Max)

```sh
scripts/ds4-fast.sh        # Metal 4 TensorOps: ~21% quicker, NOT bit-exact
scripts/ds4-vanilla.sh     # reference kernels: bit-exact, slower
```

Both wrap `scripts/ds4_serve.py` and bind the same port, so only one can run.
The script reads the route from the server log and kills the server if it
cannot confirm it. Use `vanilla` for a reproducible quality number: `fast`
flips greedy tokens on long prompts (#149).

### The scripts

Every script stamps each log line with the harness commit and machine
(`[88ed87b@M5-Max-128GB]`), so a pasted line still says what produced it. The
generated index of every script, with its platform, is
[scripts/README.md](scripts/README.md).

| script | what it does |
|---|---|
| `benchmarks/agent/run.py` | **The harness.** Trials of model × task × client |
| `benchmarks/agent/preflight.py` | Machine state before a batch |
| `benchmarks/agent/summarize.py` | Per-task table across every backend |
| `benchmarks/agent/sizing.py` | How many trials a claim needs |
| `scripts/report.py` | Summarize or compare cells, with #23's resolution rule |
| `scripts/stack_agent_report.py` | Paired A/B of two whole stacks, with void checks |
| `scripts/upstream_sweep.py` | Commits and releases across the watched repos |
| `scripts/hf_sweep.py` | New quants of models we run; `--find` picks for a machine |
| `scripts/hardware_id.py` | This machine's results-directory name. **Never type one by hand** |

## Develop

```sh
uv run pytest -q          # the full suite (about 4,800 tests); read the exit code
uv run mypy               # scope in [tool.mypy]
uv run ruff format --check . && uv run ruff check .
```

CI (`.github/workflows/test.yml`, on push to `main` and on every PR) runs the
suite with `-rs` and strict skips, `scripts/validate_ledgers.py` over every
committed ledger, a `sh -n` check of `claude-ollama`, then `ruff format
--check`, `ruff check` and `mypy`. The rules for agents working here are in
[AGENTS.md](AGENTS.md).

## How the benchmark works

The agent gets real work in a real repository at a pinned commit, and the
repository's **own test suite** is the oracle: pass or fail, no partial credit,
no judge.

- **Standard set:** a function body is excised and the agent restores it, or it
  writes a small script from a description.
- **Replay** (#714): a real feature commit is undone, described in plain words,
  and rebuilt.
- **Hard** (#726): replay with some tests hidden, plus multi-commit rebuilds.
  After the trial the hidden tests come back as a re-grade.

| target repo | language | oracle | runs on |
|---|---|---|---|
| [gmail-archive](https://github.com/evanwtf/gmail-archive) | Python | `uv run pytest` | **any platform** |
| [monitor](https://github.com/evanwtf/monitor) | Swift | `swift test` | **macOS only** |

Results from the two repos are never pooled. `monitor` builds against AppKit,
so a Linux machine has no Swift row: that is a missing cell, not a zero.
**Python is the cross-platform spine**; a comparison across platforms must be
Python-only.

| criterion | status |
|---|---|
| **problem solving** | measured |
| **speed** | measured: wall seconds for the whole loop, not tokens/sec |
| **code quality** | **not measured.** The hidden-test re-grade on hard tasks (#726) is the nearest proxy |

Reliability matters more than any of the three, so pass rates carry confidence
intervals: a perfect 21/21 only establishes ">85%". The method, task by task,
is in [benchmarks/agent/METHODOLOGY.md](benchmarks/agent/METHODOLOGY.md); why
the suite has this shape is in [docs/benchmark-design.md](docs/benchmark-design.md).

**Not in scope:** interactive chat, vendor leaderboards, vision/RAG/embeddings,
and tokens/sec: prompt re-prefill dominates agent wall time (#14).

## Project layout

| path | what is there |
|---|---|
| `benchmarks/agent/` | the harness, its tasks and its own tests |
| `scripts/` | measurement, field-watching and machine tools |
| `hardware/<machine>/` | results, logs, `RECOMMENDATIONS.md` and `howto.md` for one machine |
| `docs/` | [changelog](docs/changelog.md), [history](docs/history.md), runbooks ([M5 Max](docs/m5max-runbook.md), [DGX Spark](docs/dgx-spark-runbook.md), [two-Spark cluster](docs/dgx-cluster-setup.md)), archive |
| `logs/sweeps/` | sweep archives; the same fact on any machine |
| `vault/` | retired shell drivers, kept as text; never run |
| `claude-ollama` | Claude Code against a local Ollama ([docs/ollama-claude-shim.md](docs/ollama-claude-shim.md)) |
| [`docs/node-exporter-cpufreq-deadlock-arm64.md`](docs/node-exporter-cpufreq-deadlock-arm64.md) | not a benchmark finding: node_exporter deadlocks on aarch64 with `cppc_cpufreq` |

Work is tracked as GitHub issues, and their priority labels give the order
(`uv run python scripts/make_next.py --platform macos` or `nvidia`).
[docs/changelog.md](docs/changelog.md) records what shipped and why (before
v1.0.0: [docs/history.md](docs/history.md)). [SOURCES.md](SOURCES.md) lists who
to watch in the field. [CONVENTIONS.md](CONVENTIONS.md) holds the data and
safety rules: read it before you delete weights or commit logs.

## License

[LICENSE](LICENSE).
