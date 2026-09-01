# ⚠️ OpenCode data before 2026-08-31 21:47 EDT is INVALID

**This file is the canonical explanation. Everything else links here.**

> **Any OpenCode result recorded before `2026-08-31T21:47:18-04:00` measures a
> harness bug, not OpenCode.** Do not quote it, pool it, or compare against it.
> The cutover is sharp: the last invalid trial started `2026-08-31T21:38:54`,
> the fix landed at `21:47:18` (`7356460`), and the first valid trial started
> `21:47:37`. Results from **any other client are unaffected.**

## The error, once

`opencode run` attaches to a **persistent server that holds its own working
directory**. Setting `cwd` on the child process does nothing. `run.py` set
`cwd=worktree` correctly for the entire history of the project, and the client
ignored it.

There was no error and no crash. The client read the prompt, reasoned normally,
solved the task, wrote a correct answer **into the launcher's directory**, and
exited 0. The oracle then looked in the worktree, found no file, and recorded a
model failure. That is why it went unnoticed for two weeks and across three
engines: the failure mode is silent and looks exactly like a weak model.

It was found by reading an *excluded* row's transcript, which named
`~/git/local-llm/benchmarks/agent/reverse.py`. The file was there, and it passed
all three oracle checks.

**The fix** is the `--dir` flag (`7356460`). `opencode_argv` now refuses to build
a command line without a worktree (`28b1da6`), because a silent default is what
made this expensive.

## What is in this directory

`results-opencode-pre-dir.jsonl` holds **130 trials**, 2026-08-17 to 2026-08-31,
across `ds4` (58), `ds4anthropic` (27), `qwen38fnq3` (18), `qwen38fnq3lms` (15),
`qwen36coding` (6) and `glm53ds4` (6).

**None of them measure OpenCode.** They are kept because they are accurate
records of what this harness did, and because `dirfix.py` reads them to show the
before/after split. They must never be pooled with live results.

## Why they are void

Two causes, both ours:

1. **A missing `--dir` (all 130).** `opencode run` attaches to a persistent
   server that holds its own working directory; setting `cwd` on the child
   process has no effect. `run.py` set `cwd=worktree` correctly the whole time
   and the client ignored it. The client read the prompt, solved the task, and
   wrote the answer into the launcher's directory — so the oracle found no file
   and recorded a model failure. Discovered from an *excluded* row whose
   transcript named `~/git/local-llm/benchmarks/agent/reverse.py`; the recovered
   file passed all three checks. Fixed in `7356460`.

2. **An undeclared provider model (the 6 `glm53ds4` rows).** `ds4/glm-5.3-flash`
   was not in `~/.config/opencode/opencode.json`, so the client exited in 0.6s.
   Six client crashes were recorded as six model failures. See #69.

## What replaced them

Re-measured 2026-08-31/09-01 under #67 — 108 trials, six backends:

| backend | archived | re-measured |
|---|---|---|
| ds4 | 4/14 | 15/15 |
| ds4anthropic | 11/26 | 18/18 |
| llama.cpp Q3 (`qwen38fnq3`) | 1/12 | 18/18 |
| LM Studio (`qwen38fnq3lms`) | 4/14 | 18/18 |
| GLM-5.3 (`glm53ds4`) | no valid measurement | 16/18 |
| `qwen36coding` | 0/1 | 18/18 |

The archived pass rates above already exclude `agent_error` rows via
`results.is_excluded()`. Counting those as model failures — which a hand-rolled
`r.get("excluded")` does — inflates them further.

## Rules for this file

- **Do not edit a row.** Rewriting a recorded measurement corrupted 30 rows
  once; the rule since is to annotate or relocate, never to rewrite.
- **Do not pool with `results.jsonl`.** Only `dirfix.py` reads both, and only to
  put them in separate columns.
- Regenerate the split with `uv run python benchmarks/agent/dirfix.py`.
- The move is done by `scripts/archive_pre_dir_rows.py`, which is idempotent and
  moves lines byte-identically.
