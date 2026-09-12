# Operating the harness

How to run the benchmark safely: models, servers, engines, the reference
repositories, and the results ledger. **Read this before you start a model
server, add or switch an engine, touch a target repository, or write to
`results.jsonl`.** The durable machine operations (argv to start each server,
thermal notes, and what a comparison on this machine must do) live in
[`m5max-runbook.md`](m5max-runbook.md); the traps that void a measurement live
here.

The short forms live in [`../AGENTS.md`](../AGENTS.md).

---

## Before you say a model is not on disk, run the census

Each runtime keeps models in its own tree — `~/models` (ds4/llama.cpp GGUF),
`~/.mlx-serve/models` (mlx-serve), `~/.ollama/models` (Ollama), plus LM Studio
and the HF cache. `ls ~/models` sees only the first. Run `uv run python
benchmarks/agent/model_inventory.py` (preflight prints it every run) or read
[`model-locations.md`](model-locations.md). Searching the wrong tree is a false
negative that has cost real time.

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

**A new weights directory needs a Time Machine exclusion before the first file
lands in it** — a download is exactly when that applies. The rule is
[Model weights stay out of Time Machine](../CONVENTIONS.md#model-weights-stay-out-of-time-machine-2026-09-06).

## The testing set is written down (2026-09-01)

[`../TESTING-SET.md`](../TESTING-SET.md) lists the four axes -- hardware, client,
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
promote the new one past what three trials can carry (see
[Know what a trial count can support](measurement-discipline.md#know-what-a-trial-count-can-support)).

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

The restart itself is the argv in [`m5max-runbook.md`](m5max-runbook.md), and
`wait_ready.py` is what tells you it is back -- not `/health`, which answers
before the model is loaded.

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
the pattern, so the loop never exits. (More on waiter traps in
[`automation-hazards.md`](automation-hazards.md#a-tail--f-monitor-never-ends-so-it-outlives-the-thing-it-watched).)

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
