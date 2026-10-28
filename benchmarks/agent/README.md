# Agent benchmark — local models as coding-agent backends

Measures what matters for a coding agent: **does it finish the job, and how
long does it take?** The other benchmarks here measure the engine. This one
measures the whole loop: model, tool use, file editing, and the agent's
willingness to stop.

Why it is built this way, and every task kind in detail:
[`METHODOLOGY.md`](METHODOLOGY.md). The prompts as sent:
[`PROMPTS.md`](PROMPTS.md) (generated).

## How it works

Each trial:

1. Exports a real repository at a pinned commit into a **standalone directory**
   with `git archive`. The directory has no `.git` and no link to the source
   repo.
2. Removes the work the agent must do, then makes that state the new repo's
   *only* commit, so the answer is not in the checkout's history. An
   **excision** task hollows out one or more functions and keeps the
   signature and docstring. A **replay** task reverts a real feature commit.
   A **script** task starts from an empty directory.
3. Verifies the tests now **fail**. A task whose tests still pass proves
   nothing, and this check is not optional.
4. Runs one **agent client** against one backend, confined to that directory.
5. Runs the repository's own test suite. **The test suite is the oracle**:
   pass or fail, no rubric, no partial credit, no judge model.

The directory is destroyed afterwards. `run.py` refuses to start if the
source repo is dirty or off its pinned commit, and every row records
`source_repo_intact`. A `git worktree` is not used, because it let an agent
reach and modify the operator's checkout (METHODOLOGY §9).

## Running it

```sh
uv run python benchmarks/agent/preflight.py      # FIRST: what is already up?
uv run python benchmarks/agent/run.py --dry-run  # verify each task's control, run no agent
uv run python benchmarks/agent/run.py --backend <name> --trials 3
uv run python benchmarks/agent/run.py --backend <name> --replay --replay-hard --targets sandbox
```

Start the backend's server first. **Run the preflight every time.** A server
left running from an earlier session holds its weights, and these models
nearly fill unified memory. If the new one still fits, nothing fails: the
batch measures a contended machine and the numbers look plausible. See
[Preflight](../../README.md#preflight-check-what-is-already-running). The
root README also lists the main `run.py` flags; `--help` lists all of them.

Rows append to the machine's ledger, `hardware/<machine>/results.jsonl` (the
name comes from `scripts/hardware_id.py`). One file holds one machine, so a
second machine never mixes into a comparison (#20).

Two artifacts go outside this repo, because both carry repository content:
`--client-log` (default `~/bench-logs`) keeps the client's event stream, and
`--solutions` (default `~/bench-solutions`) keeps each trial's diff. The row
carries the patch and its SHA-256.

## Two axes: backend and client

`--client` selects the agent harness: `opencode` (the default and the primary
harness), `claude`, `codex`, `aider` or `unreal`. Name another client only
when the run is about that client. **No client is fastest on every backend**,
so a result belongs to a *pair*, not to either alone. Two clients named in
one run are interleaved per task, so server drift hits both equally.

Only `passed`, `wall_seconds` and `touched_tests` compare cleanly across
clients. Token and turn counts come from each client's own accounting: Codex
records `tool_items` and leaves `num_turns` empty.

## Raw data is committed

The ledgers are **tracked in git, on purpose**. A row costs minutes and a
matrix costs hours, so the data cannot be cheaply regenerated, and a new
analysis needs the old rows.

Every row carries its own environment capture (`env`): the harness commit,
client and engine versions, server command line, samplers, and target commit.
Old rows stay readable after the stack moves on. Model **digests** are
recorded, not just tags, because a tag can be re-pushed upstream.

Rows are never deleted. A row taken under wrong conditions is marked
`"excluded": true` with a reason, and `results.load()` and every report skip
it. `--dry-run` rows are skipped the same way.

## Reading a row

- `passed` — the oracle. Read a verdict through `results.verdict()`, never
  `row["passed"]`: a timeout has no `passed` key and is a failure.
- `wall_seconds` — end to end, including the agent's own tool calls.
- `num_turns` — round trips. Fewer is not automatically better, but a large
  number with a failure usually means thrashing.
- `touched_tests` — the cheat detector. A pass with this set is not a pass. It
  covers `tests/` (and SwiftPM's `Tests/`), a root `conftest.py` or pytest ini
  file, and the pytest section of `pyproject.toml`, `setup.cfg` or `tox.ini`,
  tracked or untracked. It compares against the trial's starting commit by sha,
  so an agent that commits its work cannot hide an edit.
- `control_fails_as_expected` — must be `true`, or the row means nothing.
- `hidden_passed` — on the harder replay tasks only: the held-out tests (#726).

**One trial is not a result.** Use `--trials 3` or more before you believe a
gap, and read [`docs/measurement-discipline.md`](../../docs/measurement-discipline.md)
before you quote one.

## Results

The tables, per machine: [`docs/results.md`](../../docs/results.md)
(regenerate with `splice_tables.py`). The picks, per machine:
[`RECOMMENDATIONS.md`](../../RECOMMENDATIONS.md).
