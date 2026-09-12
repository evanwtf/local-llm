# Working in this repo

> ⚠️ **OpenCode results before 2026-08-31 21:47 EDT are INVALID** — the client was never told which directory to work in. **Other clients are unaffected.** The ledger holds none of these rows now; do not quote them from older documents either: [what happened](docs/archive/results-opencode-pre-dir.md).

Instructions for coding agents. This file is the entry point: the cross-cutting
rules that touch ordinary work, and a map to everything else. The detail —
incident narratives, commands, tables, citations, and rationale — lives in the
topic docs, each with a reading trigger in the table at the end.
[`CONVENTIONS.md`](CONVENTIONS.md) holds the standing rules about data and
safety.

## What this project is answering

**Which model + engine + harness combination is best for running a coding agent
locally, judged on code quality, problem solving, and speed?** Three axes,
always reported together: a result that names a model without its engine and
harness is not reproducible and does not belong here — the ranking *inverts*
across backends, and the same weights can be the slowest option under one client
and among the fastest under another.

The use case is a fallback for when hosted inference is unavailable or
unaffordable: hand it a real repository, point it at a real failure, let it run
an implement-test-verify loop to a green suite. An open model on an open engine
driven by a proprietary client is not a fallback — it fails with the vendor — so
**OpenCode is the primary harness**, and Claude Code and Codex stay in the suite
only as reference points that establish a task's ceiling.

**Out of scope, and say so when a task drifts toward them:** interactive chat and
chatbot feel; vendor leaderboard scores; vision, RAG, embeddings, creative
writing; and raw tokens/sec, which is nearly irrelevant to agent wall time
because re-prefill and context handling dominate it.

This is a benchmark harness and a body of measurements — Python scripts and a few
shell wrappers run from a checkout, plus the `RESULTS.md` files they produce. It
measures a fixed set of machines; the numbers this repo publishes come from **a
MacBook Pro M5 Max, 128 GB, macOS 26**. Confirm you are on a managed machine
before comparing a result (`uv run python scripts/machines.py --check`), and
**name the machine in the third person** in every record — three agents read this
repo, one per machine, and "this machine" points somewhere different for each
(#302). Detail:
[`docs/agent-workflow.md`](docs/agent-workflow.md#confirm-your-machine-is-one-we-manage-before-you-compare-2026-09-11).

## Common commands

```sh
uv sync                                            # environment; then: uv run pre-commit install (wires the commit guard)
uv run python scripts/machine_state.py             # is the machine busy, and who says so
uv run python benchmarks/agent/preflight.py        # servers, memory ceiling, versions, notifications -- always before a batch
uv run python benchmarks/agent/run.py --backend <name> --client opencode --trials 3
uv run python benchmarks/agent/splice_tables.py    # regenerate RECOMMENDATIONS.md tables after new rows land
uv run python scripts/report.py --backend <name>   # summarize or compare cells, with #23's resolution rule
uv run python scripts/coherence_check.py           # temperature-0 sanity check before any measurement batch
uv run python benchmarks/agent/model_inventory.py  # census every runtime's model tree before saying a model is absent
uv run pytest -q                                   # the suite; read the exit code, never `| tail`
```

`uv` manages the environment (`requires-python = ">=3.11"`); engines and weights
live outside this checkout, found via `DS4_ROOT`. CI
(`.github/workflows/test.yml`) runs on push to `main` and on `pull_request`, on
the self-hosted Linux runner; `ruff`/`mypy` are not yet in CI (#154). **Do not
run `pytest` or `ruff` on this Mac while a benchmark holds the run lock** — a
suite run voids the measurement, and CI on another host covers a branch
meanwhile.

## Cross-cutting rules, always in force

Each rule is actionable on its own; the incident that earned it is in the linked
doc.

**Reporting a number** — [`docs/measurement-discipline.md`](docs/measurement-discipline.md)
- Give the absolute wall-clock seconds beside every ratio, and **never write "N× faster"** — write "took 53% of the time: 751 s against 1429 s".
- **One trial is not a result.** A 3-trial median carries ±28%, so two medians need about a 56% gap before the difference is real. Quote pass rates with a confidence interval.
- **Code quality is not measured** — write "passes the suite", never "writes better code" (#4).
- Never publish a `-dirty` number, and carry the versions when you quote one. Never call `logging.basicConfig`: use `provenance.configure()` inside `benchmarks/agent`, `logs.configure()` elsewhere.
- Cite engine source as `file:line at <sha>`, found by name and not by line — `ds4.c` moves daily across twelve worktrees.

**Writing it down** — [`docs/agent-workflow.md`](docs/agent-workflow.md)
- Every timestamp is ISO 8601 `YYYY-MM-DDTHH:MM:SS±hhmm`, America/New_York. **Parse before comparing**; never string-compare timestamps.
- American English spellings. Never rewrite a quotation, preserved evidence, or an archived snapshot.
- Use **absolute URLs** in issue and PR comments; a relative link 404s there.
- Post a **status update every 5 minutes** during any long run.

**Shell, subprocesses, and waiters** — [`docs/automation-hazards.md`](docs/automation-hazards.md)
- New code is **Python, not shell** (#235). A `.sh` may be edited only to fix a live defect in a script that is still running.
- **Never put backticks — or `$VAR`, or `$(...)` — in a `-m`/`--body` argument or an unquoted heredoc.** Use `-F -` with a quoted delimiter. If a job spawned a process, kill the **group** (`kill -TERM -<pgid>`), not the pid.
- Read the **exit status, not the tail**: `pytest -q | tail && git commit` commits on a red suite.
- No unbounded `tail -f` or `until` waiter: poll for the task's own `[exited with code N]` line **and** for the producer being gone, with a deadline.

**Operating the harness** — [`docs/harness-operations.md`](docs/harness-operations.md)
- Update every component before a batch (`preflight.py`), never after; a version change starts a new series.
- `opencode run` ignores the caller's working directory — **always pass `--dir`**. Measure OpenCode unless the run is specifically about another client.
- Start from a clean reference repo on the pinned commit; restart the server between arms; wait on a real completion, never on `/health`.

## The working loop

Five documents, each with one job. Keeping them in their lanes is what stops
this project turning into a pile of findings nobody can act on.

| document | holds |
|---|---|
| **GitHub issues** | every piece of work, one per issue, until closed |
| [`NEXT.md`](NEXT.md) | the agenda: what order to work in |
| `benchmarks/*/RESULTS.md` | the numbers, and how they were obtained (append-only) |
| [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) | the top 1–3 picks, and how to run them |
| [`docs/changelog.md`](docs/changelog.md) · [`docs/history.md`](docs/history.md) | what shipped and why (before v1.0.0: history, frozen) |
| `AGENTS.md`, `CONVENTIONS.md`, `METHODOLOGY.md` | lessons that outlive the task |

- **New work becomes an issue first** — not a note in `NEXT.md`, not a TODO. An issue is a public work log: what we did and measured, in the order it happened. Do not draft an upstream reply in it; address findings to the operator, and **never post to a repository outside `evanwtf` or `evandhoffman`**.
- **Branch per piece of work, then merge it yourself** (`<kind>/<issue>-<slug>`). No pull request and no waiting for approval: when the work is done and tests pass, `--ff-only` merge to `main`, push, and delete the branch.
- **Close the loop the same day.** Comment what was found — including what contradicts the issue's own premise — close the issue, and prune `NEXT.md` once the lesson has a permanent home.
- More than one agent works here. Read [`docs/peer_agents.md`](docs/peer_agents.md) before delegating or taking work from a peer; take identity from `LOCAL_LLM_AGENT` / `LOCAL_LLM_MODEL` / `LOCAL_LLM_EFFORT`, sign comments with a trailing agent line (`--opus`, `--deepseek`), and **never put a session URL or ID anywhere**. Check a peer every 20 minutes, and check what it is *doing*.

## Handling secrets and credentials

- **Never put a secret value in chat, tool output, documentation, an example, a log, a commit, or PR text**, and never ask anyone to paste one into chat. Inspect names, paths, and metadata; never display the value. Redact before output — redaction after output is too late.
- **This repo stores no secrets.** Local backends authenticate with non-secret local tokens in `tasks.toml` (`dsv4-local`, `ollama`), which are placeholders and safe to commit. Hosted reference arms read `ANTHROPIC_API_KEY` from the **environment**; `run.py` deliberately pops it for local runs (asserted in `test_run.py`), so a local trial cannot silently reach the hosted API.
- The real exposure vector is a **prompt capture**: `--dump-failures` (`fail-*.json`) and `--trace` logs contain the operator's `CLAUDE.md` and the contents of every file the agent read. They are gitignored — never commit one ([`CONVENTIONS.md`](CONVENTIONS.md), "Never commit a prompt capture").
- The authoritative store for machine-wide secrets is **1Password via the `op` CLI** (the operator's established choice); retrieve at runtime through it, and never hard-code a value or put one in a command argument or shell history. A contributor with a different secret manager may use it — do not require a specific vendor.
- `.gitignore` ignores `.env` and local credential files while keeping `.env.example` trackable. **Ignore rules do not protect a file already tracked, nor scrub history.** If you find an exposed secret, report only the path and the required follow-up (revoke or rotate) — do not display it, rewrite history, or rotate it as part of another task.

## Baseline policies

* Make minimal, focused changes; avoid broad refactors unless requested.
* Preserve existing architecture and patterns.
* Don't introduce new dependencies without justification.
* Update tests when behavior changes; update docs when user-visible
  behavior, configuration, or workflows change.

## Which document to read before which task

Each linked policy is mandatory within its stated scope. You do not need to read
every one on every task — read the row that matches what you are about to do.

| Before you… | Read |
|---|---|
| run, compare, or report a measurement | [`docs/measurement-discipline.md`](docs/measurement-discipline.md) |
| start a server or engine, touch a target repo, or write the ledger | [`docs/harness-operations.md`](docs/harness-operations.md) |
| write shell, a subprocess, a wait/monitor, or a `-m`/`--body` message | [`docs/automation-hazards.md`](docs/automation-hazards.md) |
| file issues, branch, manage a peer, or cut a release | [`docs/agent-workflow.md`](docs/agent-workflow.md) |
| delete or archive weights, commit a capture, or merge a data file | [`CONVENTIONS.md`](CONVENTIONS.md) |
| understand the benchmark task by task | [`benchmarks/agent/METHODOLOGY.md`](benchmarks/agent/METHODOLOGY.md) |
| run machine operations, thermals, or a cross-machine comparison | [`docs/m5max-runbook.md`](docs/m5max-runbook.md) |
| share the machine with another agent | [`docs/peer_agents.md`](docs/peer_agents.md) |
| watch the field (X, Hugging Face, upstream) | [`SOURCES.md`](SOURCES.md) |

**Maintaining these docs:** update the authoritative section in place; put a new
long explanation or incident narrative in the supporting doc that owns its
topic, not here; keep this file concise and within its word budget. `CLAUDE.md`
is a relative symlink to this file — keep the guidance tool-neutral.
