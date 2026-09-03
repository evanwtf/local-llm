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
