# Working in this repo

Instructions for coding agents. [`CONVENTIONS.md`](CONVENTIONS.md) holds the
standing rules about data and safety; this file covers how to work.

## What this project is answering

**Which model + engine + harness combination is best for running a coding agent
locally, judged on code quality, problem solving, and speed?**

Three axes, always reported together. A result that names a model without its
engine and harness is not reproducible and does not belong here — the ranking
*inverts* across backends, and the same weights can be the slowest option under
one client and among the fastest under another.

The use case is a fallback for when hosted inference is unavailable: hand it a
real repository, point it at a real failure, let it run an implement-test-verify
loop to a green suite.

**Out of scope, and say so when a task drifts toward them:** interactive chat and
chatbot feel; vendor leaderboard scores; vision, RAG, embeddings, creative
writing; and raw tokens/sec, which is nearly irrelevant to agent wall time
because re-prefill and context handling dominate it.

### Do not overstate what is measured

Of the three criteria, **problem solving** and **speed** are measured. **Code
quality is not** — the tasks are easy enough that nearly every backend passes, so
the suite cannot distinguish good code from code that merely passes (issue #4).
Write "passes the suite", never "writes better code", until that changes.

**Reliability outranks all three in practice.** Most backend×harness pairs fail a
meaningful share of these easy tasks, and a pair that fails one in five is
unusable however fast it is. Quote pass rates with their confidence interval: a
perfect 21/21 only establishes ">85%", and on current sample sizes most
combinations cannot be told apart (issue #23).

## The working loop

Five documents, each with one job. Keeping them in their lanes is what stops
this project turning into a pile of findings nobody can act on.

| document | holds | lifetime |
|---|---|---|
| **GitHub issues** | every piece of work, one per issue | until closed |
| [`NEXT.md`](NEXT.md) | the agenda: what order to work in | rewritten constantly |
| `benchmarks/*/RESULTS.md` | the numbers, and how they were obtained | append-only |
| [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) | the top 1-3 picks, and how to run them | replaced as evidence changes |
| `AGENTS.md`, `CONVENTIONS.md`, `METHODOLOGY.md` | lessons that outlive the task | permanent |

**New work becomes an issue first.** Not a note in `NEXT.md`, not a TODO in a
comment. An issue carries its own reasoning and can be argued with; `NEXT.md`
only says what to do next, and it says it by number.

**`NEXT.md` sets order and nothing else.** Each issue must stand on its own, so
this file never restates one. It carries the ordered table, the machine state
that is not in git, and the traps.

**Close the loop the same day you finish.** When a task lands:

1. **Comment on the issue** with what was found -- including the parts that
   contradict the issue's own premise. #26 was opened blaming a KV cache and the
   data refuted it; #4 named a blocker that turned out never to have existed.
   Write that down. An issue closed with "done" teaches nobody.
2. **Close it.** A finished issue left open makes the agenda lie.
3. **Prune `NEXT.md`.** Reorder the table, and move each finished item out of
   "Done since the last update" **once its lesson has a permanent home.** That
   is the release condition -- not age. A finding still only recorded in
   `NEXT.md` has not landed anywhere yet.

**Two parts of `NEXT.md` are exempt from pruning.** "Traps worth not
rediscovering" and "Machine state" are the reason the file is worth reading, and
they grow rather than shrink. A trap leaves only when it becomes impossible --
fixed in code, or pinned by a test that would go red first. Prefer that to
prose: an entry that can be made mechanical should be.

**Results go to the `RESULTS.md` for the area that produced them** --
`benchmarks/agent/`, `benchmarks/llamacpp/`, `benchmarks/ollama/`,
`benchmarks/ds4/coding/`. Raw rows live in `results.jsonl` and are the record;
`RESULTS.md` is the narrative over them, layered by date, and **corrections are
added rather than substituted.** A superseded finding stays visible with a
marker saying what replaced it. See "Keep the historical record honest".

**`RECOMMENDATIONS.md` answers one question: what should someone run today?**
The table at the top holds **one to three pairings** -- a model *and* a client,
never a model alone -- with the commands to start each. Everything below it is
support for that table: why a backend was ruled out, what a correction changed.
When new evidence moves the top table, say what it used to say and why it moved.
It has been wrong twice, and both times the old claim is more useful visible
than deleted.

## Post a status update every 5 minutes during long runs

Benchmark runs here take hours. A silent agent is indistinguishable from a
stalled one, so **report every 5 minutes** while anything long is running — a
matrix, a model download, a build.

Each update states:

- what finished since the last update, with the numbers;
- what is running now;
- the revised estimate to completion.

Say so plainly when nothing has changed. "Still on trial 7, no results yet" is a
valid update and is better than silence. Do not drop the cadence because the run
looks boring; that is when a stall hides longest.

This rule exists because the cadence has been dropped mid-run before, and the
operator had to ask where the updates went.

## One trial is not a result

These models are sampled, not deterministic, and the wall-time distribution has
a fat right tail. Do not state a finding from a single trial. Two claims in
`RESULTS.md` were made this way and both were later refuted by more data; the
failed attempts are recorded there on purpose.

Use medians, not means. Run at least 3 trials before believing a gap, and treat
a few seconds of difference as noise.

## Keep the historical record honest

Never delete a result row. A run whose conditions were wrong gets
`"excluded": true` with a reason, and `summarize.py` skips it. Deleting it would
falsify the record.

The same applies to prose: when new data refutes an earlier claim, correct the
claim and say it was refuted. Do not quietly rewrite it.

## Name the confounds

Every backend added here changes more than one variable at a time. Write the
caveat into the backend block in `tasks.toml` at the moment you add it, not
afterwards — engine, quant, tune, and default sampler settings all move
together, and a result that cannot attribute its cause must say so.

## Observe the wire call, not the status code

When two components can talk over more than one protocol, check which one they
actually used before drawing a comparison from the result. A 200 from both
endpoints means both work; it does not mean the run you are comparing against
used the same one.

This cost a 13-trial run on 2026-08-17. OpenCode was pointed at ds4-server's
OpenAI-compatible path while the Claude Code baseline used the Anthropic path,
so client and protocol varied together and the +60% gap could not be
attributed. Both endpoints had been curl-tested first; both returned 200; the
choice was never registered as a choice.

Copying a working config from another tool's `connect` output is how it
happened. A template answers "will this run", not "does this match the thing I
am comparing against".

## Always start from a known-good reference repo

Before any run, the reference repository must be **clean and on the pinned
`base_commit`**. `run.py` now refuses to start otherwise, and records
`source_repo_intact` on every row.

Check it by hand too, whenever you are about to trust a result:

```sh
cd ~/git/gmail-archive && git status --porcelain && git log --oneline -1
```

Empty output and the expected commit, or stop and find out why.

This exists because on 2026-08-17 an agent left its sandbox, ran a checkout in
the reference repo, and left it on a benchmark commit with agent edits in the
working tree. Every trial after that exported its checkout from contaminated
state, and nothing noticed for a whole run. A benchmark that starts from an
unknown state measures nothing.

If you find it dirty: stash rather than discard — the debris is evidence about
what escaped, and it is worth reading before it is thrown away.

## Verify the oracle before trusting a run

`--dry-run` checks that every excision still breaks the tests it should. Run it
after touching `tasks.toml` or bumping the target commit. A task whose tests
still pass measures nothing, and the control check is the only thing that
catches it.

## Write results through `results.py`, never by hand

`benchmarks/agent/results.py` owns the schema for `results.jsonl`. Use it:

```python
import results
row = results.new_row(task=..., backend=..., client=..., trial=..., ...)
row["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
results.write_row(row, RESULTS)      # validates, stamps, appends
```

And to read — **this is the part that matters**:

```python
rows = results.usable(RESULTS)   # normalised, exclusions already dropped
rows = results.load(RESULTS)     # normalised, exclusions still marked
```

**Never filter exclusions with a hand-written `r.get("excluded")`.** Four
different keys have meant "do not trust this row" — `excluded`, `exclude_reason`,
`excluded_reason`, `contaminated`, `confound`. An analysis that checked only the
first silently counted fifteen bad rows as good data, and published percentages
from them. `results.is_excluded()` knows all five; a hand-rolled filter knows
whichever one you happened to remember.

`error` is deliberately *not* an exclusion. A timeout is a real outcome — the
trial genuinely failed and belongs in the pass rate.

Rows written from 2026-08-28 are schema v2 and carry `schema_version`. Older
rows are v1 and are **not rewritten**: the file is append-only evidence.
`load()` normalises them in memory instead.

A row that fails validation is still written, stamped `schema_valid: false` with
the violations, and logged at ERROR. A trial costs up to half an hour; losing one
to a schema bug is worse than storing a flagged row. Check for them with:

```sh
grep -c '"schema_valid": false' benchmarks/agent/results.jsonl
```

## Transcripts are on by default

`--client-log` defaults to `~/bench-logs`. A results row records *that* a trial
failed, never *why*; the autocompact finding in #15 was only visible in a
transcript. Transcripts stay outside the repo because they carry file contents
the agent read, and this repo does not commit prompts. Disable with
`--no-client-log`.
