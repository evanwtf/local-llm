# Automation hazards

The traps that live in shell, subprocesses, exit codes, and background waiters.
**Read this before you write a new script, a subprocess call, a background
wait or monitor, or any command that takes prose through `-m` / `--body`.**
Every rule here comes from a specific failure, and most cost either a wrong
published number or an orphaned process on the machine that arbitrates every
measurement.

The short forms live in [`../AGENTS.md`](../AGENTS.md).

---

## New code is Python, not shell (2026-09-09)

**Write new scripts in Python. Do not add a new `.sh` under `scripts/`, and do
not extend an existing one when the change could go in a Python module
instead.**

Set by the operator on 2026-09-09, alongside a done condition for #235: a
**90% reduction** in shell, from 3,589 lines to **≤359**, with **every file
containing `pgrep` or `pkill` ported first**, regardless of size. A rule that
only removes shell while new shell keeps arriving is a treadmill.

`pgrep` goes first because size and danger are unrelated. An earlier plan
ordered the work largest-first and reached its line target while leaving three
process-by-name lookups alive — in two small files, which sort last precisely
because they are small.

**Do not read the numbers here; run the script.**

    uv run python scripts/shell_debt.py

It reports both targets against `git ls-files 'scripts/*.sh'` and says which
`.sh` still has no Python replacement. Every number below is what it printed
on 2026-09-09 and will be stale soon after: 23 files, **3,589 lines**, **35
`pgrep`/`pkill` calls**. The target is ≤359 lines and **zero** by-name
lookups.

`tests/test_shell_debt.py` fails when a file matching a process by name has no
replacement — including a **new** one, which is what stops the rule being a
treadmill.

**The two numbers come apart, and it is worth knowing where.** As of
2026-09-09 every `.sh` carrying a `pgrep` or `pkill` has a Python replacement
— thirteen files, 2,686 lines, all 35 calls. Deleting them leaves **903 lines
and zero by-name lookups**, a 74.8% reduction. So **`pgrep` reaches zero at
75%**, and the last 544 lines down to 359 buy tests and readability,
not safety. Do that work, but do not confuse it with the part that stops a
measurement being wrong.

**Replacement is not retirement.** A `.sh` is deleted only once its Python
replacement has produced a run that agrees with it, so the line count will sit
at 3,589 until those runs happen. That is the intended order: a port that has
never arbitrated a measurement has not been tested where it counts.

**And 90% does not mean "port everything".** The script classifies every
remaining file, because the biggest one left is the one that must not move:
`scripts/local-agent.sh` is 284 lines and RECOMMENDATIONS.md section 3 tells a
stranger to run it. Porting it would change published instructions and buy
nothing — a Python installer is still a script you paste. With it,
`install-metal-ceiling.sh` and the two three-line `exec` shims kept as shell,
porting the other 534 lines lands at **342 against a target of 359**. The
margin is 17 lines, so a new `.sh` that has to stay shell can make the target
unreachable; `test_the_target_is_reachable_without_porting_a_kept_file` fails
when that happens, and the choice then — move the target, or move a file out
of `KEEP` — belongs to the operator, not to whoever is porting that day.

### Why, in the words of the failures

Shell arbitrated every measurement this project has published. When
one is wrong the result is not a crash — it is a number that looks fine. On
2026-09-08 alone:

- `greedy_mtp_ab.sh` **never ran its control arm**. `"${mtp_args[@]}"` is an
  unbound variable under `set -u` on bash 3.2 when the array is empty, and
  only the control arm's array was empty. The treatment arm ran all 15 tasks;
  its pair never existed. One hour of machine time, no comparison.
  *Fixed in `6ca27aa`* by the guarded expansion
  `${mtp_args[@]+"${mtp_args[@]}"}` — and **`set -eu` is still on line 35**,
  so deleting that guard brings the bug straight back. The incantation is
  load-bearing and unreadable, which is the point: the Python arm is a list
  that is sometimes empty, and an empty list is not a special case.
- Seven orphaned `until ! pgrep -f '<driver>.sh'` waiter shells, up to 6h30m
  old, each waiting on itself.
- The commit guard refused every commit for hours while the machine was idle,
  because those shells matched its patterns.
- CI went red because `pgrep -a` is GNU-only and GNU `pgrep -l` truncates a
  process name to 15 characters.

And one that had been true for weeks before anyone looked (#264):

- `route_agent_ab.sh` passes `--skip-tensor-gate`, which `run.py` removed when
  the ds4 route gate was rewritten. argparse rejects it and exits 2 before any
  work. Each sweep ran under `|| echo "... returned non-zero"`, so a re-run
  would restart the 75 GiB server six times over several hours, produce **zero
  rows**, and exit 0. Nothing checked that a flag a driver emits is a flag
  `run.py` declares; a test now does, parsed out of `run.py`'s own
  `add_argument` calls.

None is exotic. They are the ordinary failure modes of shell: word splitting,
empty arrays under `set -u`, text-matching for identity, and flags that differ
between BSD and GNU. Python has none of them, and this repo already tests
Python well.

### What the port established, and what a new script inherits

Use these rather than re-deriving them. Every one exists because a shell script
got it wrong:

| use | instead of | what it prevents |
|---|---|---|
| `scripts/unitctl.py` | `pgrep` / `pkill` | a pattern matches the shell that quoted it; bracketing only ever protected against *self*-match |
| `scripts/lib/ds4_server.py`, `lib/mlx_serve.py` | hand-rolled start/stop | a leftover server, and a foreign one started beside ours |
| `scripts/ab_driver.py` | a copied alternation loop | nine drivers had their own, already spelled `REPS` and `ROUNDS` |
| `scripts/lib/stack_arm.py` | fourteen `NEW_*`/`OLD_*` variables | one wrong copy serves an arm the other arm's weights |
| a context manager | chained `EXIT` traps | a second bare `trap` silently discards the first |
| a list | `${arr[@]+"${arr[@]}"}` | an empty array is unbound under `set -u` on bash 3.2 |
| `logs.configure()` | `echo` / `>&2` | a line with no timestamp, or one in a shape nothing else here parses |
| `Proc.short` / a recorded pid / a port | matching a command line | **three separate bugs** came from matching a name |

### The bar for touching shell at all

A `.sh` may be edited to **fix a live defect in a script that is still
running**, and for nothing else. If the change is a feature, a new arm, or a
new experiment, it goes in Python — and if that means porting the script
first, port it first.

**A `.sh` is deleted only after its Python replacement has produced a run that
agrees with it** (#235). Writing the replacement is not the same as retiring
the original, and a port that has never arbitrated a measurement has not been
tested where it counts.

### The exception

A genuine one-line shim — a wrapper that sets two variables and execs
something else — is fine as shell, because there is nothing in it to get
wrong. `scripts/ds4-fast.sh` and `scripts/ds4-vanilla.sh` are three lines each
and are the shape this means.

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
session** -- the shape is too convenient to resist under time pressure. That
mistake **put a red commit on main on 2026-09-01.**

Use one of these instead, and read the number:

```sh
set -o pipefail; uv run pytest -q 2>&1 | tail -2; echo "EXIT=$?"
uv run pytest -q                       # no pipe, no problem
```

A drift test firing is the system working. Committing through it is not.

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

**It is not only about messages, and the damage is not only lost text.** On
2026-09-09 an unquoted `uv run python - <<PY` was writing a *source comment*:

    # child.run, not subprocess.run: run.py re-spawns `opencode`, and a signal

The shell substituted the backticks, so it **launched `opencode`** -- a real
interactive agent -- and then waited for it. The tool call timed out at 120
seconds and reported nothing; the edit never landed; and the `opencode`
process stayed alive **38 minutes**, until a heartbeat reported an unexpected
GPU occupant and it was traced back through its parent chain. Two costs, and
the second is the expensive one: a silently mangled comment, and an orphaned
process on the machine that arbitrates every measurement.

So the rule has no exception for "this heredoc only contains code". Quote the
delimiter always. If the tool call hangs and produces no output, suspect this
before suspecting the tool -- and check `ps` for what it started.

**Then kill the group, not the process.** Killing that `opencode` by pid at
08:20 did nothing: its parent shell was still in the substitution and spawned
another one eight seconds later, from the same dead command. Only
`kill -TERM -<pgid>` ended it. That is the same lesson `scripts/lib/child.py`
exists to enforce (#268) -- and I made the mistake by hand while holding the
module that prevents it. A pid is a process; a measurement is a tree.

`CLAUDE.md` also records this trap for `git tag -m`; it is the same trap and it
has found several doors. See also [Cutting a release](agent-workflow.md#cutting-a-release-2026-09-07),
whose release-note gate exists for exactly this reason.

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
