# Working in this repo

> ## ⚠️ OpenCode results before 2026-08-31 21:47 EDT are INVALID
>
> Any OpenCode trial recorded before `2026-08-31T21:47:18-04:00` measures a
> harness bug -- the client was never told which directory to work in, so it
> solved each task and wrote the answer somewhere else. **Do not quote, pool,
> or compare against those numbers.** Cause, cutover and replacements:
> [docs/archive/results-opencode-pre-dir.md](docs/archive/results-opencode-pre-dir.md). Other clients are unaffected.

Instructions for coding agents. [`CONVENTIONS.md`](CONVENTIONS.md) holds the
standing rules about data and safety; this file covers how to work.

## Cite engine source as `file:line at <sha>` (2026-09-06)

A bare `ds4.c:40442` is not a citation. It is unverifiable a week later and
often unverifiable the same night.

There are **twelve ds4 worktrees on this machine** at eight different shas
(`git -C ~/git/ds4 worktree list`). `ds4.c` is over 70,000 lines and moves
daily. The same function sits at a different line in every tree, so a line
number without a sha does not identify anything.

This cost real work on 2026-09-06. A claim that `raw_cap` hard-clamps a prefill
chunk to 8192 shipped into a script comment, a test docstring and a PR body,
and the script warned users that their large-chunk sweeps were being silently
clamped. Review caught that the line number was wrong; the reviewer's
replacement was also wrong, because the two readers were in different trees and
neither said which. Reading the function by name -- not the line -- showed the
ceiling does not exist at all: `ds4_prefill_cap_for_prompt` uses a non-zero
requested chunk as given, and `ds4_default_raw_cap` is the raw-KV attention cap,
an unrelated quantity. Two wrong line numbers had agreed closely enough to look
like a disagreement about digits rather than a defect in the claim.

So:

- **Write `ds4.c:12159 at ds4 399acbbe`**, naming the tree and the sha.
- **Find the code by name, not by line.** `grep -n 'ds4_prefill_cap_for_prompt'`
  in the tree you mean. A line number is the result of a lookup, never the way
  to do one.
- **Verify a correction before accepting it.** A reviewer's line number is a
  claim like any other, and one wrong number replacing another reads as
  progress.

Retrofitting the ~346 existing citations and enforcing this with a test is
tracked separately; `evidence/0169-device-gate-table.md` is the priority, since
the M5 gate taxonomy rests on 27 of them.

## An issue is a public work log, not a drafting area (2026-09-06)

An issue in this repo is the public record of a task. It holds **what we did
and what we measured**, in the order it happened. That is all it holds.

**Do not post upstream drafts here.** No "DRAFT for the operator to post to
<upstream>", no "READY TO POST", no v2/v3/v4 of the same text superseding each
other in the thread. If something is going upstream, the operator writes and
posts it; our issue is where the results it draws on already live, stated for
our own readers.

**Link the upstream issue once, in the opening post.** Every later comment
repeating the link adds nothing -- the reader arrived through the first one.

Why this is a rule and not a preference: the drafts crowd out the log. #162
reached twelve comments of which five were versions of one unsent upstream
reply, and a reader looking for the q4/q8 numbers had to work out which draft
was current before they could find a measurement. Superseded drafts also age
badly in a way results do not -- a number stays true, a draft addressed to a
person in a conversation that has moved on does not.

Write each comment so it still reads as a result a month later: what was run,
on what tree, how many runs, what the numbers were, and what was thrown away.
Second-person framing ("the re-test you asked for") belongs in the reply that
is actually sent to that person, not in the log.

## Every time and date is ISO 8601, America/New_York, with an explicit offset (2026-09-06)

**One representation, everywhere: `YYYY-MM-DDTHH:MM:SS±hhmm`.** In rows, in
manifests, in logs, in issue comments, in commit messages, in evidence
artifacts. Local time on the machine that does the work, a literal `T`, seconds
precision, and the UTC offset always written out.

```
2026-09-06T17:16:48-0400
```

The offset is not optional. A naive `2026-09-06T17:16:48` is the format that
caused every problem below; it is indistinguishable from a UTC timestamp and
silently four or five hours wrong.

This is what both tools emit natively on this machine, with no post-processing:

```sh
date +%Y-%m-%dT%H:%M:%S%z
```

```python
from datetime import datetime

datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
```

### Always parse before comparing. This is a rule, not a preference.

A UTC timestamp sorts correctly as plain text. **A local one does not.** On
2026-11-01, `01:30:00-04:00` and `01:30:00-05:00` are an hour apart in real
time but sort in the wrong order as strings, and the wall-clock hour 01:00 to
02:00 occurs twice. Choosing local time buys readability — `17:16` reads as
five in the afternoon, which matters when correlating a run against what the
operator was doing — and it costs the string-sorting shortcut. Take the trade
and parse.

```python
from datetime import datetime

START = datetime.fromisoformat("2026-09-06T17:16:00-0400")
cur = [r for r in rows if datetime.fromisoformat(r["started"]) >= START]
```

```sh
now=$(date +%s)                       # compare as epoch, after resolving
```

Never compare `HH:MM`. Never compare a truncated timestamp. Never compare a
timestamp carrying one offset against one carrying another, or against one
carrying none.

### Why, with the three that bit us

**Two formats in the two files one readout joins.** `results-*.jsonl` carried
167 rows of naive local `started`/`finished`; the manifest beside it carried
UTC `started`/`ended` with a `Z`. Anything comparing a row against its manifest
entry was comparing EDT to UTC — four hours wrong, no error, no warning.

**A cutoff that fails open across midnight.** `targets_ab.sh` and
`strip_toggle_ab.sh` compared `$(date +%H:%M)` against an `HH:MM` string. At
00:30 against a 09:15 cutoff that comparison is false, so the guard never fires
and the batch runs on. `strip_toggle_ab.sh` **defaulted** to `09:15` — a
morning cutoff means an overnight batch, so the default configuration was the
broken case. That branch exists to enforce "a partial batch is no result", so
the guard failing open produces exactly the outcome it was written to prevent.
See #175.

**A readout filter that worked by luck.** `started >= "2026-09-06T17:16"`
compares a truncated string against full timestamps. It selected the right rows
only because every value in that file happened to be 19 characters with no
offset and no sub-second part. One row in another shape and rows join or leave
the batch silently.

All three are the same mistake: **a string comparison of timestamps is correct
only under assumptions nobody restates when they add the next caller.**

### Enforced, not asserted

`tests/test_iso8601_timestamps.py` fails on a data file whose timestamp fields
are not canonical, and on a shell script that compares `date +%H:%M` output as
a string. `scripts/backfill_iso8601.py` converts the existing naive and
`Z`-suffixed values; it runs once, and the test is what keeps them converted. A
convention that lives only in this document is a convention that drifts.

## A download is not verified until the files are on disk (2026-09-06)

`hf download` takes filenames positionally. Passing two of them after
`--include` makes the first the ignored include pattern and the second the only
file fetched:

```
UserWarning: Ignoring `--include` since filenames have being explicitly set.
```

One 171 GB request, one 86 GB file, no error. Fetch one file per invocation, and
**check the result against the recorded byte size and SHA-256 before treating a
download as done** -- a file of the right name and the wrong size is the failure
this catches, and a missing second file is the one that bit us.

The general rule: a long-running job that printed a warning and kept going has
not told you it succeeded. It told you it is still running.

## Absolute URLs in issue and PR comments (2026-09-06)

A relative link works in a repo markdown file and **404s in an issue or PR
comment**. The two are rendered with different base paths, so
a link written in the relative form -- square brackets, then `../tree/main/...`
in parentheses -- resolves against the comment's own URL and lands nowhere.

Write the full URL in any comment:

```
https://github.com/evanwtf/local-llm/tree/main/benchmarks/ds4/pr952-f309990-run1
```

Relative links stay correct inside `AGENTS.md`, `NEXT.md` and the rest of the
tree, where the base path is the file's own directory.

This cost an upstream maintainer a 404 on the raw data he had just asked for --
the worst place to spend a broken link, because the whole point of publishing the
CSVs is that he does not have to take our medians on trust. **Check a link by
fetching it** (`curl -s -o /dev/null -w '%{http_code}' <url>`) before posting a
comment whose value is the link.

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
load them -- see the retention rule. Excluding them from Time Machine is not
permission to prune them.

## Check the field before concluding something is not possible

[`SOURCES.md`](SOURCES.md) lists who to watch on X and how to sweep them safely.
Run it when a result looks anomalous, when planning a week, or when about to
conclude that something cannot be done on this hardware -- somebody may have
done it last Tuesday.

Two hard rules, both from mistakes:

- **Verify before repeating.** `uv run python scripts/verify_posts.py` on
  anything before it reaches an issue. grok has fabricated a post outright.
- **Date and version every claim.** Sources describing a tool from six months
  ago may describe several major versions back (#55).

## One definition of "done", in one place (2026-09-04, three times)

A check for "is this measurement run complete" was written three separate
times in one afternoon and got it wrong differently each time:

- **counting files.** A file being written already has a name and a header,
  so a run read as finished while `ds4-bench` was still filling its last one.
- **uniform row counts.** One CSV is trivially uniform, so a directory
  holding only `q4-rep1.csv` passed -- and the report then crashed, because
  an A/B needs two arms.
- **ignoring repetitions.** `q4-rep1` and `q8-rep1` are a complete pair by
  arms and by length, and one third of a three-rep run.

Each was fixed where it was found, which is why there were three. The
correct fix was `a03ca8d`: one function, `post_ab_run.is_complete`, with the
shell runner and the status monitor delegating to it. Two copies of a rule
drift, and the drift is invisible until something crashes unattended.

**Applies to any predicate that decides whether data may be used.** If it is
worth checking, it is worth having one owner.

## Name both directions of a ratio, every time (2026-09-04, twice)

`decode_ab_report.py` already carried a comment saying a bare `0.872` had
been misread the wrong way round once. Two new outputs were then added that
printed one direction only -- the per-rep line and the between-runs block --
and both were hit within the hour of being written. Reading four completed
runs meant inverting numbers by hand, which is the manual step that puts a
wrong figure in a comment.

Print `b/a` and `a/b` side by side, always. A ratio whose direction the
reader has to infer is a ratio that will be inferred backwards.

## Assert what a script does, not what it mentions (2026-09-04)

A test guarding "thermals must never change fan state" asserted the string
`fancontrol max` was absent from the module. The module's own docstring names
that command while explaining it is never called, so the test failed on its
own documentation.

Guarding tests should parse. The working version walks the AST for argv lists
beginning `fancontrol` and asserts every verb is `status`. The same shape
applies to any "this code must not do X" check: match the call, not the word.

## Check the exit status, not the tail (2026-09-01, twice)

**`uv run pytest -q | tail -2 && git commit` commits on a red suite.** The pipe
makes `tail`'s status the command's status, and `tail` succeeds whatever pytest
did. This is already recorded as a trap and it still happened **twice in one
session** -- the shape is too convenient to resist under time pressure.

Use one of these instead, and read the number:

```sh
set -o pipefail; uv run pytest -q 2>&1 | tail -2; echo "EXIT=$?"
uv run pytest -q                       # no pipe, no problem
```

A drift test firing is the system working. Committing through it is not.

## The testing set is written down (2026-09-01)

[`TESTING-SET.md`](TESTING-SET.md) lists the four axes -- hardware, client,
engine, model -- plus the task set, and marks which backends have valid current
data against which are configured and unmeasured. **Read it before adding a
variable**, and update it in the same commit that changes one; a test fails if a
live backend in `tasks.toml` is missing from it.

## Three engines, not four: LM Studio is retired (2026-09-01)

**llama.cpp, ds4 and Ollama.** Each earns its slot for a different reason --
llama.cpp is the fast pick, ds4 is the only engine that runs our one
independent lineage, and Ollama is the 31 GB entry point and the only path for
`ornith15`, `gemma4` and `qwen36coding`. Ollama is here on friction, not speed;
dropping it would delete the recommendation a newcomer actually follows.

**LM Studio is `retired` in tasks.toml.** Its runtime is llama.cpp underneath,
so on the same GGUF it cannot beat llama.cpp -- it can only add a layer, and it
does: identical UD-Q3_K_XL weights, identical client and tasks, **90s median
against 122s**, correctness identical. It was also the only backend recording
no server identity at all (#78).

**Retire, do not delete.** The config block stays in tasks.toml because 27 rows
in results.jsonl reference it, and those rows are unexplainable without the
sampler, context length and documented deviations that block records. `retired`
removes a backend from the default matrix; naming it with `--backend` still
runs it, so a retirement can be revisited without editing config back in.

**This does not narrow #60.** That issue is about engines we have never run.
This drops one we measured and found dominated -- the opposite operation.

## Measure OpenCode only, unless the run is about another client (2026-09-01)

**Default to `--client opencode` and nothing else.** Aider, Claude Code and
Codex are run only when the question is specifically about them — a client
defect, a parser fix, or a deliberate reference point — and the reason is
stated when they are.

This is a scope decision, not a finding. The client axis has been measured
enough to act on:

- The client is the dominant cost on a large local model. On one server, one
  session, `script-transform` took **11.1 s under Aider, 39.5 s under OpenCode
  and 189.6 s under Claude Code**, and the cause is prompt size — the client's
  own scaffolding, prefilled every turn.
- OpenCode is the one that fits the premise. An open model on an open engine
  driven by a proprietary client fails with the vendor.
- Aider is cheaper but does less: **22/34** inside a repository against
  OpenCode's **91/93**.

Continuing to sweep every client multiplies machine time across an axis whose
answer is already known and whose winner is fixed by the project's own
requirements. Spend the trials on models, engines and tasks instead.

**Consequences to keep in mind:**

- A Claude-Code-specific defect (#64) is now a **lower-priority** curiosity
  rather than something inflating numbers we publish, because we no longer
  publish Claude Code numbers.
- The hosted **Opus 5 reference** still has a use: establishing a new task
  class's ceiling, as it did for `script-transform`. That is "explicitly
  testing another agent" and is fine when said out loud.
- Historical multi-client rows stay in `results.jsonl` and stay valid. This
  changes what we run next, not what we already know.

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

### How to call OpenCode (2026-09-01)

**`opencode run` ignores the caller's working directory. Always pass `--dir`.**

```sh
opencode run --dir "$WORKTREE" --model "$MODEL" --format json --auto "$PROMPT"
```

`run` attaches to a **persistent server**, and that server works in the
directory *it* was started with. Setting `cwd=` on the child process is
correct, has always been correct in `run.py`, and has no effect. `run.py` now
refuses to build the argv without a worktree; `test_run.py` guards the flag,
its position, and the refusal.

**This is the most expensive class of bug this project has hit, so learn the
shape and not just the flag.** A missing `--dir` produces no error. The client
starts, reasons well, solves the task, writes a correct answer into the
server's directory, and exits 0. The oracle then finds no file and records a
model failure. **64 trials across three engines were published as evidence that
an open client was weak, when they measured our own invocation.** The corrected
cell went from **1/15 to 3/3**.

Two rules follow, and they generalize past OpenCode:

- **A client that scores far below its public reputation is a bug report until
  the cause is known.** 1/15 for a widely-used tool is not a finding. Read a
  failing transcript before publishing a number like that -- the bug was found
  in an *excluded* row, whose transcript named the file it had written and the
  wrong directory it had written it to.
- **Check that the harness and the client agree on where work happens.** `cwd`
  is a request, not a contract; any client with a daemon, a server, or a
  session can hold its own. Assert the workspace, do not assume it.

Whether OpenCode is good enough to be the tier-1 harness in practice is **still
open**: the corrected evidence is 3/3 on one cell of one model. #67 is the
re-measurement that answers it. Do not restate the old numbers, and do not
promote the new one past what three trials can carry (see "Know what a trial
count can support").

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

### Never write "N times faster" or "N times slower"

The comparative form hides which way the ratio runs. "4x faster" can be read as
four times the rate, four times *less* time, or faster *by* a factor of four,
and readers pick differently. "3x slower" is worse — it has no agreed meaning
at all.

**A multiplier is fine when it says what it multiplies.** The problem is the
comparative adjective, not the number.

| write this | not this |
|---|---|
| took **5.2x as long** | 5.2x slower |
| finished in **1/4 the time** | 4x faster |
| took **75% less time** | 4x faster |
| **193 s against 931 s** | 4.8x faster |
| Aider **11.1 s**, Claude Code **189.6 s** | Aider was 17x faster |

**A dispersion ratio is not a comparison and needs no rewriting.** "An 18x
spread on one task" is worst-over-best *within a single cell* — a statement
about how unstable one thing is, not a claim that A beats B. The `spread`
column in `RECOMMENDATIONS.md` stays as it is.

## The working loop

Five documents, each with one job. Keeping them in their lanes is what stops
this project turning into a pile of findings nobody can act on.

| document | holds | lifetime |
|---|---|---|
| **GitHub issues** | every piece of work, one per issue | until closed |
| [`NEXT.md`](NEXT.md) | the agenda: what order to work in | rewritten constantly |
| `benchmarks/*/RESULTS.md` | the numbers, and how they were obtained | append-only |
| [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) | the top 1-3 picks, and how to run them | replaced as evidence changes |
| [`docs/changelog.md`](docs/changelog.md) | what shipped, and why | append-only |
| [`docs/peer_agents.md`](docs/peer_agents.md) | how two agents share this repo and this machine | permanent |
| `AGENTS.md`, `CONVENTIONS.md`, `METHODOLOGY.md` | lessons that outlive the task | permanent |

**More than one agent works here.** Read
[`docs/peer_agents.md`](docs/peer_agents.md) before delegating anything or
before taking work from another agent. Three rules from it apply even if you
read nothing else: claim the machine before loading a model and treat a build
as machine work; sign GitHub comments with a trailing agent line (`--opus`,
`--deepseek`) while never putting a session URL or ID anywhere; and take agent
identity from `LOCAL_LLM_AGENT` / `LOCAL_LLM_MODEL` / `LOCAL_LLM_EFFORT` rather
than from what a model believes about itself.

**New work becomes an issue first.** Not a note in `NEXT.md`, not a TODO in a
comment. An issue carries its own reasoning and can be argued with; `NEXT.md`
only says what to do next, and it says it by number.

**`NEXT.md` sets order and nothing else.** Each issue must stand on its own, so
this file never restates one. It carries the ordered table and one snapshot of
the current machine state. The durable machine operations live in
[`docs/m5max-runbook.md`](docs/m5max-runbook.md); the traps live in this file.

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

**Two things exempt from pruning moved out of `NEXT.md` (2026-09-04).** "Traps
worth not rediscovering" now lives in this file, and the durable machine
operations in [`docs/m5max-runbook.md`](docs/m5max-runbook.md) — the queue file
carries only the ranked table and one machine-state snapshot, rewritten each
session. A trap leaves this file only when it becomes impossible — fixed in
code, or pinned by a test that would go red first. Prefer that to prose: an
entry that can be made mechanical should be.

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

**And he force-pushes the preview branches.** Our `ds4-glm53` worktree sat on
`a60a2a0 "Add GLM 5.3 Flash inference"`; the rewritten branch tip carried a
commit with the **same message and a different SHA** (`147109a`), and
`git merge-base --is-ancestor` said our old HEAD was **not an ancestor** of the
tip. So "14 commits behind" understated it — the history was rewritten, not
extended. **Check ancestry, not the count**, before assuming a rebuild is an
increment. A preview branch is not a stable base and may never be one.

**Read the commits, not the branch activity.** Of everything force-pushed
since our checkout, exactly two commits mattered to us — `b0c31af "Improve
GLM 5.3 attention memory and batching"` and `9f95d9f "Fix GLM 5.3 vision in
compact prefill"`, both touching the compact prefill path that
[ds4#890](https://github.com/antirez/ds4/issues/890) names. The rest was
vision and ROCm, out of scope here. Branch activity is a poor proxy for
progress.

**preflight reports GitHub notifications for `antirez/ds4`, `ggml-org/llama.cpp`
and this repo**, mentions first. It excludes `ci_activity` and keeps *read*
items: the ds4 mention that mattered arrived by email, was already marked read
through the API, and sat under 41 CI failures from unrelated repos. Filtering to
unread would have hidden the only notification worth seeing.

## Another Mac's result is a lead, not noise

**Do not dismiss a finding because it was measured on an M3 or M4.** Most
people building these engines are on M3/M4 hardware, so that is where a new
kernel, flag or scheduling change shows up first. The **mechanism** usually
transfers to M5 even when the **number** does not.

The right shape for such a note is *"X gained 30% on an M4; test the flag
here"*, never *"not our hardware"*. Three things genuinely do not carry over,
and they are narrower than they look:

- a configuration that **does not fit** in 128 GB — ask which quant does
- the **absolute figure** — thermal state alone moves ~4% (#58), and a 3-trial
  median carries ±28% (#23), so quote ratios and rankings
- **CUDA/ROCm kernels** — the quantization reasoning transfers, the kernels do not

Our own measurement is the bar for **publishing** a claim (#59). It is not a
filter on what is worth trying.

## Finishing a batch includes regenerating the derived documents

New rows in `results.jsonl` make `RECOMMENDATIONS.md` stale by definition — its
tables are a function of that file, and `test_recommendations.py` fails until
they are regenerated:

```sh
uv run python benchmarks/agent/splice_tables.py
```

The failing test is correct behavior, not noise. A document quoting a pass
rate the data no longer supports is exactly what this project has published
three times.

**While a batch is running the check skips**, because `results.jsonl` is
mid-write and the document is being compared against a moving target — an
unrelated commit should not be blocked by a run in flight. It is meaningful
only once the data is quiescent, which is why re-splicing belongs at the end of
a batch rather than during one.

**Check pytest's exit code, not the last line of its output.**
`pytest -q | tail -2 && git commit` always commits: `tail` exits 0 whatever
pytest did. That mistake put a red commit on main on 2026-09-01. Run the suite
as its own command and read the result, or use `set -o pipefail`.

## Stamp every line with the code that produced it

**Never call `logging.basicConfig` in this package. Call `provenance.configure()`.**
There is a test that fails if you do.

```
2026-09-01 07:08:35 INFO [c263902-dirty] ds4  excision  4/14  15/15
```

The bracketed field is the harness commit. **`-dirty` means the tree had
uncommitted changes**, so that line came from code that exists nowhere but the
machine that printed it and is not reproducible from any commit. A number
carrying `-dirty` may be used to decide what to do next; it may not be
published.

**Why this is not bureaucracy.** This project has published three separate sets
of figures that turned out to measure its own bugs, and in each case the
expensive part was not the bug — it was that nobody could tell which numbers
predated the fix. Rows in `results.jsonl` have carried provenance since
`273c499`; log lines, tool output and test reports carried none, and those are
what get pasted into issues and commit messages.

Concretely:

- **Every entry point** logs through `provenance.configure()`.
- **Every test session** prints the harness commit and a fingerprint of
  `results.jsonl` (`conftest.py` → `pytest_report_header`). The fingerprint is
  row count plus a content hash, so two runs over the same data are visibly
  the same run and an edited file is visibly not.
- **Generated documents** record the *data* fingerprint, not the commit.
  `RECOMMENDATIONS.md`'s tables are a function of `results.jsonl`; stamping
  them with a HEAD that changes on every unrelated edit would churn the file
  and train people to skim the diff.
- **When quoting a measurement anywhere** — an issue, a commit message, a
  comment upstream — carry the versions with it. `env` in each row already has
  `harness_head`, `ds4_head`, the client versions, `gguf_*` and
  `metal_ceiling_mb`. Quoting a wall time without them is how "13/27" survived
  two weeks.

**The absence cases have to be honest too.** Outside a git tree the stamp reads
`nogit`, never a blank or a plausible-looking sha. A wrong attribution is worse
than a missing one, because it is believed.

## Never put backticks in a `-m` message

`git commit -m "... `foo` ..."` and `git tag -m` run **command substitution**.
The shell executes what is inside the backticks, deletes it from the message,
and neither git nor the shell says anything. On 2026-09-01 a commit message
recording that a documented command had been verified came out as *"ran
against an empty directory"* -- the evidence for the claim removed itself, and
the stray `opencode run` actually executed.

**This is not only a git rule. `gh issue comment --body "..."` has the same
shape and it fired on 2026-09-03**, silently deleting two backticked identifiers
from a posted comment. Any command taking prose through a double-quoted shell
argument does command substitution: `gh issue comment`, `gh pr create --body`,
`gh issue create --title`. Use `--body-file` (and `-F body=@file` for the API),
or a quoted heredoc.

**Use `-F`**, which does no interpretation:

```sh
git commit -F - <<'EOF'
... `backticks` are safe here ...
EOF
```

The heredoc delimiter must be quoted (`<<'EOF'`, not `<<EOF`) for the same
reason. This is already the rule for release notes; it applies to every commit
and tag message, and single quotes around a command name are the cheap
alternative when a heredoc is overkill.

**The rule fires where it is most tempting to break: one variable.** On
2026-09-07 a `gh issue comment -F -` heredoc was left unquoted only so `$SHA`
would expand. It expanded `$(UNAME_S)` too, out of a quoted Makefile line, and
published `ifeq (,Darwin)` -- a sentence whose whole point was which branch of
that conditional a flag sits in. The published claim was still readable and
still wrong, which is the worst outcome available.

Wanting one substitution is never a reason to unquote the delimiter. Keep the
delimiter quoted and put the value in afterwards:

```sh
python3 - <<'PY'
import pathlib, subprocess
body = pathlib.Path("comment.md").read_text().replace("@SHA@", subprocess.run(
    ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip())
subprocess.run(["gh", "issue", "comment", "170", "-F", "-"], input=body, text=True, check=True)
PY
```

`$(...)`, `` `...` `` and `$VAR` are one hazard, not three. An unquoted heredoc
enables all of them, so a body containing a Makefile line, a shell snippet, a
price or a regex is at risk -- not only one holding backticks.

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

## Restart the model server between arms, and between trials while #112 is open

**A server that has been up for an hour is not the same server.** Measured
2026-09-03 across two arms of #77, three trials each, every trial a fresh
OpenCode conversation:

| | trial 1 | trial 2 | trial 3 |
|---|---|---|---|
| `qwen38fnds4shim` (MTP off) | 13/15 | 13/15 | **10/15** |
| `qwen38fnds4mtp7shim` (MTP 7) | 10/15 | 9/15 | **6/15** |

Both arms are worst in their third trial, and arm B declines monotonically
across a 90-minute session. Because each trial starts a new conversation, this
cannot be the model's context degrading -- it is state that outlives the
conversation, which leaves the server or the machine. **This is #112 and it is
not yet understood.**

Until it is:

- **Restart `ds4-server` between arms.** Always. An A/B where arm A ran on a
  cold server and arm B inherited ninety minutes of state is not a comparison
  of the two arms.
- **Prefer restarting between trials too** while #112 is open. It costs about
  10 s of warm-up against a 30-minute trial, and it is the cheapest way to stop
  a session-state effect being read as a property of a backend.
- **Give each engine configuration its own `--kv-disk-dir`.** A flag that
  changes the KV format makes the server reject the other configuration's
  checkpoints, so one arm re-prefills where the other got cache hits, and the
  only symptom is that it looks slower. MTP on/off is exactly such a pair:
  `~/.ds4/server-kv` is MTP-off, `~/.ds4/server-kv-mtp` is MTP-on.
- **Record it.** A row does not currently say how long the server had been up
  when it was produced, which is why this took two arms and six trials to see.

The restart itself is the argv in
[`docs/m5max-runbook.md`](docs/m5max-runbook.md), and
`wait_ready.py` is what tells you it is back -- not `/health`, which answers
before the model is loaded.

## A failing arm looks fast, so pair the tasks before comparing totals

**A failed trial is usually a short trial.** It dies on turn one, or the agent
gives up, and it contributes a small number to the arm's total wall time. So an
arm that fails more looks *faster* on any total or median taken over all rows,
and the effect is large enough to invert a comparison.

Measured on the #77 MTP arms, trial 1, same tasks and client (2026-09-03):

| comparison | arm A (MTP off) | arm B (MTP on) | reading |
|---|---|---|---|
| total wall, **all 15 rows** | 3074 s | 1726 s | "B takes 56% of the time" |
| total wall, **the 8 tasks that passed in both** | 1106 s | 1036 s | **B/A = 0.94** |

The first row is an artifact of arm B failing five tasks fast against arm A's
two. The second is the comparison worth having, and it is nowhere near the ~26%
that #23 requires before a suite difference is real.

**So: restrict a wall-time comparison to the tasks that passed in *both* arms**,
and say how many that was. A pass-rate difference is a separate finding and gets
reported separately -- never folded into a speed number.

The same run shows why the token-count check below it is not optional. Arm B's
throughput was genuinely better -- **66.0 s/1k output tokens against 107.9** --
and its wall time still barely moved, because it emitted **53% more tokens** for
the same eight tasks (15,704 against 10,247). Two real effects in opposite
directions, and either one quoted alone is misleading.

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

## American English spellings only (2026-09-05)

Write `behavior`, `favor`, `optimize`, `summarize`, `normalize`, `recognize`,
`judgment`, `defense`, `artifact`, `license`, `labeled`, `modeling`. Not the
British forms. This holds in Markdown, in code comments and docstrings, in
commit messages, and in issue and PR text.

**Why it needed writing down.** Nobody chose British spellings; they arrived by
drift. On 2026-09-05 the operator asked why the writing said "favour", and a
count found the repo already at 52 British forms against 19 American --
`behaviour` alone 25 times -- because an agent wrote most of these documents
and then read them back as house style. A convention nothing states is a
convention that ratchets in whatever direction the last writer happened to
lean.

**Two traps, both hit on the first attempt at fixing this.** A blanket
prefix substitution is wrong:

- `analys` -> `analyz` turns **analysis** into "analyzis". American keeps the
  `-sis` noun and takes the `-yze` verb: *analysis*, but *analyze*.
- `characteris` -> `characteriz` turns **characteristic** into
  "characteriztic". Same shape: *characteristic*, but *characterize*.
- `optimis` -> `optimiz` turns **optimistic** into "optimiztic".

Check every word a substitution introduces, one at a time. Do not trust the
rule.

**Do not rewrite text that is not ours.** Three categories are off limits, and
the first was already violated once:

- **Quotations.** A quoted post, a quoted model output, a quoted upstream
  comment. On 2026-09-05 a spelling pass rewrote a model's own words inside
  `LADDER2_FINDINGS.md` -- the quote said "expected behaviour of a flawed
  metric" and the transcript it quotes still does. Changing a quotation to suit
  our style makes it a misquote, which is the same defect as
  [Keep the historical record honest](#keep-the-historical-record-honest) one
  layer down.
- **Preserved evidence.** `logs/sweeps/` holds gather archives that
  verification ran against. They are records of what a source said, not our
  prose.
- **Archived snapshots.** `docs/archive/` is what a document said on a date.

Model transcripts under `benchmarks/**/ladder2/` are in the first category:
they are what the model wrote.

**Code identifiers are out of scope.** A local variable named `centre` is a
code change with no documentation benefit; leave it, or rename it deliberately
in its own commit.

## A figure needs its input named, not only its instrument (2026-09-04)

Our upstream correction on ds4#952 quoted "prefill median 0.999" with the
commit, the weights, their SHA-256, the frontier sweep and the repetition
count — everything about the *instrument* — and did not name the prompt.
Ninety minutes later @adamlawi showed the prompt is what decides that answer:
same box, same binaries, +2.5% with a 135 kB prompt and parity with a 405 kB
one, ~2.4 pp apart.

The figure was not wrong. It was under-specified, and no amount of care about
the engine would have caught it, because the missing variable was on the other
side.

So: **before quoting a number, ask what it is a number OF, and check that the
answer is on the row.** `scripts/prompt_meta.py` stamps `prompt_file` and
`prompt_bytes` onto every ds4-bench CSV, `decode_ab_report.py` puts the prompt
in the quotable line, and it refuses to pool prefill across two prompts.
Defaults are the trap here: `PROMPT` had a default nobody had to type, so
nobody wrote it down for four months.

## Record the client version; do not pin it (2026-09-04)

This laptop is a daily driver first and a benchmark rig second. Pinning the
agent clients means holding a developer's own tools back to serve a
measurement, and the operator's decision is the other way: **run the current
version of everything, and recover comparability afterwards.**

So `preflight.py` does three things and refuses at none of them:

- logs each client's installed version, and a **SERIES BOUNDARY** line naming
  any that moved since `client-versions.toml` was written;
- logs whether each client's self-update is on, with the switch that would
  turn it off — read out of the shipped binaries, so it is reproducible on a
  fresh install: `DISABLE_AUTOUPDATER` / `"autoUpdates": false` for Claude
  Code, `OPENCODE_DISABLE_AUTOUPDATE` / `"autoupdate": false` for OpenCode,
  `auto_update_enabled = false` for Codex. All three are deliberately **on**;
- **warns when a client is behind its release**, with the upgrade command. It
  never runs it: a harness that upgrades the tool under test moves the version
  mid-batch, which is the failure being avoided.

There was a refusal here between 2026-09-04 morning and evening. It was
correct for a dedicated rig and wrong for this one — it blocked a daily-driver
update, and it would have been overridden every time, which is a guard that
teaches people to type the override without reading it. **Do not restore it
without the operator asking.**

What makes the trade safe is the row, not the file. `results.write_row()`
**excludes any row that does not record `client_version`** — excluded rather
than refused, because losing an expensive trial to a missing field is worse
than storing one that can never enter an aggregate. The published tables then
caveat themselves through `client_caveat()`, which names which client measured
which rows and retires the note when one version covers everything again.
Prevention became recovery, on purpose.

## Check that both arms can run before planning the comparison (2026-09-04)

#138 looked like a straightforward requant A/B: our cell runs a Q4_0 build of
Qwen3.8-Flash-Next for ds4, the author replaced it with a Q4_K imatrix build,
so measure one against the other. Four runs, ~106 minutes, one command.

Three checks, each a few minutes, killed it:

- `ds4-metal ba01f5d` loads our old Q4_0 build and refuses the new one
  (`required metadata key is missing: deepseek4.block_count`).
- `ivan/qwen3.8-flash-next bd9cfbc` does the exact opposite, same error.
- `ds4-bench` appeared to refuse **both** with `required tensor is missing:
  per_layer_token_embd.weight`. **That one was my own error** and is the more
  useful half of this entry: `ds4-bench` does take `--ple`, undocumented and
  absent from `--help` (`ds4_bench.c:275 at ds4-main 9ab70534`). I had grepped the help output
  instead of the parser, and my failing invocation simply passed no sidecar.
  With the flag, both arms measure.

So the quant and the engine are welded together: any old-versus-new number
moves both, and the comparison is a **stack** comparison that must be reported
as one. **A control run is what proved that rather than assumed it** — the same
flags on our engine with our own weights work fine, which is the only thing
separating "these are incompatible" from "I typed the command wrong".

Two lessons, and the second is the one I actually needed.

**Before scheduling machine time, load both arms.** A model load is minutes;
the run it would have justified is hours, and a comparison discovered to be
impossible afterwards has cost the whole window.

**A flag missing from `--help` is not a missing flag.** Read the argument
parser before reporting a capability as absent. I published "ds4-bench cannot
measure this model" on two issues off a `--help` grep, and it was wrong in the
direction that cancels work — the expensive direction, because nobody re-checks
a capability that has been ruled out.

## A generated-token count equal to the cap is a truncation, not an answer (2026-09-04)

Comparing two builds of the same model on `ds4-eval`, the new one read 5/6
against the old one's 6/6. That fitted the story -- it was the build whose
author called the previous one "less accurate" -- and it was wrong. The failing
answer had generated **exactly 2500 tokens, the `--tokens` value I had
passed**. It was cut off mid-reasoning. At 8000 tokens it answers correctly and
both builds are 6/6.

The comparison was ready to publish. The only thing that caught it was the
number 2500 appearing in both the command and the result.

So: **whenever a graded run reports a failure, check the generated-token count
against the cap before recording it.** A budget you chose is a property of the
harness, not of the model, and it silently converts "slower to reason" into
"wrong" -- which is the most damaging substitution available, because the two
argue for opposite decisions.

The same run carries a second lesson, learned the same way. The new build's
totals showed ~5% more tokens for the same answers, and I published that as a
finding. The per-case ratios span **0.724 to 1.352** -- the spread is ten times
the effect, and five cases establish nothing. **A median is not a result until
it is larger than the spread around it**; `scripts/eval_trace.py` now prints
the spread beside its own median and says so when it isn't.

What survives is the variable, not the number: **a decode-rate gain is not a
session-time gain if the model talks more to get there**, so tokens-to-answer
belongs beside any tok/s claim -- measured, not asserted.

## A remedy that cannot be switched off cannot be measured (2026-09-04)

#112's remedy 2 shipped on 2026-09-03 and was still unmeasured a day later,
and the reason was not that nobody tried. Measuring it needs an arm with the
remedy **off**, turning it off meant editing the shim, and editing the shim is
not something an unattended run can do. So the experiment never got designed.

`SHIM_NO_STRIP=1` is that arm. The rule generalizes: **when a fix ships as a
behavior change in code the harness calls, give it a switch at the same
time.** The switch costs one `if`; retrofitting one costs the credibility of
every result taken in between, because nobody can say what the fix was worth.

Two details that make a toggle safe rather than a new hazard: it is opt-in and
truthy-checked, so a stray `SHIM_NO_STRIP=` in a shell profile cannot silently
disable a shipped remedy for every run afterwards; and it toggles **only** the
remedy -- the call XML is still removed, because leaving that in content is a
different defect and does not belong in the experiment.

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

**It happened again on 2026-09-03, with the varying parameter set by the client
rather than by us.** The ds4 Qwen shim measured **12/12 on synthetic prompts and
0/6 under OpenCode** on the same instruction text, and three sessions went into
varying the instruction. The two harnesses differed in `stream`: synthetic sent
`false`, OpenCode sent `true`, and **ds4's streaming path silently drops the
assistant text it has decided to return**. Interleaved, 12 samples each, one
identical request:

    stream:true    tool_calls 1/12   nothing at all 11/12
    stream:false   tool_calls 7/12   XML as text     5/12

That is the whole of a published **0/45**. The server log said `text_len=231`
while the client received zero bytes; nobody read the two together. **Diff the
actual request bodies between two arms before believing a difference between
them** — a control that differs in an unregistered variable is not a control,
and the tell is usually already in a log.

## Wait for a completion, never for /health

`/health` lies. On 2026-08-31 a llama.cpp server answered it with
`{"status":"ok"}` and HTTP 200 while every completion returned **503**, because
an 84 GB model was still being read off disk. A batch started on that signal
failed its smoke gate three times in the same second and reported a degraded
model.

`curl` compounds it: a 503 is a *successful* HTTP transaction, so
`curl -s .../health` exits **0** unless you pass `--fail`. A bare health check
is wrong twice over.

Use the poller, from a shell script or as an import:

```sh
uv run python benchmarks/agent/wait_ready.py \
    --base-url http://127.0.0.1:8020 --model qwen3.8-flash-next-q3
```

It sends a one-token completion every 5 s for up to 300 s and exits 0 only when
that succeeds. It logs `/health` alongside, because the **gap** between the two
is the diagnostic: health ok + completion 503 means "still loading", both
failing means "nothing is listening".

The general rule: **probe with the kind of request the benchmark will actually
send.** A status endpoint describes the server's opinion of itself.

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

`swift_excise.excise(path, symbol)` **writes the file.** It returns the removed
text, so calling it to *inspect* a span modifies the real working tree. Use
`body_source()` to look; only `run.py`'s worktrees should ever see `excise()`.

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
rows = results.usable(RESULTS)  # normalized, exclusions already dropped
rows = results.load(RESULTS)  # normalized, exclusions still marked
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
`load()` normalizes them in memory instead.

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

## Report results with the script, not by hand (2026-09-01)

```sh
uv run python scripts/report.py --backend gemma426
uv run python scripts/report.py --backend qwen --backend qwen36 --since 2026-09-01T20:50
```

**Hand-rolling this analysis has now produced two wrong answers in one
evening.** `gen_tables.load()` filtered on `is_excluded()` alone and counted 127
`--dry-run` control checks as failures in the published tables. And a
hand-computed comparison divided smaller-by-larger, reporting a **56% gap as a
36% reduction** and calling three real differences noise.

`scripts/report.py` calls `results.trials()` and `results.verdict()` and
nothing else, and applies #23's rule -- a 3-trial median carries +/-27.9%, so
two medians must differ by about **56%** before the gap is real. **The gap is
measured against the smaller median.** Both accessors and the threshold are
pinned by tests using the literal numbers that were got wrong.

Never read `row["passed"]` directly: a timeout carries `None` and is a failure,
not an absence.

## Never `pkill` `run.py` — restore through the harness (2026-09-03)

The harness *moves* the real reference checkouts aside to `<name>-real` and
puts a benchmark export in their place, restoring them from `atexit`. A
`pkill` skips the restore, so `~/git/gmail-archive` is left as the benchmark
tree at `benchmark: _date removed`, and the next run refuses to start with
`base_commit 56e55cc not found`. That refusal is the guard working — the
dangerous version is not noticing.

The fix is the harness's own function, never `mv` by hand:

```sh
cd benchmarks/agent && uv run python -c "import run; print(run.restore_targets())"
```

It reads `~/.local-llm-bench-stash.json`, is idempotent, and clears the marker.
Send `SIGINT` — or `kill` without `-9` — if a run must be stopped early.

Related, from a waiting shell: **do not poll
`pgrep -f 'benchmarks/agent/run.py'`**. The waiter's own command line matches
the pattern, so the loop never exits.

## MTP is not a speed-only flag (2026-09-03)

ds4's defaults do **not** preserve the sampling distribution: without
`--mtp-exact-sampling` it accepts drafts matching what the target would
greedily produce, biasing output toward greedy at any temperature above 0, and
`--mtp-margin` (default 3) tunes that acceptance. So an MTP-on/off difference
in **pass rate** is not attributable to speculation — the model is sampled
differently. Wall time is the cleaner comparison, and only if the token counts
match. Isolating speculation itself needs a third arm with
`--mtp-exact-sampling`; see "Pin the sampler, and vary one thing at a time" and
#39.

## A backend can be fast, correctly quantised, thermally fine — and unusable

The setup that scored 0/45 (before the shim's streaming fix) was doing
**40.2 t/s decode, 1107 t/s prefill**, 74.3 GiB resident with a 32 GB PLE
table streaming from SSD, 77.7 C die max. Every engine-level number was good
and the cell was worth nothing. Engine rates are a reason to test, never a
result.

## Sustained load drifts ~10% — bracket with A-B-A

Two identical `llama-bench` runs of the same binary, five minutes apart,
differed by **-0.25% at pp512 and -9.8% at tg128 @ d16384**. Shallow tests
barely move; deep-cache tests move a lot, which is what sustained GPU load
looks like on this machine. **Any A/B smaller than about 10% at depth is
unmeasurable here without bracketing or interleaving.** Run A-B-A and check
the two A legs agree before reading anything into B — a plain A-then-B would
have reported a 6% regression that does not exist.

## Coherence-check at temperature 0 before every benchmark

A model can load, serve, and report plausible token counts while emitting
noise — that is #25, and it cost hours. Check with
`scripts/coherence_check.sh` before any measurement batch, at temperature 0
where the output is deterministic enough to read.

## Nothing may feed `results.verdict()` except the oracle

Gates, hashes and the verbatim check ride alongside a verdict and never into
it. There is a test asserting a filthy solution and a clean one get the same
verdict. The moment a quality signal decides a pass, the harness is judging,
and its whole claim is that it does not.

## Three traps that cost work on 2026-09-07

**Never pass an issue or PR body through a double-quoted shell argument.**
`gh issue close -c "... `SHA` ..."` runs the sha as a command and posts the
comment with the sha silently deleted. Nothing errors; the comment looks fine
until you read it back. Always use `-F -` with a heredoc whose delimiter is
quoted, and pick a delimiter the body cannot contain.

`CLAUDE.md` already records this for `git tag -m`. It is the same trap and it
found a second door.

**Check the repo before running a queued measurement.** `NEXT.md` listed #162
Task 4 as pending. Four of its five knobs were; `gathered-heads` had been
settled over four runs in `724b70f` a day earlier and never posted to the
issue. The re-run then wrote into a **tracked, committed, VOID** directory --
`mkdir -p` does not clear one, so a rerun overwrites the reps it produces and
keeps the ones it does not (#208).

Two habits fall out of that, and the first is cheap:

- `git ls-files benchmarks/ds4/ | grep <thing>` before launching anything the
  queue calls pending. A tracked run directory is the answer.
- **The issue comment goes in the same turn as the commit.** A result that is
  committed but unpublished is invisible to exactly the reader most likely to
  redo it -- and a result that is published but whose issue stays open costs
  the same re-run from the other direction (#143 was answered on 2026-09-06 and
  still sat at P1 the next morning).

**The no-tests-during-a-benchmark rule is about this Mac.** CI runs on the
self-hosted Linux runner (`runs-on: [self-hosted, Linux, X64]`), so a PR can go
green there while the run lock is held here. Holding a peer back from pushing
costs idle time and buys nothing. What the rule forbids is `uv run pytest` and
`uv run ruff` **on this machine** while a batch holds the lock.

## Write findings for the operator, in our own issues. Do not draft upstream replies

**Operator instruction, 2026-09-07, and it supersedes what this section said
before:**

> Don't draft a report. Just make sure the evidence is in the repo and address
> the findings to me, not an outside/upstream contributor. And just comment
> directly in the local issue.

So the rule is short:

- **The audience of every issue comment is the operator.** Not the author of
  the upstream PR, not the person who asked on their tracker. Write it to the
  person who has to decide what this machine does next.
- **The evidence belongs in this repo** -- a committed `evidence/*.json`, the
  script that produced it, and its tests. A finding that lives only in a
  comment is not evidence, and one that lives only in a scratchpad is not even
  that.
- **Do not draft upstream replies.** Not as a deliverable, not "in case it is
  wanted". Whether anything goes upstream, and in whose words, is the
  operator's call and not a gap for an agent to fill.
- **Never post to a repository outside `evanwtf` or `evandhoffman`.** Unchanged,
  and now with nothing to draft it is simply the whole of the outward policy:
  read upstream, file here.

### What the earlier version got wrong

It said *"an upstream ask is not closed until the answer is upstream"*, and
made a drafted reply the deliverable whenever an upstream thread asked us
something. Written after ds4#952 sat unanswered for eighteen hours, which felt
like a lapse.

It is not our obligation. This project measures a machine for its operator;
being a responsive participant in someone else's thread is a thing the
operator may choose, not a duty the harness discharges on their behalf. The
earlier rule also produced work nobody asked for -- a full reply drafted on
2026-09-07 that was never wanted, and would have published numbers from a
commit the branch had already moved past.

Keep the one part that still holds: **note when an upstream thread bears on
our numbers**, on our own issue, in a sentence. That is context for the
operator, not a reply queued on their behalf.

## Never publish a ratio without the absolutes beside it

**Every A/B write-up carries the per-frontier absolute rates for both arms, not
only the paired ratio.** A ratio is a claim. The absolutes are what let a reader
on another machine check it, and what lets the ratio be recomputed if our
harness turns out to be wrong.

On 2026-09-07 three comments went onto #162 carrying ratios and frontier counts
alone -- on a thread where the maintainer had already asked for absolute t/s
once and been given it for the previous head. The numbers were sitting in
`decode_ab_report.py`'s own output the whole time and were dropped in
transcription.

Two artifacts hold this up, and neither is prose:

- `scripts/post_ab_run.py` posts `decode_ab_report.py`'s output **verbatim**,
  which already includes the per-frontier absolute table. Its docstring says
  why it exists: hand-composing the comment "is how a figure gets written from
  memory." **Use it for run posts.** Hand-writing a summary comment is fine;
  hand-transcribing the numbers into one is what went wrong.
- `test_the_per_frontier_block_carries_absolute_rates_not_only_the_ratio`
  guards the report itself, so the absolute columns cannot be quietly reduced
  to a ratio column later.

Say the run-to-run variation too. Run 3 of the #162 batch was 3-7% slower than
runs 1 and 2 on **both** arms at every frontier. That is the machine, and it is
the reason the design is paired and the ratio is the reported quantity -- and
the reason a single absolute number from this laptop must not be set beside a
single absolute number from someone else's box as though they were comparable.

## A pipe hides the exit code, and a refused run then looks like a finished one

A wrapper script that ends

```sh
uv run python benchmarks/agent/run.py ... 2>&1 | tail -80
echo "exit=$? $(date '+%F %T %Z')"
```

reports **`tail`'s** status, not the harness's. On 2026-09-07 a batch refused
to start -- a stale run lock from an earlier job -- and the driver printed
`exit=0`. The refusal text was in the output, three lines above a line saying
the run had succeeded.

That is worse than no status. The whole reason to print one is to be able to
trust it without re-reading the log, and this one is confidently wrong in the
direction that matters: a run that never happened reads as a run that did.

In zsh use `${pipestatus[1]}`; in bash `${PIPESTATUS[0]}`. Better, do not pipe
at all -- redirect to a file and `tail` it afterwards, so the status is the
command's own:

```sh
uv run python benchmarks/agent/run.py ... > "$LOG" 2>&1
status=$?
tail -80 "$LOG"
echo "exit=$status $(date '+%F %T %Z')"
```

**Related, and the reason this was caught at all:** the run lock refuses
rather than waits, and says so on stdout. A driver that swallows the status
turns a designed refusal into a silent no-op. `CSV-exists is a START
condition` is the same lesson from the other direction -- neither the presence
of an output file nor a zero status is evidence a run finished.

## A `tail -f` monitor never ends, so it outlives the thing it watched

On 2026-09-07 the operator asked why the session showed "10 running tasks"
while the GPU was idle. Four `tail -f` monitors were alive. Two had been
running for **5h42m and 9h43m** -- armed to watch the #190 KV matrix and the
#146 batch, both of which had finished the previous night. They were tailing
files nothing would ever append to again, and one had been reparented to pid 1.

The Monitor tool's own guidance says this plainly: an unbounded command
(`tail -f`, `inotifywait -m`, `while true`) stays armed until its timeout even
after the event has fired. It is the wrong shape for "tell me when this
finishes".

**Choose by how many notifications you need.**

- **One** -- use `Bash` with `run_in_background` and a command that *exits*
  when the condition holds:

  ```sh
  until grep -q 'done:' "$LOG"; do sleep 5; done
  ```

- **One per occurrence** -- a Monitor is right, but give the command a way to
  end: poll and `break` on a terminal state, rather than tailing forever.

`tail -f "$LOG" | grep -m1 PATTERN` does **not** fix it. If the log goes quiet
after the match, `tail` never gets SIGPIPE and the pipeline hangs anyway.

**Stop them with `TaskStop`, not `pkill`.** `TaskStop` takes the task id and
ends the harness task cleanly. `pkill -f tail` matches the watcher's own
command line -- the self-matching trap recorded above -- and killing the `tail`
alone leaves the task to report a confusing `exit 144`. Kill by explicit pid
when a task id is no longer in context, and expect that exit code.

**Sweep at the end of a batch.** A finished measurement should leave nothing
running: no monitor, no `tail`, and a released run lock. The count of running
tasks is a claim about the machine, and a wrong one is how an idle GPU and a
busy-looking session end up side by side.

### An `until` loop is not safe either: its condition can stop being reachable

The same sweep found **seven** more waiters, the oldest 3h10m old. These were
not `tail -f`; they were the recommended shape:

```sh
until grep -q "runs 2 and 3 finished" ~/bench-logs/162-gh23-driver.log; do
    sleep 30
done
```

That exits when the string appears. The string never appeared, because the run
producing it **was killed** two hours earlier -- deliberately, on finding it
was re-running a settled null (#208). The waiter had no way to learn that. It
polled a dead producer every 30 seconds for three hours.

**A wait needs a deadline and a failure condition, not only a success one.**
Poll for the thing that means *finished* and for the thing that means *gone*,
and give up after a bound:

```sh
deadline=$(( $(date +%s) + 3600 ))
until grep -q 'done:' "$LOG"; do
    kill -0 "$RUN_PID" 2>/dev/null || { echo "producer $RUN_PID is gone"; exit 1; }
    [ "$(date +%s)" -lt "$deadline" ] || { echo "timed out"; exit 1; }
    sleep 30
done
```

**Two of the seven would have started work on firing** -- one chained a
post-run analysis, another waited on the run lock and then launched a full
analysis. A stale chained job that fires hours later, against a machine that
has moved on, is worse than one that hangs: it takes the lock and runs a
measurement nobody asked for.

**Whenever a run is killed, kill what was waiting on it in the same breath.**
Stopping the producer and leaving its waiters is how a session accumulates
work it cannot account for.

### The third failure: a success marker the task never prints

Found by the deepseek peer on 2026-09-07, in its own session, after 94 shells
had accumulated over 21 hours. Every one of them ran the same wait against the
**same already-completed** task file:

```sh
until grep -qE "frontier|prefill_tps|tps|FAIL|error|Error" .../b6fba60c8.output
do sleep 5; done
```

The task had exited **0**. Its output was a `ds4-bench` run printing "memory
detail", "context buffers", "context 262145 exceeds..." -- and not one of the
six patterns appears anywhere in it. So the wait hung *because the task
succeeded quietly*.

This is the worst of the three, because the other two announce themselves. An
unbounded `tail -f` looks wrong on sight. A dead producer can be detected. A
grep for a marker the task never emits fails **identically on success and on
failure**, and the only symptom is a shell that never returns.

Two things prevent it:

- **Match the task's own terminal line, not a marker you hope it prints.** The
  harness writes `[exited with code N]` at the end of every background task's
  output file; that is the trailer to grep for. A domain marker (`frontier`,
  `done:`) is a *bonus* signal, never the termination condition.
- **The `kill -0` producer check above would have ended all 94 on the first
  poll.** That rule was written the same morning and argued theoretically;
  this is what it looks like when it is missing. Poll for the producer being
  gone even when you are confident the success string is right -- especially
  then, because you will not notice you were wrong about it.

Sweeping the shells is the smaller half. The peer reported the count before
and after (94 -> 0) *and* the cause, which is why the fix is a rule here rather
than a cleanup nobody learns from.

## Check a peer every 20 minutes, and check what it is DOING

**Operator rule, 2026-09-07.** When you are managing a peer agent, check its
status every twenty minutes and confirm it is working. Idle is acceptable only
when idle is what you asked for.

Both failures that produced this rule were invisible from here:

- The peer was **blocked for hours on a permission prompt** its own session had
  raised. Nothing surfaces that to a peer. I learned it from the operator.
- The peer **pushed at 09:54, CI failed at 09:55, and it sat for 47 minutes.**
  Nothing tells an agent its CI went red. It had finished its turn and stopped,
  believing the work delivered.

Neither was the peer's fault and neither showed up in its messages, because in
both cases it had nothing to report.

**A check is "what is it doing right now", not "did it reply".**

```sh
top -l 2 -pid <peer-pid> -stats pid,cpu,command | tail -3   # live CPU
ps -eo ppid | awk '$1==<peer-pid>' | wc -l                  # child processes
gh run list --repo <repo> --branch <its-branch> --limit 1 \
  --json conclusion,createdAt                               # did its push pass?
```

`ps`'s `%CPU` column is a **lifetime average** and reads `0.0` for a session
that has been alive for a day, so it cannot tell a busy peer from an idle one.
Sample with `top -l 2` instead.

**Also check that your own constraints are not over-blocking it.** Told not to
run tests on a machine holding a measurement, the peer inferred it should not
work at all and idled for two hours. The constraint was mechanical -- no local
test runs, no commits to the pinned worktree -- and left every code change it
had open perfectly workable in its own worktree against CI on another host. Say
what is blocked and what is not, and when a peer holds back further than you
meant, that is a fault in the instruction.

Related: the same asymmetry runs the other way. A peer that pushes back with a
mechanism is usually worth believing -- see the route-field correction on the
same day, where the peer was right and I was wrong twice.

## A branch named after a machine is permanent infrastructure (2026-09-07)

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

## Never resolve a data file by taking the union of two row sets (2026-09-07)

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

**So resolve it as `theirs` plus the rows that are genuinely new**, and then
re-run every archiver the repo owns before committing:

```sh
uv run python scripts/archive_pre_dir_rows.py     # idempotent; says what it moved
uv run python benchmarks/agent/splice_tables.py   # tables go stale the moment rows move
uv run pytest -q                                  # the invariant tests are the check
```

**And do not trust CI to catch it.** The tests that assert invariants of the
ledger are guarded on `HAS_LOCAL_RESULTS`, which is
`results.default_path().exists()` — a path derived from the RUNNER's own
hardware by `scripts/hardware_id.py`. On any machine that is not the one that
took the measurements, the directory does not exist and every one of those
tests skips. CI was green on the branch that carried the 90 rows. See #218.

The instruction that caused this was mine, given to a peer, and the peer
followed it exactly and verified the union carefully. A merge rule for a data
file has to name what may be *missing on purpose*, or it is not a rule.

---

## Cutting a release (2026-09-07)

`v1.0.0` is the first tagged release. The procedure is four steps, and two of
them are enforced by a gate that runs on the tag push:

```sh
# 1. bump the version
$EDITOR pyproject.toml                       # version = "X.Y.Z"

# 2. write the section BEFORE tagging
$EDITOR docs/changelog.md                    # ## vX.Y.Z — YYYY-MM-DD

# 3. land it
uv run pytest -q && git commit && git push

# 4. tag, and nothing else
git tag -a vX.Y.Z -m "vX.Y.Z" && git push --tags
```

**Never write release notes into the tag message.** `git tag -m` runs its
argument through the shell, so backticks are expanded and the text they
surround is deleted with no error. This repo's prose is full of backticks —
every model name, every path, every flag. `.github/workflows/release.yml`
generates the notes from `docs/changelog.md` instead, and a test asserts the
workflow keeps using `--notes-file`.

**Two gates refuse a bad release rather than warning about one:**

- `scripts/check_release_version.py vX.Y.Z` refuses a tag that disagrees with
  any declared version. It *finds* the declarations — `pyproject.toml` today,
  plus any `__version__` — rather than reading a hard-coded list, so a second
  declaration added later cannot silently escape the check.
- `scripts/release_notes.py vX.Y.Z` refuses a version with no section in the
  changelog. An empty release note is worse than a missing release: it reads
  as trivial work rather than undocumented work.

`tests/test_release.py` weights the **negative** cases, because these scripts
run unattended and refusing correctly is the whole job. A gate that passes
everything looks exactly like a gate that works.

**Two drift guards run on every commit, not just on a tag.** One asserts the
version declared in `pyproject.toml` already has a changelog section, so a
bump without notes fails at the commit that made it rather than at the tag
push a day later. The other asserts the workflow still calls both scripts — a
workflow that stops calling a gate has deleted it, and nothing else notices.

**`${{ runner.temp }}` in a workflow-level `env:` is not a visible error.**
The `runner` context does not exist there — only inside a step. GitHub creates
the run, fails it instantly with *"This run likely failed because of a workflow
file issue"*, and writes no logs, so `gh run view --log-failed` answers `log not
found`. That is what the first `v1.0.0` tag push hit. Set `UV_CACHE_DIR` from a
step that appends to `$GITHUB_ENV`, the way `test.yml` sets it at step level,
and `test_no_workflow_uses_the_runner_context_outside_a_step` keeps it that way.

Entries in `docs/changelog.md` carry a `## vX.Y.Z` heading from v1.0.0 onward.
The ~1600 lines below that section predate versioning; they are delimited by
bold date lines and were left alone.
