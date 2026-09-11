# Conventions

Standing rules for this repo. They exist because breaking them has cost
something before.

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

`provenance.log_path(..., machine_specific=...)` encodes exactly this, and
`test_sweep_logs_are_machine_independent.py` enforces it. #292 named
`logs/sweeps/` as a place two machines could collide; they do not -- sweep files
are slugged and timestamped, so no two share a path, and `.gitattributes` marks
`logs/**` `-merge`. So the sweeps stay shared (moving them would wrongly
attribute a machine-independent observation to one machine); only machine-
specific logs move out, which is why four hand-committed #228 benchmark logs
left `logs/sweeps/` for the Mac's directory.

## Keep the historical record honest

Logs, traces, and saved transcripts under `benchmarks/` are records of what
actually ran. When paths change, **do not** rewrite them to match the new
layout — that falsifies the record. Fix live scripts and documentation; leave
`.log`, `.trace`, and captured transcripts alone.

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
  merged within days. No long-running per-machine branch.
- **Per-machine files never share a path**, so they never conflict:
  `hardware/MacA/results.jsonl` and `hardware/Spark/results.jsonl` are different
  files. `results.foreign_hardware()` refuses to pool rows across machines.

**Do not `merge=union` the ledgers.** `exclude_rows.py` annotates a row in
place and `results.load()` does not de-duplicate, so a union merge of two
diverged checkouts restores the un-excluded copy of an archived row into every
pass rate. `.gitattributes` explains why results.jsonl is left to the default
3-way merge. After any merge that touched a ledger, re-run the archivers and
check for duplicate rows before trusting an aggregate.
