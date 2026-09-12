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
  merged within days. No long-running per-machine branch **as a substitute for
  committing to `main`** — see the exception below, which is a different thing.
- **Per-machine files never share a path**, so they never conflict:
  `hardware/MacA/results.jsonl` and `hardware/Spark/results.jsonl` are different
  files. `results.foreign_hardware()` refuses to pool rows across machines.

### The exception: a machine with no coordination channel keeps a permanent branch (2026-09-07)

The rule above assumes a machine that can rebase onto `main`. One cannot.
**`Ryzen9-7900X-32GB-RTX3080Ti-12GB` must never be deleted, rebased, or
force-pushed.** It is not a feature branch, not a staging area, and not a
merge waiting to happen. It is the only place a second machine can write.

The Ryzen box runs on its own, and **there is no coordination channel between
it and this laptop.** It cannot be told that main moved, cannot be asked to
rebase, and will not notice anything done to its branch here. Its branch is
the whole interface. Delete it and that machine has nowhere to push -- which
is worse than losing rows, because it breaks every future run rather than
losing a past one.

`346 behind` is its **normal steady state, not drift to correct.** It is
behind because main advances on this machine many times a day, and ahead
because the other machine writes rows nobody has merged. Both numbers will
grow forever. Neither is a problem and neither is a task.

So the two rules do not conflict: "no long-running per-machine branch" forbids
holding a machine's commits *off* `main` when it could merge them; it does not
license deleting the one branch a machine with no other channel writes through.

What is on it that is nowhere else, as of 2026-09-07:

```
hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/results.jsonl
  on origin/main   16 rows
  on the branch   112 rows
```

96 rows across seven backends -- `dtgemma412b`, `dtornith159b`, `dtqwen359b`,
`dtmistralnemo`, `dtqwen359bq8`, `dtgemma4e4b`, `dtbonsai27b` -- measured
2026-09-02 to 2026-09-03, plus that tier's `RESULTS.md` and its
`hardware-id-*` logs. None of it can be re-derived here at any price. The
machine is a different machine.

**Before deleting any branch, ask what kind it is.** The test that settles a
feature branch is whether merging it changes anything:

```sh
git worktree add --detach /tmp/mt origin/main
git -C /tmp/mt merge --no-commit --no-ff origin/<branch>
git -C /tmp/mt diff --cached --shortstat origin/main   # empty => superseded
```

That test is right for code and **wrong for a machine branch**, which fails it
exactly the way a dead branch does. Eighteen branches were deleted on
2026-09-07 on the strength of it and every deletion was correct; this one was
kept, and the only thing that separated it from them was asking what the
branch was for instead of what its graph looked like.

The rule: **a branch whose name is a machine name belongs to that machine.
Leave it alone.** Do not delete it, do not rebase it, do not force-push it,
and do not "tidy" it because it has fallen behind.

Copying its rows into main is a separate question and still open. If it is
ever done it is an append argued for under the union rule below -- never a
`git merge`, and never anything that touches the branch itself.

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
