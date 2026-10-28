# Conventions

Standing rules for data and safety in this repo. Each one exists because
breaking it has cost something. **Read this before you delete or archive model
weights, commit a log or a capture, regenerate held-out text, or merge, union,
or branch a data file.** How to *work* (the loop, git, issues, dates, peers,
releases) is in [`docs/agent-workflow.md`](docs/agent-workflow.md); how to run
a measurement is in [`docs/harness-operations.md`](docs/harness-operations.md)
and [`docs/measurement-discipline.md`](docs/measurement-discipline.md).

## Model weights are an archive, not a working set

Do not propose to delete local model weights only because the current runtime
cannot load them, or because nobody uses them. Open weights may not stay
downloadable, a local copy has option value, and disk is not the scarce
resource here.

- **Fair game:** models superseded on the numbers that are easy to
  re-download, for example the DS4 quants that
  [`cleanup_models.sh`](hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/ds4/0731/cleanup_models.sh)
  prunes.
- **Not fair game:** anything hard or impossible to reacquire. Ask first.

The `gemma4:*-mlx-bf16` models (~77 GB) were kept under this rule when Ollama
could not run them; Ollama later gained an MLX backend.

## Do not download weights larger than 200% of the machine's memory (2026-09-17)

**Hard cap:** do not download or evaluate a checkpoint larger than **200% of
the target machine's system memory**. That is **256 GB** on the M5 Max and
**about 243 GiB** per DGX Spark (each reports 121.7 GiB). Read the size before
any pull: `hf models info <repo>` reports `used_storage`, and the model page
gives the safetensors total.

A model larger than memory runs only by streaming experts from SSD. **Under the
cap, streaming is worth it only for an excellent model that still runs well**;
one that barely works at 2 tok/s is a curiosity, not a backend. Over the cap, a
download fills the disk with weights nobody can use. Record a lead like that
(for example `nvidia/DeepSeek-V4.1-Flash-NVFP4`, ~492 GiB) on its issue with the
size; do not download it and do not queue a test.

The cap applies before a download. It is not a reason to delete weights already
on disk.

## Model weights stay out of Time Machine (2026-09-06)

This rule governs the M5 Max (Time Machine, `tmutil`). The Linux machines have
no Time Machine; apply the same reasoning to any backup that runs there. Do not
back up a large, re-downloadable weights tree that changes wholesale.

On the M5 Max, **every directory that holds weights or an engine's model cache
lives on the `Models` volume** (`/Volumes/Models`), with a symlink at the path
the engine expects. Time Machine excludes the whole volume and takes no local
snapshot of it. Move a new directory there **before** the first file lands in
it; the steps are in
[`docs/m5max-runbook.md`](docs/m5max-runbook.md#weights-live-on-the-models-volume-755).

```sh
ls -ld ~/.newengine                       # expect: ~/.newengine -> /Volumes/Models/newengine
tmutil isexcluded /Volumes/Models         # expect [Excluded]; verify, do not assume
uv run python scripts/tm_model_guard.py   # exit 1 names any model file Time Machine includes
```

**Why a volume, not a directory exclusion (#755).** A local snapshot covers the
whole Data volume and ignores exclusions, so it pins every downloaded file while
the snapshot lives. A 1,004 GiB delete freed almost nothing until
`sudo tmutil deletelocalsnapshots /` ran.

**A missing weights file is not in the backup.** That is the policy working, so
the recorded identity is the only route back: keep the file name, byte size and
SHA-256 in the results file, as
`hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/ds4/pr621-m5max/RESULTS.md`
does. A weights file with no recorded identity and no backup is gone for good.
Exclusion from Time Machine is not permission to prune.

## Never commit a prompt capture

`ds4-server --trace` and the shim's `--dump-failures` both write **full
prompts**: the operator's `CLAUDE.md` and every file the agent read.

`.gitignore` blocks `fail-*.json` only. **Nothing ignores a trace log**, so
write one outside the checkout and never stage it. Do not start a server with
`--trace` for routine work; one was left armed for a day before anyone noticed.
`run.py` keeps client transcripts and solution diffs outside the repo by
default (`~/bench-logs`, `~/bench-solutions`) for the same reason.

## Held-out text must be the tail

The perplexity slice is the **last** 300 KB of `promessi_sposi.txt`, because the
speed sweeps prompt from the **start** of the same file. If you regenerate it
with `head` instead of `tail`, every perplexity number is contaminated and
nothing raises an error.
[`run_bench.sh`](hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/ds4/0731/run_bench.sh)
regenerates it and documents the invariant.

## A log is filed by what it records, not by which machine ran it

- A **sweep** records the outside world on a day: what upstream shipped, what
  Hugging Face has, what X said. That fact is the same on every machine, so it
  goes in the shared `logs/sweeps/`. The machine slug in the filename says who
  ran the sweep; it does not make the finding a property of that machine.
- A **benchmark, preflight or build** log is a property of the machine that
  produced it, so it goes in `hardware/<id>/logs/`.

`provenance.log_path(..., machine_specific=...)` encodes this split, and
`test_committed_logs_all_name_their_machine` holds benchmark logs to a
`<script>-<slug>-<UTC>Z` name. Sweep files are slugged and timestamped, so two
machines never share a path, and `.gitattributes` marks `logs/**` `-merge`
(#292). A few early #228 benchmark logs sit in `logs/sweeps/` with a local-time
stamp and no slug. They stay where they were first committed (see the next
section).

## Keep the historical record honest

Never rewrite the record of what ran to look tidier.

**Never delete a result row.** A run with wrong conditions gets
`"excluded": true` and a reason, and `summarize.py` skips it.
(`results.is_excluded()` knows every exclusion key; never hand-roll the filter.
See [Write results through `results.py`](docs/harness-operations.md#write-results-through-resultspy-never-by-hand).)

**Do not rewrite logs, traces, or saved transcripts under `benchmarks/`** to
match a new layout. When paths change, fix live scripts and documentation, and
leave `.log`, `.trace` and transcripts alone.

**Correct prose; do not quietly rewrite it.** When new data refutes a claim,
correct it and say it was refuted. A superseded finding in a `RESULTS.md` stays
visible with a marker that says what replaced it. A spelling or style pass never
touches a quotation, preserved evidence, or an archived snapshot (see
[American English spellings](docs/agent-workflow.md#american-english-spellings-only-2026-09-05)).

## Engine roots are configurable, results are local

Benchmark scripts read `DS4_ROOT` for the engine and its weights (default
`~/git/ds4`) and write results in this repo. Keep that split when you add an
engine: binaries and weights stay where they are installed, numbers land here.
The full list is in [`docs/external-repos.md`](docs/external-repos.md).

## One main, machines are directories

**A machine is a field in the data, not a branch.** Every machine commits to
`main`, and its results, logs and notes live in `hardware/<id>/` (the name from
`scripts/hardware_id.py`, never typed) (#85, #292).

- **Harness changes land on `main` first.** A per-machine branch that lags
  `main` produces numbers from a stale harness: the Ryzen branch sat 21 commits
  behind, and its rows were not comparable until #292 merged it.
- **Short-lived topic branches only**, named by issue, merged within days.
- **Per-machine files never share a path**, so they never conflict.
  `results.foreign_hardware()` refuses to pool rows across machines.

**Before you delete a branch, ask what it is for.** For a feature branch, the
test is whether a merge changes anything:

```sh
git worktree add --detach /tmp/mt origin/main
git -C /tmp/mt merge --no-commit --no-ff origin/<branch>
git -C /tmp/mt diff --cached --shortstat origin/main   # empty => superseded
```

That test is wrong for a branch that is some machine's only write channel: such
a branch can carry rows found nowhere else and still look dead. Merge one like
#292 did, as a reviewed 3-way merge, never as a union (below).

### Never resolve a data file by taking the union of two row sets (2026-09-07)

`results.jsonl` is usually append-only, so "keep both sides and dedupe" looks
safe. It is not: on 2026-09-07 a union restored 90 rows that had been archived
on purpose.

**A removal and an absence look the same in a union.** Rows leave the ledger on
purpose: `scripts/archive_pre_dir_rows.py` moves OpenCode trials that predate
`--dir` into `docs/archive/`, because they measure the harness, not the model.
A branch that forked before the archive still carries them, and a union brings
every one back. `results.load()` does not de-duplicate, so the restored rows
enter every pass rate. `.gitattributes` says why `results.jsonl` uses the
default 3-way merge.

**Resolve it as `theirs` plus the rows that are genuinely new**, then re-run
every archiver before you commit:

```sh
uv run python scripts/archive_pre_dir_rows.py     # idempotent; says what it moved
uv run python scripts/validate_ledgers.py         # duplicates, archived rows, overlap, on every ledger
uv run python benchmarks/agent/splice_tables.py   # tables go stale the moment rows move
uv run pytest -q
```

**CI checks less than it seems.** The ledger tests guarded on
`HAS_LOCAL_RESULTS` look only at the runner's own hardware directory, so they
skip on every other machine (#218). CI also runs `scripts/validate_ledgers.py`
over every committed ledger (#394), which catches duplicates and restored
archive rows. It cannot know about a row that is missing on purpose, so a merge
rule for a data file must name what may be absent by design.
