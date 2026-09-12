# Conventions

Standing rules for data and safety in this repo. They exist because breaking
them has cost something before. **Read this before you delete or archive model
weights, commit a log or a capture, regenerate held-out text, or merge, union,
or branch a data file.** How to *work* — the loop, git, issues, dates, peers,
releases — is in [`docs/agent-workflow.md`](docs/agent-workflow.md); how to run
a measurement is in [`docs/harness-operations.md`](docs/harness-operations.md)
and [`docs/measurement-discipline.md`](docs/measurement-discipline.md).

## Model weights are an archive, not a working set

Do not propose deleting local model weights merely because the current runtime
cannot load them, or because they are unused.

Open weights may not stay freely downloadable. A high-fidelity local copy has
option value beyond present usefulness, and disk is not the scarce resource
here.

- **Fair game:** models superseded on the numbers that remain easy to
  re-download — for example the DS4 quants pruned by
  [`benchmarks/ds4/0731/cleanup_models.sh`](hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/ds4/0731/cleanup_models.sh).
- **Not fair game:** anything hard or impossible to reacquire. Ask first.

The `gemma4:*-mlx-bf16` models (~77 GB) were kept under this rule in August
2026, when Ollama could not run them at all. Ollama has since gained an MLX
backend, so they may now be usable — which is the argument for the rule, not
against it.

## Model weights stay out of Time Machine (2026-09-06)

Every directory holding GGUF weights must be excluded from Time Machine
**before** the first file lands in it:

```sh
tmutil addexclusion ~/models
tmutil addexclusion ~/git/ds4/gguf
tmutil isexcluded ~/models      # verify; do not assume
```

Both of those are excluded today. Check any new weights directory with
`isexcluded` rather than trusting that it inherited anything.

The reason is size, not secrecy: a single pair of DeepSeek-V4-Flash arms is
171 GB, it changes wholesale rather than incrementally, and it is re-downloadable
from Hugging Face by name and SHA-256. Backing it up buys nothing and evicts
things that are not re-downloadable.

**The exclusion is why a missing weights file will not be in the backup.** On
2026-09-06 the AProjQ4 and AProjQ8 files behind #91's published 1.155 were gone
from this machine, and the Extreme SSD snapshot taken the same morning did not
have them either. That is the policy working, not a backup failure -- but it
means the recorded identity is the only route back. Keep the file name, byte
size and SHA-256 in the results file, as
`hardware/.../pr621-m5max/RESULTS.md:12-13` does. A weights file with no
recorded identity and no backup is gone for good.

Related: weights are archived, not deleted, when a runtime stops being able to
load them (above). Excluding them from Time Machine is not permission to prune
them.

## Never commit a prompt capture

`ds4-server --trace` and the shim's `--dump-failures` both write **full
prompts**. That means the operator's `CLAUDE.md` and the contents of every file
the agent read.

`.gitignore` blocks `*_trace.log`, `server*.log` under the agent directories,
and `fail-*.json`. Do not start a server with `--trace` for routine work; it was
once left armed for a day before anyone noticed.

`benchmarks/ds4/coding/gen_mixed.log` and `gen_mxfp4.log` are ignored on purpose
— they hold full model output.

## Held-out text must be the tail

The perplexity slice is the **last** 300 KB of `promessi_sposi.txt`, because the
speed sweeps prompt from the **start** of the same file. Regenerating it with
`head` instead of `tail` silently contaminates every perplexity number, with no
error raised.

`benchmarks/ds4/0731/run_bench.sh` regenerates it and documents the invariant.

## A log is filed by what it records, not by which machine ran it

`logs/sweeps/` is shared; `hardware/<id>/logs/` is per-machine. The split is by
the log's subject, not its author:

- A **sweep** records the outside world on a day -- what upstream shipped, what
  Hugging Face has, what X said. That is the same fact on every machine, so it
  stays in the shared `logs/sweeps/`. The machine slug in the filename says who
  ran the sweep; it does not make the finding a property of that machine.
- A **benchmark, preflight or build** log is a property of the machine that
  produced it, so it goes in `hardware/<id>/logs/`.

`provenance.log_path(..., machine_specific=...)` encodes exactly this: a new
sweep log lands in `logs/sweeps/`, a new benchmark log in `hardware/<id>/logs/`,
and `test_committed_logs_all_name_their_machine` holds the latter to a
`<script>-<slug>-<UTC>Z` name.

#292 named `logs/sweeps/` as a place two machines could collide. They do not:
sweep files are slugged and timestamped, so no two share a path, and
`.gitattributes` marks `logs/**` `-merge`. So the sweeps stay shared -- moving
them would wrongly attribute a machine-independent observation to one machine.
A handful of early #228 benchmark logs were committed to `logs/sweeps/` by hand,
before that routing existed; they carry a local-time stamp and no slug, so they
fit neither naming rule. They stay where they were first committed rather than
being renamed to conform -- "keep the historical record honest", below, wins.

## Keep the historical record honest

The record of what actually ran is never rewritten to look tidier. This has
three faces, and all three are the same rule.

**Never delete a result row.** A run whose conditions were wrong gets
`"excluded": true` with a reason, and `summarize.py` skips it. Deleting it would
falsify the record. (`results.is_excluded()` knows every exclusion key; never
hand-roll the filter — see
[Write results through `results.py`](docs/harness-operations.md#write-results-through-resultspy-never-by-hand).)

**Do not rewrite logs, traces, and saved transcripts under `benchmarks/`** to
match a new layout. When paths change, fix live scripts and documentation and
leave `.log`, `.trace`, and captured transcripts alone. Rewriting them to match
the new paths falsifies the record of what ran.

**Correct prose; do not quietly rewrite it.** When new data refutes an earlier
claim, correct the claim and say it was refuted. A superseded finding in a
`RESULTS.md` stays visible with a marker saying what replaced it —
corrections are added, not substituted. This is also why a spelling or style
pass must never touch a quotation, preserved evidence, or an archived snapshot
(see [American English spellings](docs/agent-workflow.md#american-english-spellings-only-2026-09-05)).

## Engine roots are configurable, results are local

Benchmark scripts read `DS4_ROOT` for the engine and its weights (default
`/Users/evanhoffman/git/ds4`) and write results beside themselves in this repo.
Keep that split when adding an engine: binaries and weights stay where they are
installed, numbers land here.

## One main, machines are directories

**A machine is a field in the data, not a branch.** Every machine commits to
`main`; its results, logs and notes live in `hardware/<id>/` (the name from
`scripts/hardware_id.py`, never typed). Three machines on one branch is the
design working — one shared apparatus, many directories (#85, #292).

- **Harness changes land on `main` first, always.** A per-machine branch that
  lags `main` produces numbers from a stale harness: the Ryzen branch sat 21
  commits behind and its rows were not comparable to the laptop's without a
  merge first (#292).
- **Short-lived topic branches only**, named by issue (`269-ternary-bonsai`),
  merged within days. No standing long-running per-machine branch: the one
  machine that wrote only to its own branch (the Ryzen box) was merged into
  `main` by #292 — see the historical note below.
- **Per-machine files never share a path**, so they never conflict:
  `hardware/MacA/results.jsonl` and `hardware/Spark/results.jsonl` are different
  files. `results.foreign_hardware()` refuses to pool rows across machines.

### Historical: the Ryzen machine-branch, kept on 2026-09-07 and merged by #292

**Superseded — kept here for the lesson, not as current policy.** For a while
the Ryzen box (`Ryzen9-7900X-32GB-RTX3080Ti-12GB`) wrote only to a branch named
after it, because it had **no coordination channel** to this laptop: it could
not be told main had moved and could not be asked to rebase, so on 2026-09-07
that branch was deliberately **kept** while eighteen other stale branches were
deleted the same day. The reason it was kept is the durable lesson.

**Before deleting any branch, ask what kind it is.** The test that settles a
*feature* branch is whether merging it changes anything:

```sh
git worktree add --detach /tmp/mt origin/main
git -C /tmp/mt merge --no-commit --no-ff origin/<branch>
git -C /tmp/mt diff --cached --shortstat origin/main   # empty => superseded
```

That test is right for code and **wrong for a machine branch**, which fails it
exactly the way a dead branch does. A machine's sole write channel is not a dead
branch even when the graph says it is; the only thing that separated this branch
from the eighteen correctly deleted was asking what it was *for*. At that point
the branch carried ~96 rows across seven backends -- `dtgemma412b`,
`dtornith159b`, `dtqwen359b`, `dtmistralnemo`, `dtqwen359bq8`, `dtgemma4e4b`,
`dtbonsai27b` -- not yet on `main`, plus its `RESULTS.md` and `hardware-id-*`
logs, none of it re-derivable here.

**#292 then closed it properly.** The branch was **merged into `main` in
`04c63ee`** ("Merge the Ryzen9-... branch into main"): its rows are on `main`
(`hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/results.jsonl` now carries 224),
`docs/results.md` was re-spliced with the desktop rollup (#137), and two
branch-only harness fixes that would have died with the branch were recovered --
`run.py` `tasks_missing_targets` (#269, drops a task whose target repo is absent
on a machine) and a `summarize.py` no-arg `logger.info` crash. The full suite
passed (2841). So the deletion blocker is **cleared**: the branch may now be
deleted, its data is no longer branch-only, and the Spark records its rows
directly on `main` with no branch at all.

**Current policy is therefore the one-main rule above, with no standing
per-machine-branch exception.** Merging a machine branch is a deliberate,
reviewed 3-way merge like #292 -- never a `merge=union` (below), which would
restore archived rows.

### Never resolve a data file by taking the union of two row sets (2026-09-07)

`results.jsonl` is append-only in the ordinary case, so "keep both sides and
dedupe" looks like the safe merge. It is not, and on 2026-09-07 it restored 90
rows that had been deliberately archived months before.

**A removal and an absence are the same shape in a union.** Rows leave that
file on purpose: `scripts/archive_pre_dir_rows.py` moves OpenCode trials that
predate `--dir` into `docs/archive/`, because the client was never told which
directory to work in and those rows measure the harness rather than the model.
A branch that forked before that archiving still carries them. Union the two
sides and every archived row comes back, indistinguishable from a row the
other side simply had not seen yet.

`exclude_rows.py` annotates a row in place and `results.load()` does not
de-duplicate, so a union merge of two diverged checkouts restores the
un-excluded copy of an archived row into every pass rate. `.gitattributes`
explains why `results.jsonl` is left to the default 3-way merge rather than a
union.

**So resolve it as `theirs` plus the rows that are genuinely new**, and then
re-run every archiver the repo owns before committing:

```sh
uv run python scripts/archive_pre_dir_rows.py     # idempotent; says what it moved
uv run python benchmarks/agent/splice_tables.py   # tables go stale the moment rows move
uv run pytest -q                                  # the invariant tests are the check
```

After any merge that touched a ledger, re-run the archivers and check for
duplicate rows before trusting an aggregate.

**And do not trust CI to catch it.** The tests that assert invariants of the
ledger are guarded on `HAS_LOCAL_RESULTS`, which is
`results.default_path().exists()` — a path derived from the RUNNER's own
hardware by `scripts/hardware_id.py`. On any machine that is not the one that
took the measurements, the directory does not exist and every one of those
tests skips. CI was green on the branch that carried the 90 rows. See #218.

The instruction that caused this was mine, given to a peer, and the peer
followed it exactly and verified the union carefully. A merge rule for a data
file has to name what may be *missing on purpose*, or it is not a rule.
