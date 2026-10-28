# Automation hazards

The traps that live in shell, subprocesses, exit codes, and background waiters.
**Read this before you write a new script, a subprocess call, a background
wait or monitor, or any command that takes prose through `-m` / `--body`.**
Each rule comes from a failure that cost a wrong published number or left an
orphaned process on a measured machine.

The short forms live in [`../AGENTS.md`](../AGENTS.md).

---

## New code is Python, not shell (2026-09-09)

**Write new scripts in Python. Do not add a new `.sh`, and do not extend an
existing one when the change could go in a Python module.**

The operator set this with a done condition for #235: a **90% reduction** in
shell, from 3,589 lines to **≤359**, with **every file that contains `pgrep` or
`pkill` ported first**, whatever its size. Size and danger are unrelated: a
largest-first plan once reached its line target and left three process-by-name
lookups alive in two small files.

**Do not quote the numbers; run the script:**

```sh
uv run python scripts/shell_debt.py
```

It reports both targets against the tracked `scripts/*.sh` and `vault/*.sh`,
names each file's Python replacement, and classifies what stays shell.
`tests/test_shell_debt.py` fails when a file that matches a process by name has
no replacement, including a new file.

**Replacement is not retirement.** A `.sh` is retired only after evidence shows
its Python replacement agrees with it. Retired files sit in `vault/`, archived
and never run, because cited results still depend on them.
[`../vault/README.md`](../vault/README.md) records how each was cleared.

**90% does not mean "port everything".** `scripts/shell_debt.py` keeps a `KEEP`
list with a reason for each file. `scripts/local-agent.sh` stays shell because
`RECOMMENDATIONS.md` tells a stranger to run it, and a Python installer is still
a script you paste. `install-metal-ceiling.sh` installs a system artifact. When
a new file that must stay shell makes the target unreachable,
`test_the_target_is_reachable_without_porting_a_kept_file` fails. The choice
then (move the target or move a file out of `KEEP`) belongs to the operator.

### Why

Shell arbitrated every measurement this project published before #235, and a
shell bug does not crash: it produces a number that looks fine. On 2026-09-08
alone:

- `greedy_mtp_ab.sh` **never ran its control arm**. Under `set -u` on bash 3.2,
  `"${mtp_args[@]}"` is unbound when the array is empty, and only the control
  arm's array was empty. One hour of machine time, no comparison.
- Seven orphaned `until ! pgrep -f '<driver>.sh'` waiters, up to 6h30m old,
  each waited on itself.
- The commit guard refused every commit for hours, because those shells matched
  its patterns.
- CI went red because `pgrep -a` is GNU-only and GNU `pgrep -l` truncates a
  process name to 15 characters.

And `route_agent_ab.sh` passed a flag `run.py` had removed (#264). argparse
exited 2 before any work, the sweep ran under `|| echo`, and a rerun produced
**zero rows** and exited 0. A test now checks that every flag a driver emits is
one `run.py` declares.

### What a new script inherits

Use these rather than re-deriving them:

| use | instead of | what it prevents |
|---|---|---|
| `scripts/unitctl.py` | `pgrep` / `pkill` | a pattern matches the shell that quoted it |
| `scripts/lib/ds4_server.py`, `lib/mlx_serve.py` | hand-rolled start/stop | a leftover server, and a foreign one started beside ours |
| `scripts/ab_driver.py` | a copied alternation loop | nine drivers each had their own |
| `scripts/lib/stack_arm.py` | fourteen `NEW_*`/`OLD_*` variables | one wrong copy serves an arm the other arm's weights |
| a context manager | chained `EXIT` traps | a second bare `trap` silently discards the first |
| a list | `${arr[@]+"${arr[@]}"}` | an empty array is unbound under `set -u` on bash 3.2 |
| `logs.configure()` | `echo` / `>&2` | a line with no timestamp, or one in a shape nothing here parses |
| `Proc.short` / a recorded pid / a port | matching a command line | **three separate bugs** came from matching a name |

### The bar for touching shell at all

Edit a `.sh` only **to fix a live defect in a script that is still running**. A
feature, a new arm, or a new experiment goes in Python. If that means you must
port the script first, port it first.

**The exception:** a one-line shim that sets variables and execs something else
has nothing in it to get wrong. `scripts/ds4-fast.sh` and
`scripts/ds4-vanilla.sh` (three lines each) are the shape this means.

## Assert what a script does, not what it mentions

A test for "thermals never change fan state" asserted that the string
`fancontrol max` was absent from the module. The module's docstring names the
command to say it is never called, so the test failed on its own documentation.
The working test walks the AST for argv lists that begin with `fancontrol` and
asserts every verb is `status`. Match the call, not the word.

## Check the exit status, not the tail

**`uv run pytest -q | tail -2 && git commit` commits on a red suite.** The pipe
makes `tail`'s status the command's status. This shape put a red commit on
`main` on 2026-09-01, twice in one session.

```sh
set -o pipefail; uv run pytest -q 2>&1 | tail -2; echo "EXIT=$?"
uv run pytest -q                       # no pipe, no problem
```

The same trap hides a refused run. A driver that ends
`run.py ... 2>&1 | tail -80; echo "exit=$?"` reports `tail`'s status. On
2026-09-07 a batch refused to start on a stale run lock and the driver printed
`exit=0`, three lines below the refusal. A run that never happened read as one
that did. Use `${pipestatus[1]}` in zsh or `${PIPESTATUS[0]}` in bash, or do
not pipe:

```sh
uv run python benchmarks/agent/run.py ... > "$LOG" 2>&1
status=$?
tail -80 "$LOG"
echo "exit=$status"
```

Neither an output file nor a zero status is evidence that a run finished.

## Never put backticks in a `-m` message

**A double-quoted shell argument does command substitution.** In
`git commit -m "... `foo` ..."` the shell runs `foo`, deletes it from the
message, and says nothing. The same holds for `git tag -m`, `gh issue comment
--body`, `gh pr create --body`, and `gh issue create --title`. On 2026-09-01 a
commit message lost its evidence and the stray `opencode run` actually ran. On
2026-09-03 a posted comment lost two identifiers.

**Use `-F` with a quoted heredoc delimiter**, or `--body-file` (`-F body=@file`
for the API):

```sh
git commit -F - <<'EOF'
... `backticks` are safe here ...
EOF
```

`$(...)`, `` `...` `` and `$VAR` are one hazard. An unquoted delimiter enables
all three, so a body that holds a Makefile line, a shell snippet, a price, or a
regex is at risk. On 2026-09-07 a heredoc was unquoted so that `$SHA` would
expand. It also expanded `$(UNAME_S)` and published a wrong `ifeq (,Darwin)`.
**Wanting one substitution is never a reason to unquote the delimiter.** Keep
it quoted and insert the value afterwards:

```sh
python3 - <<'PY'
import pathlib, subprocess
body = pathlib.Path("comment.md").read_text().replace("@SHA@", subprocess.run(
    ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip())
subprocess.run(["gh", "issue", "comment", "170", "-F", "-"], input=body, text=True, check=True)
PY
```

**Code heredocs are not exempt.** On 2026-09-09 an unquoted `uv run python -
<<PY` that wrote a source comment containing `` `opencode` `` launched a real
interactive agent. The tool call timed out with no output and the process lived
38 minutes on the GPU. If a tool call hangs with no output, suspect this first
and check `ps` for what it started.

**Kill the group, not the process.** Killing that `opencode` by pid did nothing:
its parent shell spawned another eight seconds later. Only
`kill -TERM -<pgid>` ended it. `scripts/lib/child.py` enforces this (#268). A
pid is a process; a measurement is a tree.

The release-note gate in [Cutting a release](agent-workflow.md#cutting-a-release-2026-09-07)
exists for this reason.

## A waiter must be able to end

On 2026-09-07 a session showed "10 running tasks" on an idle GPU. Four `tail -f`
monitors, up to 9h43m old, watched files nothing would append to again. Seven
`until grep` waiters polled a producer that had been killed hours before. In
another session, 94 shells over 21 hours waited for a marker that a task which
exited 0 never printed. Two of the waiters would have started a stale
measurement when they fired.

**Choose the shape by how many notifications you need:**

- **One**: `Bash` with `run_in_background` and a command that *exits* when the
  condition holds.
- **One per occurrence**: a Monitor, with a poll that `break`s on a terminal
  state. Never an unbounded `tail -f`, `inotifywait -m`, or `while true`.

`tail -f "$LOG" | grep -m1 PATTERN` does **not** fix it: if the log goes quiet
after the match, `tail` never gets SIGPIPE and the pipeline hangs.

**Every wait needs a success condition, a failure condition, and a deadline:**

```sh
deadline=$(( $(date +%s) + 3600 ))
until grep -q '\[exited with code' "$TASK_OUTPUT"; do
    kill -0 "$RUN_PID" 2>/dev/null || { echo "producer $RUN_PID is gone"; exit 1; }
    [ "$(date +%s)" -lt "$deadline" ] || { echo "timed out"; exit 1; }
    sleep 30
done
```

- **Match the task's own terminal line**, `[exited with code N]`, which the
  harness writes at the end of every background task's output. A domain marker
  (`done:`, `frontier`) is a bonus signal, never the end condition: a grep for a
  marker the task never prints fails the same way on success and on failure.
- **Poll for the producer being gone** (`kill -0`), even when you trust the
  success string. That check would have ended all 94 shells on the first poll.
- **When you kill a run, kill what waits on it** in the same step.

**Stop a harness task with `TaskStop`, not `pkill`.** `pkill -f tail` matches
the watcher's own command line, and killing the `tail` alone leaves the task to
report `exit 144`. Kill by explicit pid when the task id is gone.

**Sweep at the end of a batch.** A finished measurement leaves no monitor, no
`tail`, and a released run lock. The count of running tasks is a claim about
the machine.
