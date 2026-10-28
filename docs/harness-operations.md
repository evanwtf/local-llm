# Operating the harness

How to run the benchmark safely: models, servers, engines, the reference
repositories, and the results ledger. **Read this before you start a model
server, add or switch an engine, touch a target repository, or write to
`results.jsonl`.** Per-machine operations (server argv, thermals, ports) live in
[`m5max-runbook.md`](m5max-runbook.md) and
[`dgx-spark-runbook.md`](dgx-spark-runbook.md). The traps that void a
measurement live here.

The short forms live in [`../AGENTS.md`](../AGENTS.md).

---

## Before you say a model is not on disk, run the census

Each runtime keeps models in its own tree: `~/models` (GGUF), the mlx-serve and
Ollama trees, LM Studio, and the HF cache. `ls ~/models` sees only the first.
Run `uv run python benchmarks/agent/model_inventory.py` (preflight prints it
every run) or read [`model-locations.md`](model-locations.md).

## A download is not verified until the files are on disk

`hf download` takes filenames positionally. Two filenames after `--include`
make the first an ignored include pattern, and only the second file arrives:

```
UserWarning: Ignoring `--include` since filenames have being explicitly set.
```

One 171 GB request fetched one 86 GB file, with no error. **Fetch one file per
call, and check each file against its recorded byte size and SHA-256 before
you treat a download as done.** A job that printed a warning and kept going has
not told you it succeeded.

Where weights come from, and in what order, is in [`../AGENTS.md`](../AGENTS.md)
(*Operating the harness*) and [`../CONVENTIONS.md`](../CONVENTIONS.md). On the
M5 Max, a new weights directory needs its Time Machine exclusion before the
first file lands:
[Model weights stay out of Time Machine](../CONVENTIONS.md#model-weights-stay-out-of-time-machine-2026-09-06).

## The testing set is written down

[`../TESTING-SET.md`](../TESTING-SET.md) lists the axes (hardware, client,
engine, model) and the task set, and marks which backends have valid current
data. **Read it before you add a variable**, and update it in the same commit.
A test fails when a live backend in `tasks.toml` is missing from it.

## Retire a backend, do not delete it

**Set `retired = "<reason>"` on the backend in `tasks.toml`.** Keep the config
block: rows in `results.jsonl` reference it, and they are unexplainable without
the sampler, context length and deviations it records. `retired` removes a
backend from the default matrix. Naming it with `--backend` still runs it.

LM Studio is the model case (2026-09-01). Its runtime is llama.cpp, so on the
same GGUF it only adds a layer: 122 s median against llama.cpp's 90 s,
correctness identical, and no server identity on its rows (#78).

## OpenCode is the harness to measure

**Default to `--client opencode`.** Run Aider, Claude Code or Codex only when
the question is about that client (a defect, a parser fix, or a reference
ceiling), and state the reason when you do. The premise is in
[`../AGENTS.md`](../AGENTS.md): an open model on an open engine behind a
proprietary client fails with the vendor.

The client axis is measured enough to act on. On one server, `script-transform`
took 11.1 s under Aider, 39.5 s under OpenCode and 189.6 s under Claude Code,
and the cause was the client's own prompt, prefilled every turn. Aider is
cheaper but does less: 22/34 inside a repository against OpenCode's 91/93.
Historical multi-client rows stay valid.

- **A gap between a reference client and OpenCode is a defect to chase**, not a
  result to publish. Measure a new backend with OpenCode first.
- **A client that scores far below its reputation is a bug report** until the
  cause is known. Read a failing transcript before you publish such a number.
- **`opencode run` is headless and `external_directory` defaults to `ask`.**
  With nobody to ask, agents read, and once destructively edited, repositories
  outside the trial checkout. Always set `permission.external_directory`, and
  check `workspace_escapes` on every row.

### Where OpenCode works (#67, #1086)

**Assert the workspace; do not assume it.** A client with a daemon, a server,
or a session can hold its own directory, and `cwd` is a request, not a
contract. A missing `--dir` under OpenCode 1.x produced no error: the client
solved the task, wrote the answer into the server's directory, and exited 0.
**64 trials across three engines were published as evidence that an open
client was weak**, when they measured our own invocation (#67).

`run.py` builds the argv for the installed major and refuses to build it
without a worktree. `test_run.py` guards both forms.

```sh
# 1.x: run attaches to a persistent server, which works in ITS start directory
opencode run --dir "$WORKTREE" --model "$MODEL" --format json --auto "$PROMPT"
# 2.x: no --dir; the process cwd is the worktree
cd "$WORKTREE" && opencode run --standalone --model "$MODEL" --format json --auto "$PROMPT"
```

OpenCode 2.x specifics:

- **Install.** 2.x ships only on npm as `@opencode/cli`. The GitHub releases,
  the `opencode-ai` package and opencode.ai/install stop at 1.19.0, so the
  currency check reads npm. The client image installs the registry tarball
  after a sha512 check (`docker/opencode-client/install_opencode.py`).
- **The workspace is checked after each trial.**
  `opencode session export --standalone <id>` gives the session's
  `info.location.directory`. `run.py` excludes a row whose session ran anywhere
  else as a harness fault.
- **`--standalone` on every call**, `run` and `session export` alike. Without
  it the client starts a background `opencode serve --service` that outlives
  the trial, shares state between trials, and left later runs hanging.
- **The final `step_finish` is missing** from `--format json`
  (anomalyco/opencode#53207). The row takes its turn and token counts from the
  export and records `token_source`. TTFT still comes from the stream.
- **Tools changed.** `bash` is now `shell`, `task` is now `subagent`,
  `todowrite` is gone, and `question` is new. A headless run cancels
  `question` with an error and the agent continues; the tool self-test checks
  that it still does.
- **Model keys.** 2.x drops `temperature` and `reasoning` from a model entry.
  The output cap goes as `max_completion_tokens` from `limit.output`.

## Always measure the latest infrastructure

**Update every component before a batch, not after it.** A result measured on a
build that upstream replaced last week answers a question nobody asked.

```sh
uv run python benchmarks/agent/preflight.py    # servers, memory, versions, notifications
```

- **An engine behind its newest release makes preflight refuse** (#1049).
- **A client behind its release makes preflight warn** with the upgrade command
  (`opencode upgrade`, `claude update`, `codex update`). Preflight never
  upgrades a client itself, because that would move the version mid-batch.
- **A version change starts a new series.** Every row records its versions. Do
  not pool across an upgrade, and say in `RESULTS.md` which side of it a number
  came from.
- **Tag a build before you leave it**: `git tag benchmark-<pr>-<date>` on the
  worktree, so its rows stay reproducible. A squash-merged upstream PR does not
  leave its commits in mainline history.

## Record the client version; do not pin it (2026-09-04)

The operator's rule: **run the current version of every client, and recover
comparability from the row.** The M5 Max is a daily driver first, and a pin
would hold a developer's own tools back. Preflight logs each client's version,
logs a **SERIES BOUNDARY** line for any that moved since `client-versions.toml`
was written, and logs whether each client's self-update is on. Self-update is
deliberately **on** for all three clients.

A refusal on client version existed briefly on 2026-09-04 and blocked a
daily-driver update. **Do not restore it unless the operator asks.**

`results.write_row()` **excludes any row without `client_version`**. Excluded,
not refused: losing a 30-minute trial to a missing field is worse than storing
a row that never enters an aggregate. The generated tables name which client
version measured which rows through `client_caveat()`.

## Restart the model server between arms

**Restart the server between the arms of a comparison. Always.** On #77, both
arms were worst in their third trial and one declined steadily across a
90-minute session. Each trial was a fresh conversation, so the state outlived
the conversation. An A/B where one arm ran on a cold server and the other on a
warm one is not a comparison.

- **Prefer a restart between trials too** (`scripts/restart_between_trials.py`).
  It costs about 10 s of warm-up against a 30-minute trial.
- **Give each engine configuration its own KV cache directory.** A
  configuration that changes the KV format rejects the other's checkpoints, so
  one arm re-prefills and looks slower. The ds4 paths are in
  [`m5max-runbook.md`](m5max-runbook.md).
- **Wait for the restart with `wait_ready.py`** (below), not `/health`.

## Wait for a completion, never for /health

`/health` lies. A llama.cpp server answered `{"status":"ok"}` with HTTP 200
while every completion returned **503**, because an 84 GB model was still
loading. `curl -s .../health` also exits 0 on a 503 unless you pass `--fail`.

```sh
uv run python benchmarks/agent/wait_ready.py \
    --base-url http://127.0.0.1:8020 --model qwen3.8-flash-next-q3
```

It sends a one-token completion every 5 s for up to 300 s and exits 0 only when
one succeeds. It logs `/health` beside it: health ok with completion 503 means
"still loading"; both failing means "nothing is listening". **Probe with the
kind of request the benchmark sends.**

## Always start from a known-good reference repo

Before any run, each reference repository must be **clean and on the pinned
`base_commit`**. `run.py` refuses to start otherwise, validates every repo a
selected task uses, and records `source_repo_intact` on every row.

| repo | language | pinned branch | default commit | oracle |
|---|---|---|---|---|
| `~/git/gmail-archive` | Python | `local-llm-benchmark` | `56e55cc` | `uv run pytest -q` |
| `~/git/monitor` | Swift | `local-llm-benchmark` | `cbb85ca` | `swift test` |

Some tasks pin their own `base_commit` in `tasks.toml`. Check by hand before you
trust a result:

```sh
for r in ~/git/gmail-archive ~/git/monitor; do
  git -C "$r" status --porcelain; git -C "$r" log --oneline -1
done
```

Expect empty output and the expected commit, or stop and find out why.

**Both are pinned on their own branch, not `main`.** gmail-archive's
`origin/main` once ran 73 commits ahead of the pin, and a routine `git pull`
would have changed what every trial measures. The commit is still the pin:
`git archive <base_commit>` exports it.

On 2026-08-17 an agent left its sandbox and left the reference repo dirty on a
benchmark commit, and every later trial exported from that state. **If you find
it dirty, stash rather than discard**: the debris is evidence of what escaped.

## Verify the oracle before trusting a run

`--dry-run` checks that every excision still breaks the tests it should. Run it
after you touch `tasks.toml` or move a target commit. A task whose tests still
pass measures nothing.

## Adding a language means two things, and one of them is a parser

A task in `tasks.toml` may set its own `repo`, `base_commit` and
`test_command`; otherwise it inherits the file-level defaults. A new language
needs:

1. **A test command** that exits non-zero while the excision is in place.
2. **An excision module** with `excise` and `body_source`, registered in
   `EXCISERS` (`run.py`) by file extension.

**The parser is the dangerous half.** Python gets spans from `ast`, which parses
or raises. `swift_excise.py` matches braces: a scanner that stops at the `}` in
`let brace = "}"` cuts the wrong span and leaves a file that may still compile.
It skips strings, line comments and nested block comments, and tests pin those
cases.

- `body_source` must return **exactly** the span `excise` removes, or
  `restored_verbatim` never fires and the recall signal is silently lost.
- `exciser_for` **refuses** an unknown extension. A default parser would excise
  nothing and record a broken task as valid.
- `swift_excise.excise(path, symbol)` **writes the file.** Use `body_source()`
  to inspect a span; only `run.py`'s worktrees see `excise()`.

## Write results through `results.py`, never by hand

`benchmarks/agent/results.py` owns the schema for `results.jsonl`:

```python
import results
row = results.new_row(task=..., backend=..., client=..., trial=..., ...)
row["finished"] = results.now()      # ISO 8601 with offset
results.write_row(row, RESULTS)      # validates, stamps, appends
```

And to read, **the part that matters**:

```python
rows = results.usable(RESULTS)  # normalized, exclusions already dropped
rows = results.load(RESULTS)  # normalized, exclusions still marked
```

**Never filter exclusions with a hand-written `r.get("excluded")`.** Five keys
have meant "do not trust this row": `excluded`, `exclude_reason`,
`excluded_reason`, `contaminated`, `confound`. An analysis that checked one
counted fifteen bad rows as good and published percentages from them.
`results.is_excluded()` knows all five.

`error` is deliberately *not* an exclusion. A timeout is a real failure and
belongs in the pass rate.

**A script that rewrites a ledger** (a backfill, `exclude_rows.py`, an archiver)
holds `results.ledger_lock(path)` from its read to its write, and writes with
`results.replace_ledger(path, text)`. `write_row` takes the same lock, so a row
appended meanwhile waits instead of vanishing. `replace_ledger` writes a temp
file and `os.replace`s it, so a full disk or a kill leaves the old ledger whole.
Never `write_text` a ledger: it truncates first.

Rows from 2026-08-28 on are schema v2 and carry `schema_version`. Older v1 rows
are **not rewritten**; `load()` normalizes them in memory. A row that fails
validation is still written, stamped `schema_valid: false`, and logged at ERROR,
because a trial costs up to half an hour. `is_excluded()` keeps it out of every
aggregate. Count them with:

```sh
grep -c '"schema_valid": false' benchmarks/agent/results.jsonl
```

## Transcripts are on by default

`--client-log` defaults to `~/bench-logs`. A row records *that* a trial failed,
never *why*; the autocompact finding in #15 showed only in a transcript.
Transcripts stay outside the repo because they hold file contents the agent
read. Disable with `--no-client-log`.

## Never `pkill` `run.py`; restore through the harness

The harness *moves* each real reference checkout aside to `<name>-real`, puts
a benchmark export in its place, and restores it from `atexit`. A `pkill` skips
the restore, and the next run refuses with `base_commit 56e55cc not found`.
That refusal is the guard working. Restore with the harness's own function,
never with `mv`:

```sh
cd benchmarks/agent && uv run python -c "import run; print(run.restore_targets())"
```

It reads `~/.local-llm-bench-stash.json`, is idempotent, and clears the marker.
To stop a run early, send `SIGINT`, or `kill` without `-9`.

**Do not wait on `pgrep -f 'benchmarks/agent/run.py'`.** The waiter's own
command line matches, so the loop never exits. See
[A waiter must be able to end](automation-hazards.md#a-waiter-must-be-able-to-end).

## antirez is the sherpa: check his path before you design your own

**ds4 (DwarfStar) is the reference implementation for 128 GiB Apple Silicon.**
When a model he ships is under evaluation here, his current guidance is the
starting point. Before you design an experiment on such a model:

```sh
cd ~/git/ds4 && git fetch --all
git branch -a                       # preview branches carry unreleased models
git log --oneline main..upstream/<branch>
grep -oE "^\s+[a-z0-9-]+\)" download_model.sh   # supported layouts
```

- **ds4 is not a general GGUF loader.** It supports only the layouts in its
  own `download_model.sh`. A GGUF of the "same" model from elsewhere is a
  different artifact (`glm5-next` against `glm5next` in
  `general.architecture`), and #25 lost hours to one. On GLM-5.3-Flash the
  unsupported path took 76 s and 854 tokens for a question the ds4 path
  answered in 3.2 s and 47 tokens.
- **Re-check before you conclude a model does not work.** A model can go from
  unsupported to shipped in a day, on a preview branch first.
- **He force-pushes preview branches. Check ancestry, not the commit count**
  (`git merge-base --is-ancestor`), before you treat a rebuild as an increment.
  A rewritten tip once carried the same message under a different SHA.
- **Read the commits, not the branch activity.** Of one force-pushed batch,
  two commits mattered to us.

**preflight reports GitHub notifications** for `antirez/ds4`,
`ggml-org/llama.cpp` and this repo, mentions first. It drops `ci_activity` and
keeps *read* items: the one ds4 mention that mattered had already been marked
read.
