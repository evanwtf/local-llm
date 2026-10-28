# Methodology

Why this benchmark is built the way it is, what it can and cannot tell you, and
how to extend it without breaking the parts that make it trustworthy.

Read [`README.md`](README.md) first for how to run it. This document is about
*why*. The task inventory is in [`TESTING-SET.md`](../../TESTING-SET.md), and
the prompts as sent are in [`PROMPTS.md`](PROMPTS.md).

---

## 1. What question is this answering?

**"If I point a coding agent at this local model, will it finish a real coding
task, and how long will I wait?"** OpenCode is the primary agent; Claude Code
and Codex are reference points.

That is deliberately narrower than "which model is better". A coding agent is a
loop — read files, call tools, edit, re-read, decide it is done — and a model
can be strong at code while being bad at that loop: not stopping, not reading
before editing, forgetting what it changed.

The other benchmarks in this repo measure the engine, on the M5 Max:

| benchmark | measures |
|---|---|
| [`ds4/0731/`](../../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/ds4/0731/) | prefill and generation throughput, thermals, long context |
| [`ollama/`](../../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/ollama/) | the same, for Ollama's MLX backend |
| [`ds4/coding/`](../../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/ds4/coding/) | HumanEval pass@1 — single-shot code generation |
| **`benchmarks/agent/`** | **the whole agent loop, end to end** |

HumanEval saturated at 96–98% for both DS4 quants and could not rank them
(p = 0.453). That is the gap this fills: HumanEval asks "can it write a
function"; this asks "can it operate a repository".

---

## 2. The oracle problem

Every agent benchmark faces the same question: **who decides if the work is
good?** The usual answers are all bad:

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
   `gmail-archive` runs 166 tests in about 5 s. A suite that takes minutes would
   dominate the measurement.
2. **Tests must be deterministic.** A flaky test becomes a coin flip attributed
   to the model.
3. **Tests must not be skipped.** See §4.

---

## 3. Why excision, and why keep the signature

An excision task removes one function body and replaces it with
`raise NotImplementedError`, **keeping the signature, type annotations, and
docstring.**

This mirrors the real situation. An engineer picking up a ticket has the
contract — the name, the types, the docstring, the callers, and the tests — and
has to supply the implementation. They are not guessing at an unknown API.

Removing the docstring too would make the task "infer the contract from the
tests", which is a different and much noisier skill; the `-nodoc` variant does
exactly that, on purpose (§10). Removing the signature would break the callers
and turn a focused task into a repo-wide refactor.

The excision is done with the **AST** (`excise.py`; `swift_excise.py` for the
Swift tasks), not with regexes or line numbers, so it works on methods, nested
functions and decorated functions, and is indifferent to formatting.

The harness then commits the excised state as the checkout's **only** commit
(§5). Without that, the original implementation would sit in `HEAD`, and the
task would collapse to `git checkout -- src/...`. Agents do try this — not
maliciously, but because inspecting git history is a reasonable debugging
move.

---

## 4. The control run, and the task it killed

**Before the agent runs, the harness runs the tests and requires them to fail.**

If the tests still pass after the excision, the task is measuring nothing — the
model gets a free pass regardless of what it does. `run.py` records this as
`control_fails_as_expected`, and `results.verdict()` fails any row where it is
false.

This is not hypothetical. The first task list included `query.search`, a
221-line SQL surface. The first dry run reported:

```
ERROR query-search-ds4-1: tests still pass after excision -- task is broken
```

`tests/test_query.py` skips every test without
`GMAIL_ARCHIVE_TEST_DATABASE_URL`, so deleting the function changed nothing
observable. **That task would have reported 100% pass rates while measuring
nothing at all.** It was replaced with `mbox-scan`. To use the SQL surface,
bring up the compose stack and export the database URL first.

The general lesson: *a benchmark that cannot fail is not measuring anything.*
Verify the negative case.

---

## 5. Isolation

Each trial runs in its **own standalone directory**. `build_checkout()` exports
the pinned commit with `git archive`: no `.git`, no pointer to the parent, no
shared objects. The excised state is then committed as that repository's only
commit, so the original body is absent from the checkout, history included.
The directory is destroyed afterwards. §9 explains why this replaced a
`git worktree`.

- **Trials cannot contaminate each other.** A stray file, a half-applied edit,
  or a `.pyc` cannot affect the next run.
- **Task order does not matter.** Runs are independent, so they can be
  reordered or re-run without invalidating comparisons.
- **The reference checkout is never edited.** Under the default
  `--targets legacy` (#54), `run.py` parks it aside for the batch and puts the
  export at the path a model guesses. Under `--targets sandbox` (#146), the
  export builds from the harness's own `sandbox/` clones
  (`scripts/sync_sandbox_targets.py`), and nothing in `~/git` moves.
- **The agent is confined.** `sandbox-exec` on macOS and `bwrap` on Linux keep
  it inside its checkout and away from the answers; each row's `confinement`
  field records what was enforced
  ([measurement discipline](../../docs/measurement-discipline.md#name-the-confounds)).

`base_commit` is pinned in `tasks.toml`. Results stay comparable as the target
repository moves on; a new `base_commit` starts a new series.

---

## 6. Fairness between backends

### Context windows are deliberately different

Each backend runs at its server's real window (`context_tokens` in
`tasks.toml`). Equalizing them would be the wrong kind of fair. A cap below a
model's window throws away a real capability. A window above what the server
was started with makes the client auto-compact *after* the server has already
truncated, which is silent corruption rather than a fair fight. The window is
part of the product.

### Memory pressure is controlled, not ignored

These models nearly fill unified memory. An idle server gets paged out and pays
a large penalty on its first request, and a resident second server contends for
memory and bandwidth for the whole batch. So `run.py` refuses to start while
another model server is resident, or when the selected backends span more than
one engine (`--allow-contended` overrides this for a diagnostic whose timings
will not be published). The first smoke test ignored this and measured ds4 at
~27 GiB resident, a number that flattered its rival.

### Everything else is held constant

Same prompt, same repository, base commit and tests, same machine, same client
at the same version, same permission policy. The variables are the model, the
engine and its configuration. A component version change starts a new series
(`preflight.py` updates every component before a batch, never after).

---

## 7. What is measured

| field | meaning | comparable across backends? |
|---|---|---|
| `passed` | the oracle: did the tests pass | **yes** |
| `wall_seconds` | end to end, including tool calls | **yes** |
| `num_turns` | agent round trips, as the client counts them | **yes, within one client** |
| `output_tokens`, `reasoning_tokens` | tokens the model generated | yes, with care |
| `input_tokens` | prompt tokens: OpenCode records the largest step's, Claude Code the sum of cached and uncached (#74) | **no** |
| `touched_tests` | did the agent edit the tests | cheat detector |

**Wall time is the headline.** It includes the agent's own tool calls, its
thinking, and every round trip — which is what a person actually waits for.

**`num_turns` is diagnostic, not a score.** Fewer turns is not automatically
better; an agent that solves a task in 4 turns and one that solves it in 12 both
solved it. But a high turn count paired with a failure almost always means
thrashing, and is worth reading the transcript over.

---

## 8. Statistics

`summarize.py` and `scripts/report.py` report **medians, not means**.

These runs have a fat right tail. An agent that goes down a wrong path can take
five times as long as one that does not, and a single such run drags a mean
somewhere unrepresentative. The same holds for HumanEval output
([`ds4/coding/RESULTS.md`](../../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/ds4/coding/RESULTS.md)):
the mixed build's median output was a normal 783 tokens, but ~4.9% of prompts
ran to the token cap.

**One trial is not a result.** These models sample; the same task can succeed
and fail across runs. Three trials notice a large difference and cannot
establish a small one: two task medians need about a 56% gap before the
difference is real, and two suite totals about 26%. A 1-of-3 versus 2-of-3
split is noise. Report a small difference as "no difference detected", not
"they are equal". The measured table is in
[Know what a trial count can support](../../docs/measurement-discipline.md#know-what-a-trial-count-can-support).

---

## 9. Threats to validity

| threat | mitigation | residual risk |
|---|---|---|
| Agent edits the tests to pass | `touched_tests` flag; counted as failure | none meaningful |
| Agent recovers code from git history | the checkout's only commit is the excised state | none; the original body is not in the checkout |
| Task measures nothing (skipped tests) | control run required to fail | none — this is checked every trial |
| Trials contaminate each other | isolated export per trial, destroyed after | see "the sandbox was not a sandbox" below |
| Agent leaves its checkout | confinement, `source_repo_intact` on every row | network and some paths unenforced; the row says which |
| Memory pressure favors one model | `run.py` refuses a resident second server | thermal drift across a long run |
| Training-data contamination | repo is small and recent, but **public** | cannot be ruled out; the libraries it uses are certainly in training data |
| Single-trial noise | 3 trials, medians | small effects remain undetectable |
| Prompt favors one model | identical prompt text for all backends | prompt style may suit one model's training |

**Contamination cannot be ruled out by construction.** `gmail-archive` is
public (`evanwtf/gmail-archive`); an earlier revision of this document said it
was private, which was wrong. Two things limit it: the repository is small and
recent, so its weight in any corpus is negligible, and the excised bodies are
not distinctive enough to be memorized verbatim in a way that would not also be
reconstructible from the docstring and tests. The tasks also involve `email`,
`mailbox`, `hashlib` and RFC 2822 — well-documented standard-library territory,
where a model that has seen a lot of Python has a real advantage, which is
arguably the correct thing to measure. **If contamination matters to a
conclusion, re-run the task against a repository written after the models'
training cutoffs.**

### The sandbox was not a sandbox

Until 2026-08-17 each trial ran in a `git worktree` of the reference
repository. A linked worktree shares the parent's object store and keeps a
pointer back to it, so it isolates *files*, not the *agent*, which has a shell.
On 2026-08-17 an agent followed that pointer: the reference repo was found on a
benchmark commit in detached HEAD, with agent edits in its working tree. The
operator's working copy was modified, every later trial exported from a
contaminated repo, and the shared object store held the original of every
function under test.

The fix is §5's `git archive` export, plus three checks: `run.py` **refuses to
start** unless the reference repo is clean and holds the pinned `base_commit`;
every row records `source_repo_intact`, checked after the agent runs; and an
escape is logged loudly.

**Rows before 2026-08-17 were measured under weaker isolation**, and cannot be
verified retroactively. A leak check found nothing: a leak would show as a fast
pass in unusually few turns, and every trial under 90 s sat within its
backend's normal turn range. That is weak evidence, not exoneration.

### Authorship contamination, and why the hosted reference is time-only

`gmail-archive` was itself written with Claude. The hosted Claude reference is
therefore asked to restore functions from a codebase its own family wrote,
which is closer to recall than to problem solving.

**Read the reference row for wall time only.** Its pass rate is not evidence
about task difficulty, and a perfect score there must not be cited as showing
the tasks are too easy (#4). It would show authorship contamination instead.

The local backends are not exempt: they restore Claude-authored code, which may
suit some model lineages better than others. That applies equally across them,
so it does not distort the comparison between local backends, but it is a
reason the whole suite flatters models trained on similar code.

### The agent gets a working environment

The control check runs `uv run pytest` in the checkout *before* the agent
starts, which builds `.venv` with the project and pytest installed, and
`prepare_env` runs `uv sync --frozen` before that (`env_prepared` on the row).
No trial has met an empty virtualenv. A 2026-08-28 revision of this section
claimed the opposite and was withdrawn; the evidence it cited, an agent running
`.venv/bin/python -m pytest`, works in the tree as handed over.
`test_trial_integration.py` guards the property: a scripted agent runs the
tests through `.venv/bin/python` and the trial must pass. If the control moves
after the agent, or stops using `uv run`, that test goes red first.

---

## 10. Extending it

### Adding a task

1. Pick a function whose tests **run** — not skipped, not requiring services
   you will not start.
2. Add a `[[task]]` block to `tasks.toml`.
3. Run `--dry-run` and confirm the control fails. **If it does not, the task is
   invalid.** Fix it or discard it.
4. Prefer tasks that break a meaningful number of tests. The original five
   excision tasks break 3 to 49, which gives a difficulty gradient.
5. Regenerate [`PROMPTS.md`](PROMPTS.md) with `gen_prompts.py`.

Do not name the tests in an excision prompt. Finding them is part of the task.

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

The inline `file`/`symbol` form must keep working: recorded rows name tasks
defined that way, and a task name has to keep meaning what it meant.

**Check for a leading comment** before adding a target. A comment is not an AST
node, so the first statement's line number points past it and the comment stays
in the hollowed-out file. A comment that describes the algorithm hands over the
answer. None of the current targets has one; `test_excise.py` pins the
behavior.

### Replay tasks (#714)

**What they measure.** An excision task asks for one function body back. On the
cluster ledger, seven of eight stacks pass every trial of those (#711), so
they no longer separate models on problem solving. A replay task is a larger,
real unit of work: a gmail-archive commit C that added a feature together with
its tests. The harness exports C, puts every file C touched back to its state at
C's first parent (a file C created is deleted), and keeps C's tests. The agent
gets a plain-language description of the feature, restated from the commit
message, and the names of the failing test files. It has to rebuild the feature
across however many files it takes. The oracle is C's listed tests, and there is
no judge model. The seven tasks revert 37 to 583 lines across 1 to 9 files.

**How.** `kind = "replay"`, `base_commit` = C, `revert` = the paths to put back,
`tests` = the oracle (see `tasks.toml`). `revert` is written out by hand and
checked by `test_task_definitions.py`: it must be exactly C's non-test files
minus `pyproject.toml` and `uv.lock`, which stay at C because the tests cannot
import without C's dependencies, and adding a dependency is packaging work, not
the feature. The control check and `touched_tests` work as they do for
excision. A file the agent creates counts too: a replay trial marks new files
`--intent-to-add` before it diffs, so a new `tests/conftest.py` counts as
touching the tests and the saved patch includes the new modules.
`scripts/verify_replay_tasks.py` re-derives each task's validity: the tests
pass at C and fail after the revert. The commit and parent shas, the reverted
paths, and C's line counts go on every row under `replay`, with
`task_kind = "replay"`.

**Opt-in, sandbox only.** The tasks pin commits on gmail-archive's `main`, not
the `56e55cc` the matrix pins. One checkout cannot sit at both, so each replay
task has its own clone, `sandbox/gmail-archive@<sha7>`, made by
`scripts/sync_sandbox_targets.py`. The legacy layout refuses them. They are out
of the default matrix, so no existing comparison changes: run them with
`--replay --targets sandbox`.

**Limits.**

- **The tests are visible**, and the prompt names them. The agent can read what
  is expected and write to it. That is the same contract as a real task
  handed over with failing tests, but it is not specification-only problem
  solving. The harder tasks below hold some tests out (#726).
- **Recall is possible.** `evanwtf/gmail-archive` is a public repository
  (created 2026-08-05), and it was written with Claude. A model trained on it
  after that date may have seen these exact commits. `restored_verbatim` on a
  replay row is true only if every reverted file matches C byte for byte, and
  `replay.recall` records per file the lines that differ from C and from the
  starting state. Both are recorded beside the verdict and never change it.
  Read a verbatim match as recall. The hosted reference is time-only here too
  (see *Authorship contamination* above).
- **Untested surface is unchecked.** A commit's CSS, templates and docs are
  reverted with it, and the prompt describes them, but no test reads them. The
  oracle only proves the tested part.
- **The oracle is only the listed tests.** `replay-gmail-api-sources` leaves out
  `TestMboxSource`: its six tests read a fixture that gmail-archive's
  `.gitignore` (`*.mbox`) kept out of the commit, so they fail at C itself. The
  prompt tells the agent so.
- **Most controls fail at collection.** When C created the module a test
  imports, the reverted tree fails with an import error rather than a failing
  assertion. That is the feature missing, and it is what the task is. Only
  `replay-mbox-separator` (3 of 19 tests fail) and `replay-login-throttle` (3 of
  54) fail at assertions.

### Harder replay tasks: held-out tests and spans (#726)

**Why.** After one run each, both cluster stacks passed all 21 replay trials
(#726 has the table), so the seven tasks above rank only time. Two changes
make a task harder with no judge model. The new tasks carry `suite = "hard"`
and run with `--replay-hard --targets sandbox`. `--replay` still runs exactly
the seven above, so neither set grows when the other does.

**Held-out tests.** `hidden_tests` lists pytest node ids the agent never sees.
After the revert and before the starting commit, `replay.hide` removes them
from the agent's tree. A bare file id deletes the file; `file::Class` or
`file::Class::test` cuts that definition out of its file, with its decorators
and the comment block directly above it. The tree is then read back
(`replay.still_visible`), and a trial stops if any held-out id is still
defined. `touched_tests` is taken against that starting commit, so it covers
only tests the agent could see. After the visible oracle,
`replay.hidden_restored` writes the held-out files back and runs the held-out
ids once. The files come from `base_commit`, or from `hidden_ref` when the
tests are regression tests the repository added later. Afterwards every file
goes back to how the agent left it, so the gates, recall and saved patch never
see a held-out test.

**The verdict.** `passed` stays the visible oracle, and `results.verdict()` is
unchanged, so a replay task's series means what it meant. The held-out run is
recorded beside it: `hidden_passed`, plus `hidden.pytest`, `hidden.counts`,
`hidden.ref` and what hiding did to each id (`hidden.hidden`).
`results.hidden_verdict()` applies the same guards as `verdict()` (no touched
tests, sandbox intact, control failed) and then reads `hidden_passed`. It is
None on a row whose task holds nothing out. A timeout is False, as it is for
`verdict()`. The harder tasks get their own table in `docs/results.md`, with a
`hidden passed` column beside `passed`. A visible pass with a hidden failure
is the signal these tasks exist for: a solution fitted to the tests it could
read.

**Choosing them.** `scripts/hidden_test_candidates.py` measures every test
function in the task's visible files. A function is eligible if it passes at
the reference, fails after the revert, and is not older than the work (a test
the file already had fails after the revert only because its file cannot
import something new). Of the eligible functions it proposes the third with
the smallest sha256 of their node id. That choice is reproducible and blind
to what each test checks. It never takes every eligible test, so the visible
control still fails. `--later REF` instead proposes tests that REF added to
the same files, run from REF's copy against the reference tree.
`scripts/verify_replay_tasks.py` then proves each task, on the tree exactly
as the agent gets it:

- the visible tests pass at the reference with the held-out ones cut;
- the held-out tests pass there twice, with identical counts;
- both sets fail after the revert;
- no held-out test uses a name the project defines that the agent cannot
  read: not in the prompt, the visible tests, or the starting source. A test
  that calls a function nothing visible names fails for the name, not the
  behavior. Three of `main`'s later regression tests for the web login fail
  this check on `_client_id`, so they are left out.

**Spans.** `span_start` names the first of several consecutive commits,
and `base_commit` the last. The revert puts every path back to `span_start^`,
and `revert` must be every non-test file the span touched, net
(`test_task_definitions.py` checks this). The row records `replay.span_start`
and `replay.span_commits`. `replay-span-web-ui` is 7ef1734 through f4faf86:
the Gmail-style UI rebuild, URL defanging, the day picker and its inbox
checkbox, and search operators. That is 1,643 added and 686 removed lines
across 17 files, 4 of them new. Its oracle is five test files, 96 tests that
need no database and 26 that skip without one. `replay-span-web-ui-no-test-names`
is the same task with a prompt that names no test file
(`prompt_names_tests = false`, which `replay.validate` enforces), so the agent
has to find what to run.

**Considered and not added.** The analytics span (dd20a74..03f88dd, 1,189
added lines) has 5 database-free tests of its own, 3 skip, and the rest of its
oracle would be the `filesize` filter, so almost none of the classifier would
be checked. Phase 7's web UI (04d00c3, 1,002 lines) has 12 database-free
tests. The IMAP work has no database-free tests. `replay-mbox-separator` and
`replay-login-throttle` have 3 discriminating tests each, too few to split.

**Limits.** The held-out node ids are public in `tasks.toml`, which the
agent's confinement denies; `PROMPTS.md` gives only their count. A held-out
test is still one the commit's author wrote, so it shares the visible tests'
view of the feature. Cutting a test leaves any helper or fixture only it used
in the visible file. Each prompt says `The check also runs tests you cannot
see.`, and `replay.validate` requires that sentence.

### Measurements taken alongside the verdict

The oracle is binary and stays the authority — `results.verdict()` is the only
thing that decides a pass, and nothing below feeds into it. When nearly every
backend passes, the next question is whether the passing solutions are equally
good, and these make that askable (#4).

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

`restored_verbatim` exists because of authorship contamination: a model
reproducing a function its own family wrote is recalling, not solving.
`excise` hands back the exact body it removed, so this is checkable. Rows before
2026-08-28 kept no solution at all, so they carry no evidence about code.

### Adding a backend

Add a `[backend.name]` block with `base_url`, `auth_token`, `model`,
`context_tokens`, and `opencode_model` for the primary client. Set
`context_tokens` to the server's *real* window. Write the backend's confounds
(engine, quant, sampler) into the block as you add it. For Ollama ≤ 0.32.13
under Claude Code, point `base_url` at `ollama_claude_shim.py` on `:11500`
rather than at Ollama directly
([docs/ollama-claude-shim.md](../../docs/ollama-claude-shim.md)).

### Screening a new stack (#762)

Compute time is finite, and a bad model must cost a fraction of a run, not
three full runs (operator, 2026-09-25 and 2026-09-27). Every new stack goes
through four stages. The same rule applies on every machine; only the
thresholds marked "per machine" differ.

**Stage 0: the card screen, before any download.** Read the model card and
answer each item. A "no" on items 1–3 is a veto; record it on the issue and
download nothing.

1. **Coding or agentic work is in the card's headline claim.** Vendor
   benchmark scores are not evidence, but a card that leads with something
   else (MiMo-V2.6-Flash led with "Native Omnimodal") is flagged.
2. **The active parameters are under the machine's line (per machine).**
   Decode reads the active weights once per token on both the GB10 and Apple
   silicon, so active parameters predict speed better than anything else on a
   card. Record the value as `active_params_b` on the backend. No line is
   fixed yet; the cluster proposal is about 20–25B, and the M5 Max leader is
   6B active.
3. **The best first-party quant fits** in the machine's usable memory and under
   its download cap (`CONVENTIONS.md`).
4. Noted, not a veto: weights only from a third party (they need operator
   approval anyway), and a reasoning effort that defaults to max (large models
   launch at `low`).

**Stage 1: one full run, with the early stop on.** `run.py` stops a run against
the leader on the same task set and client in the machine's ledger
(`benchmarks/agent/screening.py`):

- **Failure stop**, checked after every trial, from the first: at
  `ceil(0.30 × leader passes)`, with the leader's pass rate scaled to this
  run's planned trials. A 21/21 replay leader stops a 21-trial run at its 7th
  failure. A timeout, a failed suite, and a pass that edited the tests are all
  failures.
- **Time stop**, once every task has run one trial: at 2× the leader's time on
  the same (task, trial) pairs. A timeout counts at its full limit.
- **The stop is on for every run, re-checks of standing picks included**
  (operator, 2026-10-16). A re-check that trips it has lost its place as a
  pick, and its remaining trials would only buy timeouts. In the #1086 refresh,
  one re-check run with the stop off spent 3.5 hours on 7 timeouts in its
  first 15 trials.
- **A run started without the stop** cannot switch it on.
  `scripts/early_stop_check.py --backend <name> --set replay` applies the same
  rule to the rows it has written so far; exit status 3 means stop it by hand.
- **Turning it off needs a reason on the record:** `run.py` refuses
  `--no-early-stop` without `--no-early-stop-reason "<why>"` and logs the
  reason at the start of the run. A leader's own baseline run is the usual
  reason. Without a leader in the ledger, the run logs that and the stop stays
  off.
- **The leader** is the machine's `[early_stop.leader]` pin in `tasks.toml`
  when it has one, else the best pass rate on the same task set and client,
  then the fastest. `--early-stop-leader` overrides both for one run. A pin
  exists because an unpinned best can be an old run that every current stack
  trips.
- **A stop on a first run means "check the configuration" before it means
  "veto":** the chat template, the reasoning and tool-call parsers, the
  reasoning effort, the context length, the sampler. It becomes a veto only
  when a rerun with a checked configuration fails the same way. The abort
  record says which state it is in.
- A void run or an infrastructure fault is rerun, never counted as a veto.

**Stage 2: the cut**, after one full run.
`uv run python scripts/screen_stacks.py --results <ledger>` prints keep, cut or
screened out per stack, with its numbers:

- **Gate:** a pass rate of 90% or better, and a sum of per-task medians under
  2× the leader's. A stack that misses either is "screened out after 1 run",
  not ranked.
- **Rank** the rest by sum of per-task medians. Keep the fastest 3, and any
  stack within 25% of the third, up to 5. One run cannot separate stacks that
  close.

**Stage 3: runs 2 and 3, for kept stacks only.** A stack that was cut or
screened out gets one line in the machine's RECOMMENDATIONS saying why. On the
M5 Max, each model or engine runs at most once a week, so Stage 3 there takes
three weeks (operator, 2026-09-27).

**Why a one-run veto is allowed.** It is a screening decision, not a published
result: "if it fails every test, or takes 6 hours, there's no point continuing
to waste time" (operator, 2026-09-25). A ranking between stacks still needs
three runs.

**Re-entry.** A veto holds for that model on that engine release. A new release
tag or a fixed recipe starts a new series at Stage 1. A new commit on an engine
built from git is not a new release.

### The step timeout: a flat 1,800 s

Every agent step has the same wall-clock limit, `run.py --timeout`, 1,800 s by
default. A test pins the default (`test_the_step_timeout_is_a_flat_1800_seconds`).
A trial that hits it is a failure, and the early stop counts it at the full
1,800 s.

**Why not lower.** On the single Spark's ledger (2026-10-16, 4,382 OpenCode
trials), 27 passes took 20–30 minutes. That is 0.6% of all passes, but 17 of
the 150 replay + hard passes, or 11%, and most of them belonged to the leaders.
At a 20-minute limit the leader would have dropped from 32/42 to 23/42 replay
passes, behind a stack it beat at 1,800 s (31/41 to 29/41): the limit, not the
model, would have set the ranking.

**Why not per task.** A limit of k × the task's median pass, clamped to
600–1,800 s, loses 33 passes for k = 3, 4 or 5 and saves about a third of the
time spent in timeouts (21,414 of 63,000 s across that ledger). Three costs
outweigh that:

- the timeouts sit on tasks whose passes are slow too (the two
  `replay-span-web-ui` tasks have 12 of the 35, and their passes take about 25
  minutes), so their limit stays at 1,800 s and saves nothing;
- it turns slow passes into failures, which mixes speed into the pass rate;
- it drifts as rows land, and each change would start a new series.

**What saves the time instead** is stopping the stack, not the step: the early
stop above, and the #366 guards in `benchmarks/agent/timeout_policy.py` (an
idle-GPU watchdog per trial, and a circuit breaker for a cell that mostly times
out).

Rerun the evidence when the ledger grows:

    uv run python scripts/timeout_sizing.py --results hardware/<machine>/results.jsonl

### The target repository is on its own branch

`~/git/gmail-archive` sits on **`local-llm-benchmark`**, a branch pinned at
`56e55cc`, so that a routine `git pull` cannot move it. `origin/main` had moved
73 commits ahead while the checkout sat held back on `main`; one pull would
have changed what every trial measures. `~/git/monitor` is pinned the same way.

The pin is still the **commit**, not the branch: `run.py` exports with
`git archive <base_commit>`, and a branch name would follow the branch.

```sh
git -C ~/git/gmail-archive checkout local-llm-benchmark
```

### Adding a target repository

A task may set its own `repo` and `base_commit`, overriding the file-level
defaults; the Swift tasks use `~/git/monitor` this way. The harness needs only
a git repo, a way to run its tests, and tests that fail when code is removed.
Results from a new repository are a new series and never pool with another
repository's. `scripts/sync_sandbox_targets.py` reads `tasks.toml`, so the
sandbox clone follows on its own.

---

## 11. What this does not measure

Stated plainly, so nobody over-reads the results:

- **Code quality.** A passing solution may be unidiomatic, slow, or insecure.
  The gates and held-out tests above are signals beside the verdict, not a
  quality score.
- **Long-horizon work.** The largest task is a five-commit span of about 1,600
  added lines (`replay-span-web-ui`). Nothing here tests work spanning hours
  and several context compactions.
- **Ambiguity handling.** The tasks are unambiguous by construction. Real work
  is not, and knowing when to ask is a large part of being useful.
- **Reading comprehension at long context.** Tasks fit comfortably in every
  backend's window. Long-context *quality* remains unmeasured — the same gap
  noted in
  [`ds4/0731/claude_code_recommendations.md`](../../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/ds4/0731/claude_code_recommendations.md).
- **Tool breadth.** No web access, no MCP servers, no subagents.
- **Recovery from its own mistakes** beyond what a single task exercises.
