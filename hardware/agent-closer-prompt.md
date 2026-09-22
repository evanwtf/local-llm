# The closer: end an agent session on any benchmark machine

This file is a **prompt**. Paste everything below the line into a session you
are about to end, on any machine in [`MACHINES.md`](MACHINES.md). It is the
other half of each machine's opener (#556):

| machine | opener |
|---|---|
| M5 Max MacBook Pro | [`agent-opener-prompt-m5-max.md`](MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/agent-opener-prompt-m5-max.md) |
| dual DGX Spark cluster | [`agent-opener-prompt-dgx-cluster.md`](Cortex-X925-128GB-GB10-x2/agent-opener-prompt-dgx-cluster.md) |
| DGX Spark (superseded by the cluster above) | [`agent-opener-prompt-dgx-spark.md`](Cortex-X925-128GB-GB10/agent-opener-prompt-dgx-spark.md) |
| Ryzen / RTX 3080 Ti desktop | [`agent-opener-prompt-ryzen-3080ti.md`](Ryzen9-7900X-32GB-RTX3080Ti-12GB/agent-opener-prompt-ryzen-3080ti.md) |

Think of a restaurant crew. At the end of the night the closers clean the
station and write the log. In the morning the openers read the log, check the
station for themselves, and start service. A good close leaves the openers
little to clean up.

**Terms.** The **closer** is this prompt. The **opener** is each machine's
start prompt, which was called the handoff prompt until #556.

- **The closer ends a session.** The departing session stops taking new work.
  It accounts for everything in flight, puts each piece in a durable place, and
  writes a closer log.
- **The opener starts the next one.** A fresh session starts minutes or days
  later, on a machine in an unknown state. It checks the machine first, then
  reads the closer log (its §1, step 8), then resumes. It works with no
  closer log at all (#558).

The closer log lives on the machine at
`~/.local-llm-bench/closer-logs/<timestamp>.md`, beside the run lock. It is
outside the repo because it names local paths, pids, and worktrees. Public
state still goes on the issues.

Why close at all: a session that runs for days carries tens of millions of
tokens, and its judgment degrades. A fresh session with a good log is better
than a long one with a full context.

Placeholder the operator fills in before pasting:

- `{REASON}` — optional: why the session ends, for example "routine restart
  after two days". Empty means routine.

---

````markdown
# Close this session

You are about to be ended. Reason: {REASON}. The next session starts cold. It
knows only what the disk, git, GitHub, and your closer log tell it. Your
memory of this conversation dies with you. Write down everything the next
session needs before you stop.

The rules in `AGENTS.md` and in this machine's opener (§2, the hard
rules) still apply while you close. In particular: no session URLs or IDs
anywhere, nothing private in the public repo, and no commits in a checkout that
a live run has frozen.

## Machine-specific commands

Find this machine with `uv run python scripts/machines.py --check`, then use
its column below wherever a step says "the server status command" or similar.

| step | M5 Max | DGX Spark | Ryzen / RTX 3080 Ti |
|---|---|---|---|
| queue | `make_next.py --platform macos` | `make_next.py --platform nvidia` | `make_next.py --platform nvidia` |
| server status | `scripts/unitctl.py status` | `scripts/dgx_server.py status <name>` for `llamacpp ds4 vllm omni clip ollama`; `docker ps` | `ps -Ao pid,ppid,etime,command`; `ollama ps` |
| stop a server | `scripts/unitctl.py stop <name>` | `scripts/dgx_server.py stop <name>`; `docker stop <container>` | stop the process you started; never `pkill` by pattern |
| GPU snapshot | `scripts/mac_dash.py` | `nvidia-smi`; `scripts/dgx_metrics.py`; `sensors` | `nvidia-smi`; `scripts/thermals.py` |
| special | A live stack A/B freezes every local-llm worktree. Commit doc-only changes through the GitHub contents API. | Memory can outlive a stopped server's PID. After a stop, run `scripts/machine_health.py check --for server` and record what it says. | The operator may power the box off after the session. A job left running dies then. Ask the operator before you leave one running. |

## 1. Stop taking new work

- Do not start a new run, a new task, or a new PR.
- Finish a small step that is already in progress when it takes a few minutes
  and leaves nothing half done: a push, a comment, a PR merge you are watching.
- Anything larger stays in flight. You record it; you do not rush it.
- A live benchmark keeps running. Do not stop it to close.

## 2. Take the inventory — run these, read every output

```sh
TZ=America/New_York date '+%Y-%m-%dT%H:%M:%S%z'
cd ~/git/local-llm
uv run python scripts/machines.py --check
uv run python scripts/machine_state.py              # run lock, resident servers, GPU occupant
cat ~/.local-llm-bench/run-lock.json 2>/dev/null
uv run python scripts/machine_claim.py status
<the server status command for this machine>
ps -Ao pid,ppid,etime,command | grep -E 'stack_agent_ab|run\.py|opencode run' | grep -v grep
git worktree list
for wt in $(git worktree list --porcelain | awk '/^worktree /{print $2}'); do
  echo "== $wt"; git -C "$wt" status --short --branch | head -20
  git -C "$wt" log --oneline '@{u}..HEAD' 2>/dev/null   # unpushed commits
done
git stash list                                      # read only; never pop or drop another session's entry
ls -1 ~/.local-llm-bench/closer-logs/*.md 2>/dev/null   # a log an earlier session left and nobody acted on
gh pr list --state open --json number,title,headRefName,mergeStateStatus,autoMergeRequest
gh run list --limit 10 --json conclusion,status,headBranch,displayTitle
```

In Claude Code, also run `CronList`, `TaskList`, and `ListAgents`. Print the
queue with this machine's queue command.

Then go back through this conversation. List every item that is not finished:

- a promise to the operator or a peer ("I will post the results on #N"),
- a decision the operator made that is not yet in a permanent place,
- a lesson you learned the hard way,
- work you parked, and why,
- anything you were told not to touch.

An earlier log that nobody acted on is still open work. Fold its open items
into your log, then move it to `closer-logs/done/`.

## 3. Make every live job safe to outlive you

A job that is a child of this session can die when the session ends.

- For each live job, find its parent: `ps -o ppid= -p <pid>`. A parent of `1`
  (launchd or init), or a systemd scope, means it is detached. A parent that
  is this session's shell means it may die with the session.
- Do not end the session under an attached job that must finish. Wait for it,
  or tell the operator and let them decide.
- Your background waiters, `Monitor` tasks, `/loop` wakeups, and `CronCreate`
  jobs die with the session. The next session does not inherit them. For each
  one, write down what it was waiting for and what it would have done next.

## 4. Put every piece of work somewhere durable

Work that lives only in your context or in an uncommitted file is lost.

**Code and docs:**
- Finished and not pushed: push it, open the PR, and set auto-merge. Record the
  PR number.
- Unfinished but worth keeping: push the branch and open a **draft** PR that
  says what is left. Record it.
- When a live run has frozen the checkouts, the pre-commit hook refuses a
  commit. Land a doc-only change through the GitHub contents API (the opener
  says how). For anything else, save a patch:
  `git -C <worktree> diff > ~/.local-llm-bench/closer-logs/patches/<worktree-name>.patch`.
  Name the patch in the log.
- A worktree whose branch has merged: leave it if a run uses it. Otherwise note
  it as safe to remove. Do not remove a worktree that a peer or a run uses.

**Results:**
- A run finished and its rows are not landed: land them now if the machine is
  FREE (opener, "Landing results"). If it is not FREE, record the run's
  output folder and the read-out steps.

**Issues — the public log:**
- Every issue with work in flight gets one comment. The comment gives the
  state, the next step, and where the evidence is. Use absolute numbers, as
  always. Sign it. Put no private paths, hostnames, or addresses in it.
- An operator decision about an issue goes on that issue.

**Rules and lessons:**
- A rule for the repo or for every agent goes into `AGENTS.md` or the openers,
  through a PR.
- A fact about the operator, or about how to work with them, goes into your
  memory folder (the path your system prompt names). Update an existing memory
  before you add one. If this machine has `~/git/claude`, publish it:
  ```sh
  cd ~/git/claude
  uv run --no-project --python 3.13 scripts/sync_memories.py --dry-run
  uv run --no-project --python 3.13 scripts/sync_memories.py
  ```
  Never commit, push, stash, or switch branch in `~/git/claude` by hand.
- A Codex session has no memory folder. It puts lessons in the log.

## 5. Clean the station

- Stop each server or unit that you started and that no live run needs. Use
  this machine's stop command. Leave running what a live run needs, and say so
  in the log.
- Release your machine claim if no live run needs it:
  `uv run python scripts/machine_claim.py release`.
- Delete your heartbeat job (`CronDelete <id>`) and stop your loops.
- Leave `~/git/local-llm` on `main`. Do not switch it under a live run.
- Do not run `git stash`, `git clean`, or `git reset --hard` to tidy up.

## 6. Write the closer log

```sh
mkdir -p ~/.local-llm-bench/closer-logs/patches ~/.local-llm-bench/closer-logs/done
LOG=~/.local-llm-bench/closer-logs/$(TZ=America/New_York date '+%Y-%m-%dT%H%M%S%z').md
```

Write the log to `$LOG`, in this shape. Keep every heading. Under a heading
with nothing in it, write "none".

```markdown
# Closer log

Written: <from `date`, full ISO 8601 with offset>
Machine: <the slug from machines.py --check>
By: <Claude Code or Codex, model>
Reason: {REASON}

## Live jobs (they outlive this session)
| pid | what | issue | started | ETA | detached? | log / output |
When each one exits, do this: <the exact read-out steps, in order>

## Resident servers and units
| unit or process | port | needed by | stop when |

## Run lock and claims
<holder, or "free">

## Open PRs
| # | whose | state | waiting for |

## Worktrees
| path | branch | state | keep or remove |

## Saved patches
| patch | applies to | what it is |

## Promises and unfinished items
- <what, the issue, the next step>

## Operator decisions this session
- <decision, date, and where it is now written: issue, AGENTS.md, memory>

## Do not touch
- <item and why: paused, awaiting approval, another session's>

## First actions for the new session
1. <in order; the most time-sensitive first>

## Suspicions and unknowns
- <things you were not sure of; what would settle them>
```

Rules for the log:

- Every time is a full ISO 8601 time with offset, from `date`, in New York.
- Every number comes from a command you ran while you closed.
- Name the evidence for each claim (a log path, a PR, an issue comment), so the
  new session can check it.
- Put no secret in the log.

## 7. Check your close, then report

- Run the §2 inventory again. Every live job, server, worktree, and open PR in
  the output is also in the log.
- Check each issue comment and PR text you wrote for a session URL or ID:
  `grep -n -E 'claude\.ai/code|session_[0-9A-Za-z]' <file>` must print nothing.
- Tell the operator, in chat:
  - the log's path,
  - the live jobs and their ETAs,
  - the first action for the new session,
  - "Ready to end."

Then stop. Do not end the session yourself; the operator ends it.
````
