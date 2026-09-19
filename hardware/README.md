# One directory per machine

Every measurement in this project is only meaningful next to the hardware that
produced it. Each directory here holds the results, logs and notes for exactly
one of the machines we manage.

**Rows from different machines are never pooled.** `results.foreign_hardware()`
refuses to append to a file that already holds another machine's rows, and the
directory a file sits in is the first answer to which machine that is.

## Naming

**Derive the name, never type it.** A hand-written directory can disagree with
the hardware it claims to describe and nothing would catch it:

```sh
uv run python scripts/hardware_id.py          # prints the canonical name
uv run python scripts/hardware_id.py --json   # and the facts behind it
```

It reads `system_profiler` and `sysctl` on macOS, and `/proc/cpuinfo`,
`/proc/meminfo`, `lscpu`, `dmidecode` and `nvidia-smi` on Linux. It refuses to
emit a name for a machine it cannot identify rather than producing an empty one
that would collide with every other unknown machine.

**Apple hardware: `<Model Name>-<Chip>-<Memory>-<Model Number>`.**
Apple ships one model number per configuration, so it removes all ambiguity —
"M5 Max 128 GB" describes several SKUs, `Z1MZ0002NLL/A` describes one. The `/`
becomes `_` because a path cannot hold it.

    MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A

**Everything else: `<CPU>-<Memory>-<GPU>-<VRAM>`.**
A self-built PC has no model number, so the identifier is the three things that
decide what it can run.

    Ryzen9-7900X-32GB-RTX3080Ti-12GB

**Memory is the installed size, not the usable size.** The Ryzen box reports
30.5 GiB to the OS and has 32 GB in its slots; the sticker number is what
someone comparing machines will have.

## What goes in a directory

Results (`results.jsonl`), run logs, a `RESULTS.md` for that machine, and a
`README.md` recording what does not fit in the name: OS, kernel, driver
versions, engine builds, and anything about the machine that could change under
us. Versions belong **in** the files, never in the directory name — a name that
changes on a driver update breaks every link to it.

The harness itself is shared and stays out of here: one apparatus, many
machines. See #85.

## One branch, not one branch per machine

Every machine commits to `main`. A machine is a directory here, never a
long-running branch (#292). A per-machine branch drifts behind `main` and then
its numbers come from a stale harness — the Ryzen branch sat 21 commits behind,
so its rows were not comparable to the laptop's without a merge first. Harness
changes land on `main` first, always; results reach `main` as ordinary commits
to `hardware/<id>/`. Per-machine `results.jsonl` files never share a path, so
two machines never conflict. See CONVENTIONS.md, "One main, machines are
directories", for why the ledgers are not `merge=union`.

## Openers and the closer

Each machine directory holds an **opener**, `agent-opener-prompt-<short>.md`:
the prompt that starts an agent session on that machine. One shared **closer**,
[`agent-closer-prompt.md`](agent-closer-prompt.md), ends a session on any
machine and leaves a closer log for the next one (#556, #558).

An opener must keep five rules:

1. **It works from nothing.** Its §1, the opening routine, starts from a
   machine with no clone, no `~/.local-llm-bench/`, no worktrees, no memory,
   and no closer log. It does not assume `~/git/local-llm` exists.
2. **It runs the same routine every time.** Every step is a check followed by
   a fix, and no step is skipped. A new machine, a good close, and a crash all
   go through the same ten steps.
3. **It reads a closer log but never depends on one.** No log is the normal
   case. When there is a log, its claims are checked against the machine.
4. **The machine beats the documents.** When the machine, the log, and the
   prompt disagree, trust the machine, then the issue, then `AGENTS.md`, then
   the log.
5. **It is public.** No hostnames, LAN addresses, or private paths.

`tests/test_shift_change.py` checks that every opener carries the ten steps
and links the closer, and that the closer names every opener.

### A new machine

1. Derive its name: `uv run python scripts/hardware_id.py`.
2. Register it: add an entry to `MACHINES` in `scripts/machines.py`, then run
   `uv run python scripts/machines.py` to regenerate `MACHINES.md`. Create its
   `hardware/<id>/` directory and its `hardware:<slug>` GitHub label.
   `tests/test_machines.py` checks that the three agree.
3. Copy the M5 Max opener as the starting point. Change what is specific to the
   machine:

   | section | what changes |
   |---|---|
   | preamble | the machine, its label, the placeholders |
   | §0 tool mapping | how to wait, how to find peers, where the metrics come from |
   | §1 opening routine | the commands in step 2 (reboot), step 4 (servers), and step 9 (queue); keep the ten steps and their order |
   | §2 hard rules | the machine's first rule and its server tooling |
   | §3a heartbeat | where each number comes from |
   | §4 queue | the `hardware:` label and `make_next.py --platform` |
   | §7 contracts | what is parked or promised on this machine |

4. Add the machine's column to the table of machine commands in the closer,
   and a row for its opener at the top of the closer.
5. Run the suite. `tests/test_shift_change.py` fails until the opener and the
   closer agree.

### Rewriting an opener

Take the rules from `AGENTS.md` on `origin/main`, not from memory or from an
old session. Change a rule in `AGENTS.md` and in the openers in the same PR.
Then follow §1 by hand from a scratch clone, and note each step that assumes
something an earlier session left behind.
