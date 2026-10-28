# Measurement discipline

How to read, compare, and report a number in this project. **Read this before
you run a measurement, compare two results, or quote a figure in an issue,
comment, or commit.** Each rule exists because breaking it has put a wrong
number into a published place at least once.

The short forms live in [`../AGENTS.md`](../AGENTS.md). The full incident
stories are in the linked issues and [`changelog.md`](changelog.md).

---

## Cite engine source as `file:line at <sha>`

A bare `ds4.c:40442` is not a citation. It is unverifiable a week later and
often unverifiable the same night. ds4 has many worktrees at different shas,
`ds4.c` is over 70,000 lines and moves daily, and the same function sits at a
different line in every tree.

On 2026-09-06 a claim that `raw_cap` clamps a prefill chunk to 8192 shipped
into a script comment, a test docstring and a PR body. The reviewer's
corrected line number was also wrong, because the two readers were in
different trees. Read by name, the function showed the clamp does not exist.

- **Write `ds4.c:12159 at ds4 399acbbe`**, naming the tree and the sha.
- **Find the code by name, not by line.** `grep -n 'ds4_prefill_cap_for_prompt'`
  in the tree you mean. A line number is the result of a lookup, never the way
  to do one.
- **Verify a correction before you accept it.** A reviewer's line number is a
  claim like any other.

`tests/test_citation_allowlist.py` fails on a new bare citation. Its allowlist
holds the known-bare ones that are not retrofitted yet, and only shrinks.

## Do not overstate what is measured

The project answers one question: **which model + engine + harness combination
is best for running a coding agent locally, judged on code quality, problem
solving, and speed?** **Problem solving** and **speed** are measured. **Code
quality is not**: nearly every backend passes the tasks, so the suite cannot
tell good code from code that merely passes (#4). Write "passes the suite",
never "writes better code".

**Reliability outranks all three in practice.** A pair that fails one task in
five is unusable however fast it is. Quote pass rates with their confidence
interval: a perfect 21/21 only establishes ">85%" (#23).

## Never write "N times faster" or "N times slower"

The comparative form hides which way the ratio runs. "4x faster" can be read as
four times the rate, a quarter of the time, or faster *by* a factor of four.
"3x slower" has no agreed meaning at all. **A multiplier is fine when it says
what it multiplies.** The problem is the comparative adjective, not the number.

| write this | not this |
|---|---|
| took **5.2x as long** | 5.2x slower |
| finished in **1/4 the time** | 4x faster |
| took **75% less time** | 4x faster |
| **193 s against 931 s** | 4.8x faster |
| Aider **11.1 s**, Claude Code **189.6 s** | Aider was 17x faster |

**Always give the absolute wall-clock seconds beside the relative figure.** A
percentage hides magnitude: 4.2 s → 2.2 s and 500 s → 300 s are both "≈40%
less time", but the first is noise and the second is worth a paragraph. Write
"took 53% of the time: 751 s against 1429 s", never "took 53% of the time"
alone.

**A dispersion ratio is not a comparison and needs no rewriting.** "An 18x
spread on one task" is worst-over-best *within a single cell*. It says how
unstable one thing is, not that A beats B.

The generated tables in [`results.md`](results.md) print three dispersion
columns. Name the column whenever you quote one:

- **`spread (worst task)`**: for each task with two or more passing trials,
  the slowest pass over the fastest pass; the column shows the largest. Look
  here to see how bad one task can get.
- **`spread (typical task)`**: the median of the same per-task ratios. Look
  here to see how consistent the stack usually is.
- **`range (all tasks)`**: the slowest pass of any task over the fastest pass
  of any task. It measures how different the tasks are, so it is mostly task
  mix, not consistency. Never quote it as a spread (#944).

Both spreads show a dash when no task has two passes.

## Name both directions of a ratio, every time

Print `b/a` and `a/b` side by side. A ratio whose direction the reader has to
infer will be inferred backwards. Two `decode_ab_report.py` outputs that
printed one direction were each misread within the hour they were written.

## One definition of "done", in one place

**A predicate that decides whether data may be used has one owner.** On
2026-09-04 a "is this run complete" check was written three times and was
wrong a different way each time: it counted files (a file being written already
has a name), checked uniform row counts (one CSV is trivially uniform), and
ignored repetitions (`q4-rep1` and `q8-rep1` are a complete pair and a third of
the run). The fix was one function, `post_ab_run.is_complete`, that every
caller delegates to. Two copies of a rule drift, and the drift is invisible
until something crashes unattended.

## Another machine's result is a lead, not noise

**Do not dismiss a finding because it was measured on other hardware.** Most
people who build these engines are on M3/M4 Macs or other GPUs, so a new kernel,
flag or scheduling change shows up there first. The **mechanism** usually
transfers even when the **number** does not. Write *"X gained 30% on an M4;
test the flag here"*, never *"not our hardware"*. Three things do not carry
over:

- a configuration that **does not fit** in our memory — ask which quant does;
- the **absolute figure** — thermal state alone moves ~4% (#58), and a 3-trial
  median carries ±28% (#23), so quote ratios and rankings;
- **kernels for another backend** (CUDA, ROCm, Metal) — the quantization
  reasoning transfers, the kernels do not.

Our own measurement is the bar for **publishing** a claim (#59). It is not a
filter on what is worth trying.

## An outside number carries its own version (#770)

When an outside source calibrates a result — an upstream PR's table, a post, a
vendor card — write the source's version beside the version under test. A gap
between "upstream says X t/s" and "we measured Y" means nothing when the two
ran different releases: ollama#18550's +14–19% prefill shipped in 0.34.4-rc0,
so a row from an earlier release cannot calibrate against it (#692). When the
source names no version, say so, and treat the comparison as a lead, not a
calibration.

## Stamp every line with the code that produced it

**Never call `logging.basicConfig`. Inside `benchmarks/agent`, call
`provenance.configure()`; everywhere else, `logs.configure()`.**
`test_provenance.py` and `tests/test_logging_format.py` fail if you do.
`provenance.configure()` adds the harness stamp on top of `logs.DATEFMT`.
(The timestamp format is [Every time and date is ISO 8601](agent-workflow.md#every-time-and-date-is-iso-8601-americanew_york-with-an-explicit-offset-2026-09-06).)

```
2026-09-01T07:08:35-0400 INFO [c263902-dirty] ds4  excision  4/14  15/15
```

The bracketed field is the harness commit. **`-dirty` means the tree had
uncommitted changes**, so that line came from code that exists on no commit. A
number carrying `-dirty` may decide what to do next; it may not be published.
Outside a git tree the stamp reads `nogit`, never a blank or a plausible sha: a
wrong attribution is worse than a missing one, because it is believed.

**Why.** Three published sets of figures turned out to measure this project's
own bugs, and the expensive part each time was not knowing which numbers
predated the fix.

- **Every entry point** logs through `provenance.configure()`.
- **Every test session** prints the harness commit and a fingerprint of the
  ledger (`conftest.py` → `pytest_report_header`): row count plus a content
  hash, so two runs over the same data are visibly the same run.
- **Generated documents** record the *data* fingerprint, not the commit. A
  stamp that changes on every unrelated edit churns the file and trains people
  to skim the diff.
- **When you quote a measurement anywhere**, carry the versions with it. Each
  row's `env` has `harness_head`, the client and engine versions, and the server
  command line. Quoting a wall time without them is how "13/27" survived two
  weeks.

## Pin the sampler, and vary one thing at a time

**A sampler parameter can halve the pass rate, and the parameters interact.**
Over 36 trials (#36), same task, model, engine and client:

| configuration | pass |
|---|---|
| `top_p 0.95`, no repetition penalty | **17/18** |
| `top_p 0.90`, **no** repetition penalty | **7/12** |
| `top_p 0.90` + `repeat_penalty 1.1` | **6/6** |

Temperature and top_k were each isolated and are innocent. `top_p 0.90` is
harmful **only** without a repetition penalty.

Every engine has its own default sampler, and nobody chose them. So **a
cross-engine or cross-backend comparison is valid only when both sides are
sampler-matched**, and the sampler belongs on the row. llama.cpp rows record it
from `/props`, and Ollama rows from `/api/show`. ds4 and other OpenAI-compatible
servers do not report one, and the row says `engine defaults (not reported by
<engine>)`.

**Vary one parameter at a time.** This effect was missed twice by controls that
moved a *set* of related settings together. A control that changes a group
only tells you the group matters.

## A failing arm looks fast, so pair the tasks before comparing totals

**A failed trial is usually a short trial**: it dies on turn one, or the agent
gives up. So an arm that fails more looks *faster* on any total or median over
all rows, and the effect can invert a comparison. On the #77 MTP arms
(2026-09-03):

| comparison | arm A (MTP off) | arm B (MTP on) | reading |
|---|---|---|---|
| total wall, **all 15 rows** | 3074 s | 1726 s | "B takes 56% of the time" |
| total wall, **the 8 tasks that passed in both** | 1106 s | 1036 s | **B/A = 0.94** |

**Restrict a wall-time comparison to the tasks that passed in *both* arms**,
and say how many that was. Report a pass-rate difference separately; never fold
it into a speed number.

The same run shows why the next check is not optional. Arm B's throughput was
better (66.0 s per 1k output tokens against 107.9), but its wall time barely
moved, because it emitted 53% more tokens for the same eight tasks (15,704
against 10,247).

## A wall-time difference is a token-count hypothesis

**Check seconds per 1k output tokens before you attribute a speed gap to
anything.** Three times a gap that looked like a property of an engine, a
stack or a client was how many tokens the model emitted:

| claim | reality |
|---|---|
| llama.cpp is 66% slower than Ollama (#28) | identical throughput; 4x the tokens. Four sampler defaults |
| MTPLX is 17% faster on 68% fewer tokens | never re-checked; same shape, marked provisional |
| Codex is 2.14x slower than Claude Code on Swift (#44) | **39.6 vs 47.6 s/1k — Codex is *faster* per token, and emits 2.37x more** |

```
seconds_per_1k = wall_seconds / output_tokens * 1000
```

If the rates match, the gap is token count — a prompting or sampling effect,
and portable. If they differ, it is throughput, and belongs to the stack.
**Say which one it is.**

## Within one backend, a wall ratio is a turn-count lottery

Between two arms of the **same** engine (a flag flipped, a KV type changed),
**wall time tracks how many turns the agent took, not how fast the engine
ran.** Over 90 rows of `qwen38fnq3nothinkdgx`, `r(num_turns, wall_seconds) =
0.932`. Wall time spans 19.9 s to 496.7 s while seconds per turn spans only 2.8
times; `parser-mbox-quoting` alone drew 8 to 67 turns across nine control
trials. #344's ubatch-2048 arm published a wall ratio of **0.873**, a 13%
speed-up, while its TTFT ratio was **1.005** and its seconds-per-turn ratio
**1.007**: the treatment drew shorter conversations, and nothing changed.

**Read three numbers, in this order, with a gate in front:**

0. **The cell's own seconds-per-turn spread across tasks.** Under ~2x, the rate
   isolates the engine; much above it, the quotient still carries workload.
   `qwen38fnq3nothinkdgx` spans 1.6x and passes; `qwen36nvfp4v1dgx` spans 6.9x
   (6.90 s to 47.93 s per turn) and fails. On a failing cell, report the
   per-task disagreement, not a median over it.
1. **turns** — did the treatment change what the agent did? It needs no gate.
   A ratio far from 1 is itself a finding (#354: draftless speculation blew up
   the turn count 2.8 times).
2. **seconds per turn** and **TTFT** — did the engine get faster? Seconds per
   turn is gated on step 0. TTFT is not, because it comes from the engine's own
   counter.
3. **wall** — what the user feels, and the project's headline (#14). It is just
   not the number an engine flag is being asked.

`scripts/report.py` implements this: `turns()`, `seconds_per_turn()`, and
`homogeneous()` (the step-0 gate). A two-backend `report.py` run prints a
"What moved: the agent, or the engine? (#353)" block, and warns when a cell's
per-task spread makes the rate unreadable.

**Which TTFT field to read (#444).** `step_ttft_ms_*` is anchored on OpenCode's
`step_start` event, which OpenCode 1.18.31 emits *after* the request has spent
most of its time in the engine: it read 47–50 ms where invocation to first text
took 4.4–4.5 s. So it describes OpenCode's serialization, not first-token
latency, and must carry the client version when quoted. Rows after #444 carry
`first_content_ms` (invocation to first visible output, measured outside the
client) and `step_ttft_anchor`. Prefer the engine counter where there is one:
on vLLM and SGLang, `engine_ttft_ms_mean` is the change in the engine's
`time_to_first_token_seconds` histogram across the trial, with
`engine_ttft_requests` and `engine_ttft_source` beside it
(`benchmarks/agent/engine_ttft.py`). It is exact only while the trial is the
server's only client. llama.cpp, ds4 and Ollama record nothing.

## Know what a trial count can support

Measured over 398 trials by `../benchmarks/agent/sizing.py`, not estimated:

| trials | one task's median | 5-task suite | pass rate claim |
|---|---|---|---|
| 3 | ± 27.9% | ± 12.9% | >0% only |
| 10 | ± 13.5% | ± 5.4% | — |
| 35 | ± 4.9% | ± 2.2% | **>90%, if unbroken** |

**One trial is not a result.** These models are sampled, and the wall-time
distribution has a fat right tail, so use medians, not means. Two findings in
`RESULTS.md` came from one trial each, and more data refuted both.

**Three trials is a screening run.** It answers "does this work at all" and "is
this difference enormous". Two suite totals separate only above a ~26% gap, and
two task medians above ~56%. Below that, write "no difference measured", never
"X is faster than Y".

**A pass-rate claim above 90% needs 35 consecutive passes.** One failure costs
about twenty trials: 46/46 clears 90%, 46/47 does not. A 15/15 backend is
unmeasured above 80%.

## A figure needs its input named, not only its instrument

**Before you quote a number, ask what it is a number OF, and check that the
answer is on the row.** Our ds4#952 correction quoted "prefill median 0.999"
with the commit, weights, SHA-256 and repetition count, but not the prompt.
The prompt decided the answer: +2.5% with a 135 kB prompt, parity with a 405 kB
one. `../scripts/prompt_meta.py` stamps `prompt_file` and `prompt_bytes` onto
every ds4-bench CSV, and `decode_ab_report.py` puts the prompt in the quotable
line and refuses to pool prefill across two prompts. A default nobody has to
type is a default nobody writes down.

## Check that both arms can run before planning the comparison

**Load both arms before you schedule machine time.** A model load is minutes;
the run it would justify is hours. #138's requant A/B died in three short
checks: each engine loaded one quant and refused the other, so the quant and
the engine were welded together and any comparison was a **stack** comparison.
A control run (the same flags, our own weights) is what proved that, rather
than a typo.

**A flag missing from `--help` is not a missing flag.** Read the argument
parser before you report a capability as absent. `ds4-bench` takes `--ple`,
undocumented (`ds4_bench.c:275 at ds4-main 9ab70534`), and a `--help` grep
published "ds4-bench cannot measure this model" on two issues. That error
cancels work, and nobody re-checks a capability that has been ruled out.

## A generated-token count equal to the cap is a truncation, not an answer

**When a graded run reports a failure, compare the generated-token count with
the cap before you record it.** On `ds4-eval` a new build read 5/6 against the
old build's 6/6. The failing answer had generated exactly 2500 tokens, the
`--tokens` value passed, and at 8000 tokens both builds were 6/6. A budget you
chose is a property of the harness, and it silently turns "slower to reason"
into "wrong".

**A median is not a result until it is larger than the spread around it.** The
same run's "~5% more tokens" had per-case ratios from 0.724 to 1.352;
`../scripts/eval_trace.py` now prints the spread beside its median. A decode
rate gain is not a session-time gain if the model talks more to get there, so
tokens-to-answer belongs beside any tok/s claim.

## A remedy that cannot be switched off cannot be measured

**When a fix ships as a behavior change in code the harness calls, give it a
switch at the same time.** #112's remedy 2 stayed unmeasured because turning it
off meant editing the shim, which an unattended run cannot do.
`SHIM_NO_STRIP=1` is now that arm. Make a toggle opt-in and truthy-checked, so
a stray `SHIM_NO_STRIP=` in a shell profile cannot disable a shipped remedy,
and make it toggle **only** the remedy.

## Name the confounds

Every backend added here changes more than one variable at a time. Write the
caveat into the backend block in `tasks.toml` when you add it: engine, quant,
tune, and default sampler all move together, and a result that cannot
attribute its cause must say so.

**Confinement is one of those variables (#477).** The policy is the same on
every machine: the trial writes only its checkout and cannot read the answers.
What each mechanism enforces differs, and each row's `confinement` field
(written by `run.py`'s `confinement_record`) records it:

| dimension | macOS (`sandbox-exec`) | Linux (`bwrap`) |
|---|---|---|
| `paths` | `home-allow-list` (#780; `deny-list` before) | `deny-list` |
| `tmp` | `private`: its own `TMPDIR` | `private`: its own `/tmp` and `/dev/shm` |
| `writes` | `allow-list` | `unenforced` |
| `sockets` | `denied` | `denied` |
| `network` | `unenforced` | `unenforced` |
| `memory` | `harness:<cap>`, `run.py`'s memcap | `harness:<cap>`, `run.py`'s memcap |

The stamp records what is enforced, not what could be.

**`home-allow-list` (#780):** a trial once read personal files in the
operator's home directory, which the deny-list left open. The macOS profile
denies reading file contents under `$HOME`, except for `HOME_READABLE` in
`run.py`, the harness checkout and the trial's checkout. A tool that fails
with "Operation not permitted" on a home path needs that path added there,
with its reason. Rows stamped `home-allow-list` must not pool with `deny-list`
rows.

**A missing field means different things on the two platforms.**

- **On Linux**, every row from 2026-09-17T23:34-0400 on carries
  `"mechanism": "bwrap"`. Rows without the field ran unconfined.
- **On the M5 Max**, the field starts at 2026-09-17T23:12:11-0400, but
  `sandbox-exec` confined trials from 2026-08-31 (#54). A row without the field
  but with a `sandbox_denied` list ran confined. **Read `sandbox_denied`, not
  the missing field, to find an unconfined row**. The unconfined rows have
  neither, and date from 2026-08-15 to 2026-09-01.

Do not pool confined and unconfined rows on a task where the sandbox is what
moved: `script-transform` went 12/20 unconfined and 9/10 confined on the A3B
leader (#477).

## Observe the wire call, not the status code

**Diff the actual request bodies between two arms before you believe a
difference between them.** A 200 from two endpoints means both work, not that
both arms used the same one.

- On 2026-08-17 OpenCode used ds4-server's OpenAI path while the Claude Code
  baseline used the Anthropic path, so client and protocol varied together and
  a +60% gap could not be attributed. The config came from another tool's
  `connect` output: a template answers "will this run", not "does this match".
- On 2026-09-03 the ds4 Qwen shim scored 12/12 on synthetic prompts and 0/6
  under OpenCode. OpenCode sent `stream: true`, and ds4's streaming path
  dropped the assistant text (`tool_calls` 1/12 streamed, 7/12 not). That was
  the whole of a published 0/45. The server log said `text_len=231` while the
  client received zero bytes; the tell is usually already in a log.

## ds4's Qwen MTP runs only at temperature 0, and clients send no temperature

`ds4_session_eval_speculative()` enters the Qwen MTP path only at
`temperature <= 0.0f` (`ds4.c:80120 at ds4-metal ba01f5d`); above zero it does
one plain eval and returns (`ds4.c:80216 at ds4-metal ba01f5d`). A request with
no `temperature` gets `DS4_DEFAULT_TEMPERATURE`, `1.0f`
(`ds4.h:56 at ds4-metal ba01f5d`, `ds4_server.c:12734 at ds4-metal ba01f5d`).
**OpenCode sends no temperature**, so 119 MTP-arm rows loaded the sidecar,
reported `state=ready draft=7`, and never speculated.

**Zero timing lines means the call was never reached.** The timing print is not
conditional on success: even a turned-away cycle prints
`verifier=scheduler-bypass` (`ds4.c:78942 at ds4-metal ba01f5d`), and `timing`
is true from `--mtp-timing` alone (`ds4.c:78920 at ds4-metal ba01f5d`).

- **An MTP arm must pin `temperature: 0` on the client**, or it is not an MTP
  arm. `run.py --require-draft` refuses one that is not (#210); it fired on a
  trial that otherwise passed.
- **Pinning temperature is itself a change of regime**, so a greedy-MTP arm
  needs a greedy-plain arm beside it.

The `Qwen MTP history frontier short` line also prints from the ordinary
forward pass (`ds4.c:56588 at ds4-metal ba01f5d`), so it is not evidence that
speculation ran.

## MTP is not a speed-only flag

ds4's defaults do **not** preserve the sampling distribution: without
`--mtp-exact-sampling` it accepts drafts that match the target's greedy
choice, which biases output toward greedy at any temperature above 0, and
`--mtp-margin` (default 3) tunes that acceptance. So an MTP-on/off difference
in **pass rate** is not attributable to speculation. Wall time is the cleaner
comparison, and only if the token counts match. Isolating speculation needs a
third arm with `--mtp-exact-sampling`; see
[Pin the sampler, and vary one thing at a time](#pin-the-sampler-and-vary-one-thing-at-a-time)
and #39.

## A backend can be fast, correctly quantized, thermally fine — and unusable

The setup that scored 0/45 (before the shim's streaming fix) ran **40.2 t/s
decode and 1107 t/s prefill** at a 77.7 C die maximum. Every engine-level
number was good and the cell was worth nothing. Engine rates are a reason to
test, never a result.

## Sustained load drifts ~10% — bracket with A-B-A

Two identical `llama-bench` runs of one binary, five minutes apart on the M5
Max, differed by -0.25% at pp512 and -9.8% at tg128 @ d16384. **An A/B smaller
than about 10% at depth is unmeasurable without bracketing or interleaving.**
Run A-B-A and check that the two A legs agree before you read anything into B.
A plain A-then-B would have reported a 6% regression that does not exist.

## Coherence-check at temperature 0 before every benchmark

A model can load, serve, and report plausible token counts while it emits
noise (#25). Run `../scripts/coherence_check.py` before any measurement batch,
at temperature 0, where the output is deterministic enough to read.

## Nothing may feed `results.verdict()` except the oracle

Gates, hashes and the verbatim check ride alongside a verdict and never into
it. A test asserts that a filthy solution and a clean one get the same verdict.
The moment a quality signal decides a pass, the harness is judging, and its
whole claim is that it does not.

## Never publish a ratio without the absolutes beside it

**Every A/B write-up carries the absolute rates for both arms, not only the
paired ratio.** A ratio is a claim; the absolutes let a reader on another
machine check it, and let the ratio be recomputed if the harness is wrong. On
2026-09-07 three #162 comments carried ratios alone, after the maintainer had
already asked for absolute t/s.

- **Post runs with `../scripts/post_ab_run.py`.** It posts
  `decode_ab_report.py`'s output **verbatim**, per-frontier absolutes
  included. A hand-written summary is fine; hand-transcribed numbers are what
  went wrong.
- `test_the_per_frontier_block_carries_absolute_rates_not_only_the_ratio`
  keeps the absolute columns in the report.

Say the run-to-run variation too. Run 3 of the #162 batch was 3–7% slower than
runs 1 and 2 on **both** arms. That is the machine, and it is why the design is
paired, and why one absolute number from one box must not be set beside one
from another.

## Report results with the script, not by hand

```sh
uv run python scripts/report.py --backend gemma426
uv run python scripts/report.py --backend qwen --backend qwen36 --since 2026-09-01T20:50
```

`scripts/report.py` calls `results.trials()` and `results.verdict()` and
nothing else, and applies #23's rule: a 3-trial median carries ±27.9%, so two
medians must differ by about **56%**, **measured against the smaller median**,
before the gap is real. Tests pin both accessors and the threshold with the
literal numbers that were once got wrong: hand-rolled analysis counted 127
`--dry-run` control checks as failures, and divided smaller-by-larger to report
a 56% gap as a 36% reduction.

Never read `row["passed"]` directly: a timeout carries no `passed` and is a
failure, not an absence.
