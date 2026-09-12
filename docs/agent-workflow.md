# The working loop

How an agent works in this repo: which document holds what, git and issues,
timestamps and spelling, managing a peer, and cutting a release. **Read the
relevant section before you file an issue, commit, branch, manage a peer agent,
or tag a release.** More than one agent works here, one per machine; the
five-document loop is what keeps their output something a person can act on.

The short forms live in [`../AGENTS.md`](../AGENTS.md).
[`peer_agents.md`](peer_agents.md) covers how two agents share this repo and
this machine — read it before delegating or taking work from another agent.

---

## Confirm your machine is one we manage before you compare (2026-09-11)

This project measures a fixed set of machines, listed in
[`../hardware/MACHINES.md`](../hardware/MACHINES.md) and defined once in
`scripts/machines.py`. Anyone may run this code. Most will not be on one of our
machines, and the published numbers come from ours.

Check which case you are in before you compare a result:

```sh
uv run python scripts/machines.py --check
```

It prints the machine when the hardware matches the registry, and exits 1 with
a warning when it does not. A machine that is not in the registry still runs the
code. Its numbers do not match the published ones: a close card is not the same
card, and a 3090 is not our 3080 Ti.

The machine label is the slug from `scripts/hardware_id.py`. A machine finds its
own issues with its own label:

```sh
gh issue list --label "hardware:$(uv run python scripts/hardware_id.py --slug)"
```

The machine label is the slug under a `hardware:` namespace; the slug itself
stays the bare identity (`--slug` prints it). `platform:macOS` and
`platform:Nvidia` are broad class tags under the `platform:` namespace (#302).
They do not identify a machine:
the RTX 3080 Ti desktop and the DGX Spark are both `platform:Nvidia`. An issue that
consumes a machine's time carries one machine label as well.

Name the machine in the third person in every record -- an issue, a commit, a
results document, a sweep. Do not write "this machine", "this box", or "here".
Three agents read this repo, one per machine, and a relative term points at a
different machine for each reader (#302).

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

### Log lines too -- they were the exception until 2026-09-09

This section has said "in logs" since it was written, and until 2026-09-09 the
logs were the one place it was not true. Python's default `asctime` is

```
2026-09-09 07:00:12,481          the default: a space, a comma, no offset
2026-09-09T07:00:12-0400         what every row and manifest here carries
```

A space where ISO 8601 wants `T`, a comma where it wants a dot, and no offset
at all -- the exact shape the rest of this section exists to forbid. It
mattered because log lines get joined to rows by hand: *the server said X at
07:00:12, which row was that?* is a question you cannot answer from a
timestamp with no zone.

**`scripts/lib/logs.py` owns the format, and nothing else may call
`logging.basicConfig`.** `tests/test_logging_format.py` fails on any tracked
file that does, and asserts against a line the logger actually wrote -- not
against the format constant, because a constant can be right while the handler
never uses it.

```python
import logs

logs.configure()  # a driver: time, module, level, message
logs.configure(fmt=logs.PLAIN)  # a report: the table, and nothing else
```

`PLAIN` is the one exemption and it is narrow. Several scripts render a
markdown table through the logger, because `print` is forbidden; a timestamp
on every row makes the table unpastable. `PLAIN` carries **no** timestamp, so
there is no second date format -- an exemption from stamping, not from ISO
8601.

`force` defaults to False, as `basicConfig`'s own does. Setting it True tears
down the root logger's handlers, and under pytest one of those is `caplog`'s:
defaulting it True failed 50 tests at once, every one of them asserting on a
message its script had written correctly to a handler that had just been
removed.

(The harness-commit stamp that rides on top of this format is
[Stamp every line with the code that produced it](measurement-discipline.md#stamp-every-line-with-the-code-that-produced-it).)

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

## Check the field before concluding something is not possible

[`../SOURCES.md`](../SOURCES.md) lists who to watch on X and how to sweep them safely.
Run it when a result looks anomalous, when planning a week, or when about to
conclude that something cannot be done on this hardware -- somebody may have
done it last Tuesday.

Two hard rules, both from mistakes:

- **Verify before repeating.** `uv run python scripts/verify_posts.py` on
  anything before it reaches an issue. grok has fabricated a post outright.
- **Date and version every claim.** Sources describing a tool from six months
  ago may describe several major versions back (#55).

## The five-document working loop

Five documents, each with one job. Keeping them in their lanes is what stops
this project turning into a pile of findings nobody can act on.

| document | holds | lifetime |
|---|---|---|
| **GitHub issues** | every piece of work, one per issue | until closed |
| [`../NEXT.md`](../NEXT.md) | the agenda: what order to work in | rewritten constantly |
| `benchmarks/*/RESULTS.md` | the numbers, and how they were obtained | append-only |
| [`../RECOMMENDATIONS.md`](../RECOMMENDATIONS.md) | the top 1-3 picks, and how to run them | replaced as evidence changes |
| [`changelog.md`](changelog.md) | what shipped, and why, one `## vX.Y.Z` section per release | append-only |
| [`history.md`](history.md) | the same record before v1.0.0 | frozen 2026-09-07 |
| [`peer_agents.md`](peer_agents.md) | how two agents share this repo and this machine | permanent |
| `AGENTS.md`, `CONVENTIONS.md`, `METHODOLOGY.md` | lessons that outlive the task | permanent |

**More than one agent works here.** Read [`peer_agents.md`](peer_agents.md)
before delegating anything or before taking work from another agent. Three
rules from it apply even if you read nothing else: claim the machine before
loading a model and treat a build as machine work; sign GitHub comments with a
trailing agent line (`--opus`, `--deepseek`) while never putting a session URL
or ID anywhere; and take agent identity from `LOCAL_LLM_AGENT` /
`LOCAL_LLM_MODEL` / `LOCAL_LLM_EFFORT` rather than from what a model believes
about itself.

**New work becomes an issue first.** Not a note in `NEXT.md`, not a TODO in a
comment. An issue carries its own reasoning and can be argued with; `NEXT.md`
only says what to do next, and it says it by number.

**`NEXT.md` sets order and nothing else.** Each issue must stand on its own, so
this file never restates one. It carries the ordered table and one snapshot of
the current machine state. The durable machine operations live in
[`m5max-runbook.md`](m5max-runbook.md); the traps live in the topic docs.

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
worth not rediscovering" now live in the topic docs, and the durable machine
operations in [`m5max-runbook.md`](m5max-runbook.md) — the queue file
carries only the ranked table and one machine-state snapshot, rewritten each
session. A trap leaves the docs only when it becomes impossible — fixed in
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
marker saying what replaced it. See
[Keep the historical record honest](../CONVENTIONS.md#keep-the-historical-record-honest).

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

When you commit the regenerated tables, **read pytest's exit code, not the last
line of its output** — `pytest -q | tail -2 && git commit` always commits and
has put a red commit on main. The full trap is
[Check the exit status, not the tail](automation-hazards.md#check-the-exit-status-not-the-tail-2026-09-01-twice).

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
  [Keep the historical record honest](../CONVENTIONS.md#keep-the-historical-record-honest) one
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

## Before running a queued measurement, check the repo (2026-09-07)

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

The third trap of that same day -- passing an issue or PR body through a
double-quoted shell argument, so `gh issue close -c "... `SHA` ..."` runs the
sha as a command -- is [Never put backticks in a `-m` message](automation-hazards.md#never-put-backticks-in-a--m-message).

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

**`docs/changelog.md` holds releases; `docs/history.md` holds everything
before them.** The changelog was 1652 lines on 2026-09-07 — one `#` heading and
~1590 dated entries with no versions to attach them to. Splitting it was not
tidying: a section that runs to end-of-file becomes the release notes, and the
first attempt at v1.0.0 produced 102,607 bytes of them.

So each file now has one job, and four tests hold the line:

- an entry in the pre-1.0 format (`**YYYY-MM-DD`) in `changelog.md` fails,
- a `## vX.Y.Z` heading in `history.md` fails,
- an entry dated after 2026-09-07 in `history.md` fails — the file is frozen,
  and appending to the bottom of 1600 lines is what habit does,
- a version section that does not end before the next one fails, because one
  missing `---` takes every section below it into the notes.

Write a new entry under the version it ships in, and end it with a `---`.
