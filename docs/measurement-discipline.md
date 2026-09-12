# Measurement discipline

How to read, compare, and report a number in this project. **Read this before
you run a measurement, compare two results, or quote a figure in an issue,
comment, or commit.** These rules exist because breaking each one has put a
wrong number into a published place at least once.

The short forms live in [`../AGENTS.md`](../AGENTS.md); the incident narratives,
tables, and citations are here.

---

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
tracked separately; `../evidence/0169-device-gate-table.md` is the priority, since
the M5 gate taxonomy rests on 27 of them.

## Do not overstate what is measured

The project answers one question: **which model + engine + harness combination
is best for running a coding agent locally, judged on code quality, problem
solving, and speed?** Of the three criteria, **problem solving** and **speed**
are measured. **Code quality is not** — the tasks are easy enough that nearly
every backend passes, so the suite cannot distinguish good code from code that
merely passes (issue #4). Write "passes the suite", never "writes better code",
until that changes.

**Reliability outranks all three in practice.** Most backend×harness pairs fail a
meaningful share of these easy tasks, and a pair that fails one in five is
unusable however fast it is. Quote pass rates with their confidence interval: a
perfect 21/21 only establishes ">85%", and on current sample sizes most
combinations cannot be told apart (issue #23).

## Never write "N times faster" or "N times slower"

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

**Always give the absolute wall-clock seconds, not the relative figure alone.**
A percentage or fraction hides magnitude, and magnitude is the point: 4.2 s →
2.2 s and 500 s → 300 s are both "≈40% less time", but the first is noise and
the second is worth a paragraph. So name the seconds beside every relative
claim — "took 53% of the time: 751 s against 1429 s", never "took 53% of the
time" on its own. Absolute numbers come first here too (issues are work logs).

**A dispersion ratio is not a comparison and needs no rewriting.** "An 18x
spread on one task" is worst-over-best *within a single cell* — a statement
about how unstable one thing is, not a claim that A beats B. The `spread`
column in `../RECOMMENDATIONS.md` stays as it is.

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

## Stamp every line with the code that produced it

**Never call `logging.basicConfig`. Inside `benchmarks/agent`, call
`provenance.configure()`; everywhere else, `logs.configure()`.** Two tests
fail if you do -- `test_provenance.py` for this package, and
`tests/test_logging_format.py` for the tree. `provenance.configure()` adds the
harness stamp on top of `logs.DATEFMT`; it does not get a clock of its own.
(The timestamp format itself is [Every time and date is ISO 8601](agent-workflow.md#every-time-and-date-is-iso-8601-americanew_york-with-an-explicit-offset-2026-09-06).)

```
2026-09-01T07:08:35-0400 INFO [c263902-dirty] ds4  excision  4/14  15/15
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

Measured over 398 trials by `../benchmarks/agent/sizing.py`, not estimated:

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
answer is on the row.** `../scripts/prompt_meta.py` stamps `prompt_file` and
`prompt_bytes` onto every ds4-bench CSV, `decode_ab_report.py` puts the prompt
in the quotable line, and it refuses to pool prefill across two prompts.
Defaults are the trap here: `PROMPT` had a default nobody had to type, so
nobody wrote it down for four months.

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
it is larger than the spread around it**; `../scripts/eval_trace.py` now prints
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

## ds4's Qwen MTP runs only at temperature 0, and clients send no temperature (2026-09-08)

`ds4_session_eval_speculative()` splits on temperature in its first statement:
at `temperature <= 0.0f` it enters the Qwen MTP path
(`ds4.c:80120 at ds4-metal ba01f5d`); above zero a Qwen session is neither GLM
nor DSpark, so it does one plain eval and returns
(`ds4.c:80216 at ds4-metal ba01f5d`). A request with no `temperature` field
gets `DS4_DEFAULT_TEMPERATURE`, which is `1.0f`
(`ds4.h:56 at ds4-metal ba01f5d`, `ds4_server.c:12734 at ds4-metal ba01f5d`).

**OpenCode sends no temperature.** So every MTP arm this project has run
through an agent client passed the flags, loaded the sidecar, reported
`state=ready draft=7` — and never speculated. 119 rows.

The silence is total rather than partial, and that is the tell. The timing
print is not conditional on success: even a turned-away cycle prints
`verifier=scheduler-bypass` (`ds4.c:78942 at ds4-metal ba01f5d`), and `timing`
is true from `--mtp-timing` alone (`ds4.c:78920 at ds4-metal ba01f5d`).
**Zero timing lines means the call was never reached, not that it ran and
failed.** A single line, even a bypass line, means the opposite.

Two things follow for any run:

- **An MTP arm must pin `temperature: 0` on the client**, or it is not an MTP
  arm. `run.py --require-draft` refuses one that is not (#210), and that gate
  fired on a trial that otherwise **passed** — 16 tests green, wall time
  ordinary, nothing in the row saying the treatment was absent.
- **Pinning temperature is itself a change of regime**, so a greedy-MTP arm
  needs a greedy-plain arm beside it. Otherwise a win is unattributable
  between speculation and greedy decoding.

Two published readings were withdrawn when this was found, both from correct
logs and an inference that did not follow: that the
`Qwen MTP history frontier short` aborts showed speculation entered and
abandoned (the line also prints from the ordinary forward pass,
`ds4.c:56588 at ds4-metal ba01f5d`), and that the tool-free/tool-bearing
separation tracked prompt size (an 11,000-token prompt speculates normally at
temperature 0).

## MTP is not a speed-only flag (2026-09-03)

ds4's defaults do **not** preserve the sampling distribution: without
`--mtp-exact-sampling` it accepts drafts matching what the target would
greedily produce, biasing output toward greedy at any temperature above 0, and
`--mtp-margin` (default 3) tunes that acceptance. So an MTP-on/off difference
in **pass rate** is not attributable to speculation — the model is sampled
differently. Wall time is the cleaner comparison, and only if the token counts
match. Isolating speculation itself needs a third arm with
`--mtp-exact-sampling`; see [Pin the sampler, and vary one thing at a time](#pin-the-sampler-and-vary-one-thing-at-a-time)
and #39.

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
`../scripts/coherence_check.py` before any measurement batch, at temperature 0
where the output is deterministic enough to read.

## Nothing may feed `results.verdict()` except the oracle

Gates, hashes and the verbatim check ride alongside a verdict and never into
it. There is a test asserting a filthy solution and a clean one get the same
verdict. The moment a quality signal decides a pass, the harness is judging,
and its whole claim is that it does not.

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

- `../scripts/post_ab_run.py` posts `decode_ab_report.py`'s output **verbatim**,
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
