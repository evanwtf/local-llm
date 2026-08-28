# Methodology

Why this benchmark is built the way it is, what it can and cannot tell you, and
how to extend it without breaking the parts that make it trustworthy.

Read [`README.md`](README.md) first for how to run it. This document is about
*why*.

---

## 1. What question is this answering?

**"If I point Claude Code at this local model, will it finish a real coding
task, and how long will I wait?"**

That is deliberately narrower than "which model is better". A coding agent is a
loop — read files, call tools, edit, re-read, decide it is done — and a model
can be strong at code while being bad at that loop: not stopping, not reading
before editing, forgetting what it changed.

The other benchmarks in this repo measure the engine:

| benchmark | measures |
|---|---|
| `benchmarks/ds4/0731/` | prefill and generation throughput, thermals, long context |
| `benchmarks/ollama/` | the same, for Ollama's MLX backend |
| `benchmarks/ds4/coding/` | HumanEval pass@1 — single-shot code generation |
| **`benchmarks/agent/`** | **the whole agent loop, end to end** |

HumanEval saturated at 96–98% for both DS4 quants and could not rank them
(p = 0.453). That is the specific gap this fills: HumanEval asks "can it write a
function"; this asks "can it operate a repository".

---

## 2. The oracle problem

Every agent benchmark faces the same question: **who decides if the work is
good?**

The usual answers are all bad:

- **A judging model.** Introduces the grader's own bias, and if the grader is
  the same family as the subject, it grades its own homework.
- **A rubric scored by hand.** Does not scale and is not reproducible.
- **String or diff matching against the original code.** Punishes correct
  solutions that differ from the original, which is most of them.

This harness uses **the repository's own test suite**. The tests were written
by a human, before the benchmark existed, for reasons unrelated to it. They
encode the actual contract. The agent either satisfies it or does not.

That gives a binary, reproducible, un-gameable-by-argument signal. The cost is
that it is coarse: there is no partial credit, and a solution that is correct
but slow, ugly, or insecure still passes. **This measures completion, not
craftsmanship.**

### Requirements this places on the target repository

1. **Tests must be fast.** They run twice per trial (control + verification).
   `gmail-archive` runs 166 tests in ~4.7 s. A suite that takes minutes would
   dominate the measurement.
2. **Tests must be deterministic.** A flaky test becomes a coin flip attributed
   to the model.
3. **Tests must not be skipped.** See §4.

---

## 3. Why excision, and why keep the signature

Each task removes one function body and replaces it with
`raise NotImplementedError`, **keeping the signature, type annotations, and
docstring.**

This mirrors the real situation. An engineer picking up a ticket has the
contract — the name, the types, the docstring, the callers, and the tests — and
has to supply the implementation. They are not guessing at an unknown API.

Removing the docstring too would make the task "infer the contract from the
tests", which is a different and much noisier skill. Removing the signature
would break the callers and turn a focused task into a repo-wide refactor.

The excision is done with the **AST** (`excise.py`), not with regexes or line
numbers, so it works on methods, nested functions and decorated functions, and
is indifferent to formatting.

### Why the excision is committed

After hollowing out the function, the harness commits it in the worktree.

Without that, the original implementation sits in `HEAD` and the whole task
collapses to `git checkout -- src/...` or `git stash pop`. Agents do try things
like this — not maliciously, but because inspecting git history is a reasonable
debugging move. Committing the excision makes the answer genuinely absent from
the working tree's history.

The original code is still recoverable from the base commit's parent if an agent
goes looking hard enough. That is an accepted, documented residual risk: a
`git log -p` deep enough to find it is itself real work, and `touched_tests`
plus manual inspection of surprising passes is the backstop.

---

## 4. The control run, and the task it killed

**Before the agent runs, the harness runs the tests and requires them to fail.**

If the tests still pass after the excision, the task is measuring nothing — the
model gets a free pass regardless of what it does. `run.py` records this as
`control_fails_as_expected` and `summarize.py` discards any row where it is
false.

This is not hypothetical. The first task list included `query.search`, a
221-line SQL surface that looked like the most interesting task of the five. The
first dry run reported:

```
ERROR query-search-ds4-1: tests still pass after excision -- task is broken
```

`tests/test_query.py` skips every test without
`GMAIL_ARCHIVE_TEST_DATABASE_URL`. All 8 tests were skipping, so deleting the
function it tested changed nothing observable. **That task would have reported
100% pass rates for both models while measuring nothing at all.**

It was replaced with `mbox-scan`. To use the SQL surface as a task, bring up the
compose stack and export the database URL first — then the control will pass and
the task becomes valid.

The general lesson: *a benchmark that cannot fail is not measuring anything.*
Verify the negative case.

---

## 5. Isolation

Each trial runs in its **own git worktree**, created from a pinned base commit
and destroyed afterwards.

This matters more than it looks:

- **Trials cannot contaminate each other.** An agent that leaves a stray file,
  a half-applied edit, or a `.pyc` cannot affect the next run.
- **Task order does not matter.** Runs are independent, so they can be
  reordered, re-run, or parallelised later without invalidating comparisons.
- **The source repository is never modified.** The benchmark reads
  `~/git/gmail-archive` and writes nothing to it.

`base_commit` is pinned in `tasks.toml`. Results stay comparable as the target
repository moves on; bumping it invalidates comparisons against older results
and should be treated as starting a new series.

---

## 6. Fairness between backends

### Context windows are deliberately different

ds4 runs at 100,000 tokens; Qwen3.8 at 262,144.

Equalising them would be the wrong kind of fair. Capping Qwen at 100k throws
away a real capability; raising ds4 to 262k exceeds what `ds4-server` was
started with, and Claude Code would then auto-compact *after* the server had
already truncated — producing silent corruption rather than a fair fight.

Each model gets its real window. The window is part of the product.

### Memory pressure is controlled, not ignored

ds4 is ~91 GiB resident and Qwen is ~18 GB. Both fit in 128 GiB, but not
comfortably alongside a page cache. Whichever server is idle gets paged out and
pays a large penalty on its first request.

`run_matrix.sh` therefore runs **all ds4 trials, stops ds4 to free the 91 GiB,
then runs all Qwen trials**, preloading each model first. Neither model is ever
measured while paged out.

The first smoke test ignored this and measured ds4 at ~27 GiB resident — a
number that flattered Qwen. It is reported in the README with that caveat
attached rather than quietly dropped.

### Everything else is held constant

Same prompt, same permission mode (`bypassPermissions`), same repository, same
base commit, same tests, same machine, same `claude` binary. The only variables
are the model and its context window.

---

## 7. What is measured

| field | meaning | comparable across backends? |
|---|---|---|
| `passed` | the oracle: did the tests pass | **yes** |
| `wall_seconds` | end to end, including tool calls | **yes** |
| `num_turns` | agent round trips | **yes** |
| `output_tokens` | tokens the model generated | yes, with care |
| `input_tokens` | tokens the model consumed | **no — ds4 reports 0** |
| `api_ms` | time inside API calls | yes |
| `touched_tests` | did the agent edit the tests | cheat detector |
| `total_cost_usd` | **fiction for local models** | no — ignore |

**Wall time is the headline.** It includes the agent's own tool calls, its
thinking, and every round trip — which is what a person actually waits for.

**`num_turns` is diagnostic, not a score.** Fewer turns is not automatically
better; an agent that solves a task in 4 turns and one that solves it in 12 both
solved it. But a high turn count paired with a failure almost always means
thrashing, and is worth reading the transcript over.

**`total_cost_usd` prices local inference at Anthropic API rates.** A 124-second
local run reported \$0.14. It is meaningless here.

---

## 8. Statistics

`summarize.py` reports **medians, not means**.

These runs have a fat right tail. An agent that goes down a wrong path can take
five times as long as one that does not, and a single such run drags a mean
somewhere unrepresentative. This mirrors the finding in
[`../ds4/coding/RESULTS.md`](../ds4/coding/RESULTS.md): the mixed build's median
output was a normal 783 tokens, but ~4.9% of prompts ran to the token cap. The
median described the typical experience; the mean did not.

**One trial is not a result.** These models sample; the same task can succeed and
fail across runs. The default matrix is 3 trials, which is enough to notice a
large difference and *not* enough to establish a small one. A 1-of-3 versus
2-of-3 split is noise. Treat wall-time differences under ~20% on a single task
as noise too.

With 5 tasks × 3 trials = 15 runs per backend, a difference in overall pass rate
of one or two runs is not significant. Report it as "no difference detected",
not "they are equal".

---

## 9. Threats to validity

Honest accounting of what could make these numbers wrong.

| threat | mitigation | residual risk |
|---|---|---|
| Agent edits the tests to pass | `touched_tests` flag; counted as failure | none meaningful |
| Agent recovers code from git history | trial repo's only commit is the excised state | none from history; the original body is not present in the checkout |
| Task measures nothing (skipped tests) | control run required to fail | none — this is checked every trial |
| Trials contaminate each other | isolated copy per trial, destroyed after | see "the sandbox was not a sandbox" below |
| Memory pressure favours one model | phased runs, preload, one model resident | thermal drift across a long run |
| Training-data contamination | repo is small and recent, but **public** | cannot be ruled out; the libraries it uses are certainly in training data |
| Single-trial noise | 3 trials, medians | small effects remain undetectable |
| Prompt favours one model | identical prompt text for all backends | prompt style may suit one model's training |

**The contamination point deserves emphasis, and it got weaker.** An earlier
revision of this document claimed `gmail-archive` is private and therefore
almost certainly absent from any training set. **That was wrong — the repository
is public** (`evanwtf/gmail-archive`).

Contamination therefore cannot be ruled out by construction. Two things still
limit it: the repository is small and recent, so its weight in any corpus is
negligible; and the excised bodies are not distinctive enough to be memorised
verbatim in a way that would not also be reconstructible from the docstring and
tests.

The tasks also involve `email`, `mailbox`, `hashlib` and RFC 2822 — well
documented standard-library territory. A model that has seen a lot of Python has
a real advantage there that is not specific to this repository, and that is
arguably the correct thing to measure.

**If contamination matters to a conclusion you are drawing, re-run the task
against a repository written after the models' training cutoffs.**

### The sandbox was not a sandbox

**Discovered 2026-08-17, after 147 trials had already been run.**

Until this date each trial ran in a `git worktree` of the reference repository.
That was described here as making contamination between trials impossible.
**It did not.**

A linked worktree shares the parent's object store and keeps a pointer back to
it. A worktree isolates *files*; it does not isolate the *agent*, which has a
shell and can go anywhere on the machine. On 2026-08-17 one did: the reference
repo `~/git/gmail-archive` was found on a benchmark commit
(`benchmark: remove BlobStore.put`) in detached HEAD, with agent edits to
`mbox.py` and `storage.py` in its working tree. Its reflog recorded
`checkout: moving from main to 8d6183d`.

Two consequences, and the second is worse than the first:

1. **The operator's working copy was modified** by a benchmark run.
2. **Every trial started after that point exported its checkout from a
   contaminated reference repo**, and nothing detected it.

There is also an answer-leak path implied by the same mechanism: a shared
object store contains every commit, including the pristine original of the
function under test. An agent that reaches the parent can read what it is
being asked to reproduce.

**What changed.** Trials no longer use worktrees. `build_checkout()` exports
the pinned commit with `git archive` into a bare directory — no `.git`, no
pointer to the parent, no shared objects — and the excised state is then
committed as that repository's *only* commit. The original body is therefore
absent from the checkout entirely, including from history.

Three checks now exist where there were none:

- `run.py` **refuses to start** unless the reference repo is clean and holds
  the pinned `base_commit`.
- Every row records `source_repo_intact`, checked after the agent runs.
- An escape is logged loudly rather than passing silently.

**What this does to the existing 147 trials.** They cannot be retroactively
verified — `source_repo_intact` did not exist when they were written. The
detected escape happened during the OpenCode series on 2026-08-17; how many
earlier trials were affected, if any, is unknown. Treat pre-2026-08-17 results
as measured under weaker isolation than this document previously claimed.

**A leak check was run and found nothing.** If an agent had recovered the
original body from the shared object store, the signature would be a fast pass
in unusually few turns. Every trial under 90 s was inspected: all but three are
`ornith:35b`, which is simply the fastest backend tested (92.5 t/s), and their
turn counts are unremarkable (7-13). The two fastest low-turn passes -- ds4 at
83.4 s / 6 turns, ornith at 76.5 s / 7 turns -- sit within the normal range for
those backends, whose medians are 8 turns.

This is weak evidence, not exoneration: the check is retrospective, and the
telemetry that would settle it did not exist at the time. It is recorded so the
question is not silently dropped.

### Authorship contamination, and why the hosted reference is time-only

`gmail-archive` was itself written with Claude. That makes the hosted
`opus5` reference a special case: it is being asked to restore functions from
a codebase it authored, which is closer to recall than to problem-solving.

**Read the reference row for wall time only.** Its pass rate is not evidence
about task difficulty, and a 15/15 there must not be cited as showing the
tasks are too easy (issue #4) — it would show authorship contamination
instead. Answering issue #4 needs harder tasks in a repository no frontier
model wrote.

The local backends are not exempt from this either: they are restoring
Claude-authored code, which may suit some model lineages better than others.
It applies equally across them, so it does not distort the comparison between
local backends — but it is a reason the whole suite flatters models trained on
similar code.

### The empty-virtualenv confound — withdrawn 2026-08-28

**This section described a flaw that does not exist and never did.** It is kept,
corrected rather than deleted, because it was cited in issue #4 as work that had
to land before the task set could change, and because a retracted claim is worth
more visible than absent.

**What it said.** A fresh checkout has no `.venv`, so the agent must work out how
to run the tests — `uv run pytest`, `uv sync` first, or discovering that
`.venv/bin/python` is not there — before it can see whether its implementation is
correct. The prescribed fix was to run `uv sync` before handover, and, because
that would change every wall-time number, to start a new series and never pool
across the change.

**Why it is wrong.** The control check runs `uv run pytest` in the worktree
*before* the agent is invoked, and `uv run` materialises `.venv` with the project
and pytest installed. That takes about 1.2 s and it has been the control's exact
form since the harness's first commit. All 482 recorded rows carry a control
result, so no trial has ever been handed an empty environment.

The evidence originally cited — the first ds4 trial running
`.venv/bin/python -m pytest` — was read backwards. That command works in the tree
as handed over; it was verified directly. The agent guessed a path and the path
was there.

**What follows.** There is nothing to fix, so the harder tasks in #4 do **not**
start a new series and results may be pooled across them. Environment discovery
is not part of the wall-time numbers, which removes it as an explanation for the
1.74x within-condition spread in #26 — that spread is sampling variance, and this
was never a competing cause.

`test_trial_integration.py` now guards the property directly: a scripted agent
runs the tests through `.venv/bin/python` and the trial must pass. If the control
is ever moved after the agent, or stops using `uv run`, the confound becomes real
and that test goes red first.

---

## 10. Extending it

### Adding a task

1. Pick a function whose tests **run** — not skipped, not requiring services
   you will not start.
2. Add a `[[task]]` block to `tasks.toml`.
3. Run `--dry-run` and confirm the control fails. **If it does not, the task is
   invalid.** Fix it or discard it.
4. Prefer tasks that break a meaningful number of tests. The current set spans
   3 to 49, which gives a difficulty gradient.

Do not name the tests in the prompt. Finding them is part of the task.

A task may remove **more than one symbol**, and may remove the **docstring** as
well as the body:

```toml
[[task]]
name = "mbox-quoting-both-halves"
tests = ["tests/test_mbox.py", "tests/test_parser.py"]
targets = [
    { file = "src/gmail_archive/mbox.py",   symbol = "strip_envelope" },
    { file = "src/gmail_archive/parser.py", symbol = "unquote_mbox" },
]

[[task]]
name = "parser-mbox-quoting-nodoc"
file = "src/gmail_archive/parser.py"
symbol = "unquote_mbox"
keep_docstring = false          # the contract goes too
```

The inline `file`/`symbol` form still works and must keep working: recorded rows
name tasks defined that way, and a task name has to keep meaning what it meant.

**Check for a leading comment** before adding a target. A comment is not an AST
node, so the first statement's line number points past it and the comment stays
in the hollowed-out file. A comment that describes the algorithm hands over the
answer. None of the current targets has one; `test_excise.py` pins the
behaviour.

### Measurements taken alongside the verdict

The oracle is binary and stays the authority — `results.verdict()` is the only
thing that decides a pass, and nothing below feeds into it. When seven of eight
backends score 100%, the next question is whether the passing solutions are
equally good, and these are what make that askable (issue #4).

| key | what it says |
|---|---|
| `solution_patch`, `solution_sha256` | the agent's diff, kept and hashed |
| `gates_before`, `gates_after`, `gates_delta` | ruff and mypy counts, and the change |
| `restored_verbatim` | the body came back unchanged, modulo whitespace |
| `removed_symbols`, `keep_docstring` | what the task actually withheld |

Three properties are deliberate.

**The gates are the target repository's own.** ruff and mypy as gmail-archive
configures them, not a rubric written here. The harness's claim is that it does
not judge, and inventing a quality standard would end that.

**They are deltas, not absolutes.** gmail-archive carries 18 mypy errors at the
pinned commit, and the `NotImplementedError` stub adds a ruff violation of its
own. The baseline is measured on the excised tree, so the number describes the
agent rather than the repository.

**Absent is recorded as absent.** A gate that is missing, times out or crashes
leaves its key out entirely; it is never present as `0`. A zero reads as "clean"
and becomes a published claim.

`restored_verbatim` exists because of authorship contamination. gmail-archive
was written with Claude, so a model reproducing a function its own family wrote
is recalling, not solving — the reason the hosted reference cannot calibrate
difficulty. `excise` hands back the exact body it removed, so this is checkable,
and until now it was never checked.

Before 2026-08-28 the worktree was deleted with the solution still in it. 398
trials produced code and none of it was kept, which is why no claim about *code
quality* has ever had evidence behind it.

### Adding a backend

Add a `[backend.name]` block with `base_url`, `auth_token`, `model` and
`context_tokens`. Anything speaking Anthropic `/v1/messages` works — that is
the whole interface. Set `context_tokens` to the server's *real* window.

For Ollama ≤ 0.32.13, point `base_url` at the shim on `:11500` rather than
Ollama directly; see the repo README.

### Adding a target repository

`tasks.toml` currently assumes one repo. Supporting several means moving `repo`
and `base_commit` into each task. The rest of the harness is already agnostic —
it only needs a git repo, a way to run tests, and tests that fail when code is
removed.

---

## 11. What this does not measure

Stated plainly, so nobody over-reads the results:

- **Code quality.** A passing solution may be unidiomatic, slow, or insecure.
- **Long-horizon work.** Every task is a single function. Nothing here tests
  multi-file refactors, or work spanning hours and compactions.
- **Ambiguity handling.** The tasks are unambiguous by construction. Real work
  is not, and knowing when to ask is a large part of being useful.
- **Reading comprehension at long context.** Tasks fit comfortably in both
  models' windows. Long-context *quality* remains unmeasured — the same gap
  noted in `benchmarks/ds4/0731/claude_code_recommendations.md`.
- **Tool breadth.** No web access, no MCP servers, no subagents.
- **Recovery from its own mistakes** beyond what a single task exercises.
