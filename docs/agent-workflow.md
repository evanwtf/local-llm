# The working loop

How an agent works in this repo: which document holds what, git and issues,
timestamps and spelling, managing a peer, and cutting a release. **Read the
relevant section before you file an issue, commit, branch, manage a peer agent,
or tag a release.**

The short forms live in [`../AGENTS.md`](../AGENTS.md).
[`peer_agents.md`](peer_agents.md) covers how agents share the repo and a
machine. Read it before you delegate work or take work from another agent.

---

## Confirm your machine is one we manage before you compare (2026-09-11)

This project measures a fixed set of machines, listed in
[`../hardware/MACHINES.md`](../hardware/MACHINES.md) and defined once in
`scripts/machines.py`. Anyone may run this code, but the published numbers come
from our machines. Check which case you are in before you compare a result:

```sh
uv run python scripts/machines.py --check
```

It prints the machine when the hardware matches the registry. It exits 1 with a
warning when it does not. An unregistered machine still runs the code, but its
numbers do not match the published ones: a close card is not the same card.

The machine label is the slug from `scripts/hardware_id.py` under the
`hardware:` namespace. A machine finds its own issues with its own label:

```sh
gh issue list --label "hardware:$(uv run python scripts/hardware_id.py --slug)"
```

`platform:macOS` and `platform:Nvidia` are broad class tags (#302). They do not
identify a machine. An issue that uses a machine's time also carries one
machine label.

Name the machine in the third person in every record: an issue, a commit, a
results document, a sweep. Do not write "this machine", "this box", or "here".
Each machine has its own agent, so a relative term points at a different
machine for each reader (#302).

## An issue is a public work log

An issue holds **what we did and what we measured**, in the order it happened.
Write each comment so it still reads as a result a month later: what ran, on
what tree, how many runs, the numbers, and what was thrown away.

- **Address findings to the operator** (operator, 2026-09-07). The operator
  decides what a machine does next. Not the upstream PR author.
- **Do not draft upstream replies**, in the issue or anywhere else. Whether
  anything goes upstream, and in whose words, is the operator's call. Drafts
  crowd out the log: #162 reached twelve comments, five of them versions of one
  unsent reply.
- **Never post to a repository outside `evanwtf` or `evandhoffman`.** Read
  upstream, file here.
- **Link the upstream issue once**, in the opening post. When an upstream
  thread bears on our numbers, note it in one sentence on our issue.
- **Put the evidence in the repo**: a committed `evidence/*.json`, the script
  that produced it, and its tests. A finding that lives only in a comment is
  not evidence.
- Do not post the periodic status update to an issue. It goes in chat
  ([`../AGENTS.md`](../AGENTS.md), *Writing it down*).

## Every time and date is ISO 8601, America/New_York, with an explicit offset (2026-09-06)

**One representation everywhere: `YYYY-MM-DDTHH:MM:SS±hhmm`.** This applies to
rows, manifests, logs, issue comments, commit messages and evidence. Use local
time on the machine that does the work, a literal `T`, seconds precision, and
the UTC offset.

```sh
date +%Y-%m-%dT%H:%M:%S%z                                 # 2026-09-06T17:16:48-0400
```

```python
datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
```

A naive `2026-09-06T17:16:48` looks the same as a UTC timestamp and is silently
four or five hours wrong.

### Always parse before comparing

A local timestamp does not sort correctly as text. On 2026-11-01,
`01:30:00-04:00` and `01:30:00-05:00` are an hour apart but sort in the wrong
order, and the hour from 01:00 to 02:00 occurs twice. Local time buys
readability when you correlate a run with what the operator was doing. It costs
the string-sort shortcut, so parse:

```python
START = datetime.fromisoformat("2026-09-06T17:16:00-0400")
cur = [r for r in rows if datetime.fromisoformat(r["started"]) >= START]
```

Never compare `HH:MM`, a truncated timestamp, or two timestamps with different
offsets (or one with none). Each of these has bitten us: a row file in naive
EDT joined to a manifest in UTC (four hours wrong, no error); a shell cutoff
that compared `$(date +%H:%M)` to `09:15` and failed open after midnight
(#175); and a readout filter on a truncated string that worked only because
every value had the same length.

`tests/test_iso8601_timestamps.py` fails on a data file with a non-canonical
timestamp, and on a shell script that string-compares `date +%H:%M`.
`scripts/backfill_iso8601.py` converted the old values once.

### Log lines

**`scripts/lib/logs.py` owns the log format, and nothing else may call
`logging.basicConfig`.** Python's default `asctime` (`2026-09-09 07:00:12,481`)
has a space, a comma, and no offset, so you cannot join a log line to a row.
`tests/test_logging_format.py` fails on any tracked file that calls
`basicConfig`. It asserts against a line the logger actually wrote, not the
format constant.

```python
import logs

logs.configure()  # a driver: time, module, level, message
logs.configure(fmt=logs.PLAIN)  # a report: the table, and nothing else
```

`PLAIN` carries no timestamp at all, so a markdown table rendered through the
logger stays pasteable. It is not a second date format.

`force` defaults to False. True removes the root logger's handlers, including
pytest's `caplog` handler: a True default failed 50 tests at once.

The harness-commit stamp on top of this format is
[Stamp every line with the code that produced it](measurement-discipline.md#stamp-every-line-with-the-code-that-produced-it).

## Absolute URLs in issue and PR comments

A relative link works in a repo markdown file and **404s in an issue or PR
comment**, because the comment resolves it against its own URL. Write the full
URL in a comment:

```
https://github.com/evanwtf/local-llm/tree/main/benchmarks/ds4/pr952-f309990-run1
```

**Fetch a link before you post a comment whose value is the link**
(`curl -s -o /dev/null -w '%{http_code}' <url>`). A broken link once cost an
upstream maintainer the raw data he had asked for.

## Check the field before you conclude something is not possible

[`../SOURCES.md`](../SOURCES.md) lists who to watch and how to sweep them. Sweep
when a result looks anomalous, when you plan a week, or before you conclude
that something cannot be done on a machine.

- **Verify before you repeat a post.** Run `uv run python scripts/verify_posts.py`
  on anything before it reaches an issue. grok has fabricated a post outright.
- **Date and version every claim.** A six-month-old source can describe a tool
  several major versions back (#55).

## The five-document working loop

| document | holds | lifetime |
|---|---|---|
| **GitHub issues** | every piece of work, one per issue | until closed |
| P0–P3 labels (`scripts/make_next.py --platform {macos,nvidia}`) | the agenda, per machine | live (#463 retired the committed `NEXT.md`) |
| `benchmarks/*/RESULTS.md` | the numbers, and how they were obtained | append-only |
| [`../RECOMMENDATIONS.md`](../RECOMMENDATIONS.md) | the top 1–3 picks, and how to run them | replaced as evidence changes |
| [`changelog.md`](changelog.md) | what shipped, and why, one `## vX.Y.Z` section per release | append-only |
| [`history.md`](history.md) | the same record before v1.0.0 | frozen 2026-09-07 |
| `AGENTS.md`, `CONVENTIONS.md`, `METHODOLOGY.md` | lessons that outlive the task | permanent |

**New work becomes an issue first.** Not a note in a doc, not a TODO in a
comment. An issue carries its reasoning and can be argued with.

**The labels set order and nothing else:** P0 before P1, then by issue number,
per platform. Machine operations live in [`m5max-runbook.md`](m5max-runbook.md)
and [`dgx-spark-runbook.md`](dgx-spark-runbook.md); traps live in the topic
docs. A trap leaves the docs only when code or a test makes it impossible.

**Every open issue carries these labels.** `scripts/audit_labels.py` checks
them, and the issue-sweep skill runs it.

| label | how many | what it decides |
|---|---|---|
| `P0`–`P3` | exactly one | the order in the queue |
| `platform:macOS`, `platform:Nvidia` | one or both, when the work costs machine time | which machine's queue shows it |
| `hardware:*` | one per machine the work runs on | which machine; the DGX lane is `hardware:Cortex-X925-GB10-x2` (the single-Spark label is retired) |
| `bug`, `enhancement`, `documentation`, `question` | at least one, for repo work with no platform | what kind of work it is |
| `model:*`, `engine:*` | any number; required when the title names one | the family, so one query finds a model's history (#465) |

The family labels name a family, not a build: `model:qwen`, `model:glm`,
`model:deepseek`, `model:nemotron`, `model:gptoss`, `model:gemma`,
`model:llama`, `model:minimax`, `model:other`; `engine:vllm`,
`engine:llamacpp`, `engine:dwarfstar` (antirez's ds4 engine, not the DeepSeek
family), `engine:mlx`, `engine:ollama`, `engine:sglang`, `engine:trtllm`,
`engine:exl3`, `engine:tensorfold`. The quant and revision go in the body.

**Close the loop the same day you finish:**

1. **Comment on the issue** with what you found, including what contradicts the
   issue's own premise. #26 blamed a KV cache and the data refuted it.
2. **Close it.** A finished issue left open makes the agenda lie.
3. **Give the lesson a permanent home**: a topic doc, a runbook, or a test. A
   finding that lives only in a closed issue has not landed.

**Results go to the `RESULTS.md` for the area that produced them.** Raw rows in
`results.jsonl` are the record. `RESULTS.md` is the narrative over them, and
**corrections are added, not substituted**: a superseded finding stays visible
with a marker. See
[Keep the historical record honest](../CONVENTIONS.md#keep-the-historical-record-honest).

**`RECOMMENDATIONS.md` answers one question: what should someone run today?**
Its top table holds one to three pairings, a model *and* a client, with the
commands to start each. When new evidence moves the table, say what it used to
say and why it moved.

## Branches, pull requests, and the merge gate

**Branch per piece of work, open a PR, and let CI merge it.** `main` is
branch-protected and **agents never push to it directly**. No human approval is
required: the required `pytest` check is the whole gate.

```sh
git checkout -b <kind>/<issue>-<slug>     # docs/24-..., analysis/26-..., tasks/4-...
git push -u origin <branch>
gh pr create --fill
gh pr merge <n> --auto --squash           # merges when `pytest` is green; deletes the branch
```

- **The branch must be up to date with `main`** (`strict`). A
  `non-fast-forward` rejection is that rule or a race.
- **Squash every PR** (operator, 2026-10-27). The repo allows no other merge
  method.
- **Protection is a guardrail, not a permission boundary.** Every agent and the
  operator push as the same admin account, and `enforce_admins` is off, so
  GitHub does not stop a mistaken push to `main`. Never push to `main` and never
  bypass a failing check. Only the operator decides to override.
- **If auto-merge stalls, read the check.** A red `pytest` or a branch behind
  `main` is the usual cause.
- **Rebase a stacked branch after its parent merges.** The squash put the
  parent on `main` as one new commit, so replay only the child's commits:
  `git rebase --onto origin/main <parent-branch-tip> <child-branch>`. A plain
  `git rebase origin/main` replays the parent too and conflicts with its
  squashed copy. Merge bottom-up.

### A live run starts only from a commit on `main` (#1281)

A row's `harness_head` answers "which harness produced this". The sha must stay
an ancestor of `main`, or `git gc` collects it and the provenance dangles. A
rebase after a run started orphaned 65 GB10 rows this way (#355). A squash
merge drops every branch commit, so a run from a branch has the same defect.

`run.py` checks that `HEAD` is `origin/main` or one of its ancestors
(`provenance.on_main`) before it writes a row, and refuses otherwise. **Merge
the PR first, then run from `main`.** `--dry-run` runs no agent and marks its
rows, so it may run from a branch. The check reads the local `origin/main`: if
it refuses a commit that is on `main`, run `git fetch` and retry.

Rows from before the squash policy are safe: the merge commits still hold their
shas. `dirfix.REBASED_AFTER_FIX` maps the #355 orphans to the reachable commit
each was rebased into, so the rows classify correctly while `harness_head`
stays as written.

## Before you run a queued measurement, check the repo

A queue entry can be stale. #162 Task 4 was listed as pending after one knob had
been settled in `724b70f` and never posted. The rerun then wrote into a tracked
VOID directory, and `mkdir -p` does not clear one (#208).

- Run `git ls-files benchmarks/ | grep <thing>` before you launch anything the
  queue calls pending. A tracked run directory is the answer.
- **Post the issue comment in the same turn as the commit.** A result that is
  committed but unpublished is invisible to the reader most likely to redo it.

## Finishing a batch includes regenerating the derived documents

New rows in `results.jsonl` make the generated tables stale, and
`test_recommendations.py` fails until you regenerate them:

```sh
uv run python benchmarks/agent/splice_tables.py
```

The failing test is correct behavior. A document that quotes a pass rate the
data no longer supports is what this project has published three times. The
check skips while a batch is running, because `results.jsonl` is mid-write.
Re-splice at the end of a batch.

**Read pytest's exit code, not the last line of its output.** See
[Check the exit status, not the tail](automation-hazards.md#check-the-exit-status-not-the-tail).

## American English spellings only (2026-09-05)

Write `behavior`, `favor`, `optimize`, `summarize`, `normalize`, `recognize`,
`judgment`, `defense`, `artifact`, `license`, `labeled`, `modeling`. This holds
in Markdown, code comments, docstrings, commit messages, and issue and PR text.
British forms arrived by drift (52 against 19 on 2026-09-05), and an unstated
convention follows whoever wrote last.

**Do not trust a blanket prefix substitution.** Check every word it introduces:

- `analys` → `analyz` makes "analyzis". Keep *analysis*; the verb is *analyze*.
- `characteris` → `characteriz` makes "characteriztic".
- `optimis` → `optimiz` makes "optimiztic".

**Do not rewrite text that is not ours:**

- **Quotations**: a quoted post, model output, or upstream comment. A spelling
  pass once rewrote a model's own words inside `LADDER2_FINDINGS.md`. A changed
  quotation is a misquote. Model transcripts under `benchmarks/**/ladder2/` are
  quotations.
- **Preserved evidence**: `logs/sweeps/` holds what a source said.
- **Archived snapshots**: `docs/archive/` is what a document said on a date.

**Code identifiers are out of scope.** Rename one deliberately, in its own
commit, or leave it.

## Check a peer every 20 minutes, and check what it is doing

**Operator rule, 2026-09-07.** When you manage a peer agent, check its status
every 20 minutes and confirm it is working. Idle is acceptable only when you
asked for idle. Two failures were invisible from the managing session: a peer
blocked for hours on a permission prompt, and a peer that sat 47 minutes after
its push went red in CI. Nothing tells an agent its CI failed.

**A check asks "what is it doing now", not "did it reply":**

```sh
top -l 2 -pid <peer-pid> -stats pid,cpu,command | tail -3   # live CPU (macOS)
ps -eo ppid | awk '$1==<peer-pid>' | wc -l                  # child processes
gh run list --repo <repo> --branch <its-branch> --limit 1 \
  --json conclusion,createdAt                               # did its push pass?
```

`ps`'s `%CPU` is a lifetime average and reads `0.0` for a day-old session.
Sample with `top` instead.

**Say what is blocked and what is not.** Told not to run tests on a machine
that held a measurement, a peer idled for two hours. It could have kept coding
in its own worktree against CI. When a peer holds back further than you meant,
the fault is in the instruction.

**The no-tests rule covers the measured machine only.** CI runs on the
self-hosted Linux runner, so a PR can go green there while the run lock is
held. What the rule forbids is `uv run pytest` and `uv run ruff` on the machine
that holds the lock; `conftest.py` refuses the suite there.

## Cutting a release (2026-09-07)

Two gates in `.github/workflows/release.yml` run on the tag push:

```sh
# 1. bump the version in pyproject.toml          version = "X.Y.Z"
# 2. write the changelog section BEFORE tagging  ## vX.Y.Z — YYYY-MM-DD, ended by ---
# 3. land both through a PR, as any change
# 4. tag main, and nothing else
git tag -a vX.Y.Z -m "vX.Y.Z" && git push --tags
```

**Never write release notes into the tag message.** The shell expands
backticks in `-m` and deletes the text they surround, with no error. The
workflow generates the notes from `docs/changelog.md` with `--notes-file`, and a
test keeps it that way.

**The gates refuse a bad release rather than warn:**

- `scripts/check_release_version.py vX.Y.Z` refuses a tag that disagrees with
  any declared version. It *finds* the declarations (`pyproject.toml`, any
  `__version__`), so a new one cannot escape the check.
- `scripts/release_notes.py vX.Y.Z` refuses a version with no changelog
  section. An empty release note reads as trivial work rather than undocumented
  work.

`tests/test_release.py` weights the **negative** cases: these scripts run
unattended, and a gate that passes everything looks exactly like one that
works. Two drift guards run on every commit: the declared version must already
have a changelog section, and the workflow must still call both scripts.

**`${{ runner.temp }}` in a workflow-level `env:` fails with no logs.** The
`runner` context exists only inside a step. Set `UV_CACHE_DIR` from a step that
appends to `$GITHUB_ENV`, as `test.yml` does.
`test_no_workflow_uses_the_runner_context_outside_a_step` keeps it that way.

**`docs/changelog.md` holds releases; `docs/history.md` holds everything
before them.** A section that runs to end of file becomes the release notes: the
first v1.0.0 attempt produced 102,607 bytes of them. Four tests hold the line:

- a pre-1.0 entry (`**YYYY-MM-DD`) in `changelog.md` fails;
- a `## vX.Y.Z` heading in `history.md` fails;
- an entry dated after 2026-09-07 in `history.md` fails (the file is frozen);
- a version section that does not end before the next one fails, because one
  missing `---` takes every section below it into the notes.
