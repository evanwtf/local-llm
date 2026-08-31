# Working in this repo

Instructions for coding agents. [`CONVENTIONS.md`](CONVENTIONS.md) holds the
standing rules about data and safety; this file covers how to work.

## Check the field before concluding something is not possible

[`SOURCES.md`](SOURCES.md) lists who to watch on X and how to sweep them safely.
Run it when a result looks anomalous, when planning a week, or when about to
conclude that something cannot be done on this hardware -- somebody may have
done it last Tuesday.

Two hard rules, both from mistakes:

- **Verify before repeating.** `~/.claude/skills/grok/verify-posts.py` on
  anything before it reaches an issue. grok has fabricated a post outright.
- **Date and version every claim.** Sources describing a tool from six months
  ago may describe several major versions back (#55).

## OpenCode is the primary harness (2026-08-30)

The project exists as a fallback for when hosted inference is unavailable or
unaffordable. **An open model on an open engine driven by a proprietary client
is not a fallback -- it fails with the vendor.** So the agent layer is held to
the same standard as the model and the engine, and the target stack is
**OpenCode + open model + open engine on owned hardware**.

Claude Code and Codex stay in the suite as **reference points**: they establish
a task's ceiling. A gap between them and OpenCode is **a defect to chase, not a
result to publish**.

Practical consequences:

- A new backend is measured with OpenCode first. The others calibrate it.
- "OpenCode cannot do X" is a bug report, not a benchmark row, until the cause
  is known. #54 is why: its entire measured record turned out to be an artifact
  of running headless without workspace confinement.
- **`opencode run` is headless and `external_directory` defaults to `ask`.**
  With nobody to ask, agents read -- and in one case destructively edited --
  repositories outside the trial checkout. Always set
  `permission.external_directory` explicitly, and check `workspace_escapes` on
  every row before believing it.

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

**Branch per piece of work, then merge it yourself.** A branch keeps one line
of work separable while it is in progress, which is worth having. It is not a
review gate: **do not open a pull request, and do not wait for approval.** When
the work is done and the tests pass, merge to `main` and push.

```sh
git checkout -b <kind>/<issue>-<slug>     # docs/24-..., analysis/26-..., tasks/4-...
# ... work, commit, push as you go ...
git checkout main && git merge --ff-only <branch> && git push
git branch -d <branch> && git push origin --delete <branch>
```

**Delete the branch once it is merged.** A merged branch left behind reads as
work still in flight. Three of them accumulated before this rule was written,
stacked on each other, and `main` sat fourteen commits behind the code its own
README described.

Prefer a fast-forward. The branches here are usually a stack -- each one built
on the last, because the next task starts before the previous is merged -- and a
stack fast-forwards cleanly if nothing lands on `main` in between. Merge from
the bottom up if it does not.

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

## antirez is the sherpa — check his path before designing your own

**ds4 (DwarfStar) is the reference implementation for this hardware.** antirez
runs these models on the machines this project targets — 128 GiB Apple Silicon —
and publishes what works. When a model he has shipped is being evaluated here,
**his current guidance is the starting point, not a footnote.**

Before designing any experiment on a model he covers:

```sh
cd ~/git/ds4 && git fetch --all
git branch -a                       # preview branches carry unreleased models
git log --oneline main..upstream/<branch>
grep -oE "^\s+[a-z0-9-]+\)" download_model.sh   # supported layouts
```

**ds4 is not a general GGUF loader.** Only layouts from its own
`download_model.sh` are supported. A GGUF of the "same" model from elsewhere is
a different artifact, and the metadata says so out loud:

```
antirez GLM-5.3   general.architecture = glm5-next
Unsloth  GLM-5.3  general.architecture = glm5next
```

Neither engine reads the other's file. That single hyphen is why #25 burned
hours on a model that "loads and emits gibberish".

**The cost of not checking, measured 2026-08-29.** GLM-5.3-Flash was evaluated
on Unsloth's GGUF via llama.cpp PR #27752 through the shim, and produced
`glm53 x codex` 15/15 plus a **3,600 s timeout** under Claude Code. On the
supported ds4 path the same question at temperature 0 answered in **3.2 s using
47 completion tokens**, against **76 s and 854 tokens** on the unsupported one.
**18x fewer tokens.** The pathology was the stack, not the model — and the
branch that fixed it had existed the whole time.

**He is also fast.** A model can go from unsupported to shipped in a day, and
preview branches are where it lands first. Re-check before concluding a model
does not work here.

**preflight reports GitHub notifications for `antirez/ds4`, `ggml-org/llama.cpp`
and this repo**, mentions first. It excludes `ci_activity` and keeps *read*
items: the ds4 mention that mattered arrived by email, was already marked read
through the API, and sat under 41 CI failures from unrelated repos. Filtering to
unread would have hidden the only notification worth seeing.

## Always measure the latest infrastructure

llama.cpp, Ollama, Codex and OpenCode ship several times a day. **Update before
a batch, not after it**, and take the newest release of every component this
project measures through.

```sh
uv run python benchmarks/agent/preflight.py    # servers, memory, versions, notifications
codex update                                   # self-updating
opencode upgrade
# Ollama is /Applications/Ollama.app -- update it from the app, not the shell
cd ~/git/llama.cpp && git fetch && git log --oneline HEAD..origin/master
```

The reason is not tidiness. This project exists to say what a local coding stack
can do *now*, and a result measured on a build that upstream replaced last week
answers a question nobody asked. A merged PR is the common case: PR #27742 was a
pinned worktree for two days and then landed in mainline, and NEXT.md still said
"do not `git pull` this away" afterwards.

**A version change starts a new series.** Every row already records the versions
it was produced under, so old results stay readable — but do not pool across an
upgrade, and say in RESULTS.md which side of it a number came from. That is a
cost worth paying: an out-of-date measurement is wrong in a way no amount of
extra trials fixes, while a series boundary is merely an inconvenience.

**Tag a build before leaving it.** `git tag benchmark-<pr>-<date>` on a
worktree you are moving off, so the rows that depend on it stay reproducible.
A squash-merged PR does not leave its commits in mainline history.

## Pin the sampler, and vary one thing at a time

**A sampler parameter can halve the pass rate, and the parameters interact.**
Measured over 36 trials (#36), same task, model, engine and client:

| configuration | pass |
|---|---|
| `top_p 0.95`, no repetition penalty | **17/18** |
| `top_p 0.90`, **no** repetition penalty | **7/12** |
| `top_p 0.90` + `repeat_penalty 1.1` | **6/6** |

Temperature and top_k were each isolated and are innocent. `top_p 0.90` is
harmful **only** without a repetition penalty — the two are coupled, and no
launcher treats them that way.

Every launcher sets a different sampler and **nobody chose them**: `llamacpp-up`
hardcoded Qwen's `0.95` for every model it served; Ollama uses each modelfile,
which for `ornith-1.5:35b` sets nothing and falls back to Ollama's `0.9`.

So: **a cross-engine or cross-backend comparison is not valid unless both sides
are sampler-matched**, and the sampler belongs on the row. `llamacpp-up` takes
`TEMP/TOP_P/TOP_K/MIN_P`; llama.cpp rows carry sampling via the `/props` probe.
Ollama and ds4 rows do not yet, which is a known gap.

**Vary one parameter at a time.** This effect was missed twice by controls that
moved a *set* of related settings together — first three at once, then a
four-cell sweep in which every control cell happened to share the same `top_p`.
A control that changes a group is not a control; it only tells you the group
matters.

## A wall-time difference is a token-count hypothesis

**Check seconds-per-1k-output-tokens before attributing a speed gap to
anything.** Three times now a difference that looked like a property of an
engine, a stack or a client turned out to be how many tokens the model was
induced to emit:

| claim | reality |
|---|---|
| llama.cpp is 66% slower than Ollama (#28) | identical throughput; 4x the tokens. Four sampler defaults |
| MTPLX is 17% faster on 68% fewer tokens | never re-checked; same shape, marked provisional |
| Codex is 2.14x slower than Claude Code on Swift (#44) | **39.6 vs 47.6 s/1k — Codex is *faster* per token, and emits 2.37x more** |

The arithmetic is one line and it settles the question:

```
seconds_per_1k = wall_seconds / output_tokens * 1000
```

If the rates match, the gap is token count — a prompting or sampling effect,
and portable. If they differ, it is throughput, and belongs to the stack.

**Say which one it is.** "X is slower than Y" without this check has been wrong
every time it has been examined here.

## Know what a trial count can support

Measured over 398 trials by `benchmarks/agent/sizing.py`, not estimated:

| trials | one task's median | 5-task suite | pass rate claim |
|---|---|---|---|
| 3 | ± 27.9% | ± 12.9% | >0% only |
| 10 | ± 13.5% | ± 5.4% | — |
| 35 | ± 4.9% | ± 2.2% | **>90%, if unbroken** |

**Three trials is a screening run.** It answers "does this work at all" and "is
this difference enormous". Two suite totals separate only above a ~26% gap, and
two task medians above ~56%. Below that the honest phrasing is "no difference
measured", never "X is faster than Y".

**A pass-rate claim above 90% needs 35 consecutive passes and there is no
shortcut.** One failure costs about twenty trials: 46/46 clears 90%, 46/47 does
not. A 15/15 backend is not "as good as ds4 pending data" -- it is unmeasured
above 80%.

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

**There are two of them**, and `run.py` validates every repo a selected task
uses, not just the file-level default:

| repo | language | pinned branch | oracle |
|---|---|---|---|
| `~/git/gmail-archive` | Python | `local-llm-benchmark` @ `56e55cc` | `uv run pytest -q` |
| `~/git/monitor` | Swift | `local-llm-benchmark` @ `cbb85ca` | `swift test` |

Check them by hand too, whenever you are about to trust a result:

```sh
for r in ~/git/gmail-archive ~/git/monitor; do
  git -C "$r" status --porcelain; git -C "$r" log --oneline -1
done
```

Empty output and the expected commit, or stop and find out why.

**Both are pinned on their own branch rather than tracking `main`**, and that is
not ceremony. On gmail-archive `origin/main` had moved **73 commits ahead** of
the pinned base while the checkout sat held back — a routine `git pull` would
have changed what every trial measures, silently, and irreversibly for
comparison against 600 existing rows. The pin is still the **commit**:
`git archive <base_commit>` is what exports, and a branch name would follow the
branch.

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

## Adding a language means two things, and one of them is a parser

`tasks.toml` takes a per-task `repo`, `base_commit` and `test_command`; anything
that omits them inherits the file-level defaults, so recorded rows keep meaning
what they meant. Adding a language needs:

1. **A test command.** `swift test`, `uv run pytest -q`. It must return a
   non-zero exit when the excision is in place — that is the control check.
2. **An excision module** exposing `excise` and `body_source`, registered in
   `EXCISERS` by file extension.

**The parser is the dangerous half.** Python gets spans from `ast`, which either
parses or raises. `swift_excise.py` matches braces, and a scanner that stops at
the `}` in `let brace = "}"` cuts the wrong span and leaves a file that **may
still compile** — it does not crash, it silently changes what the task is. So it
skips strings, line comments and nested block comments, and the tests pin those
cases specifically.

`body_source` must return **exactly** the span `excise` removes. If they drift,
`restored_verbatim` compares different things and never fires — losing the
recall signal for a whole repository without any error.

`exciser_for` **refuses** an unknown extension rather than defaulting. Handing a
`.rs` file to the Python parser would excise nothing, leave the control check
*passing*, and record a broken task as a valid one.

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
