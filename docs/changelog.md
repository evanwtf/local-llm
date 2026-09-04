# Changelog

What shipped, and why. Newest first.

This is the permanent home for what shipped. An entry belongs here the day the
work lands — a test, a convention in `AGENTS.md`, a line in `RESULTS.md`, or an
entry here is where a finding becomes durable; anything still only in
[`NEXT.md`](../NEXT.md) has not landed anywhere.

`NEXT.md` was reduced to the queue and nothing more on 2026-09-04 (see that
day's entry). Before that it carried a "Done since the last update" staging
area that stopped being drained — by 2026-09-02 it had reached **577 lines and
41.6 KB, 54% of the file** — so the file that was supposed to say what to do
next was mostly a record of what had already been done. That history was moved
here whole rather than summarised, because the reasoning in these entries is
the part worth keeping; several of them are the only written account of why a
guard exists.

**Read this for history, not for current state.** Numbers here were true when
written. Current results live in `hardware/<machine>/RESULTS-agent.md`, current
picks in `RECOMMENDATIONS.md`, and the current queue in `NEXT.md`.

---

**2026-09-04, evening. The build we publish is the one its author withdrew (#138).**

`ivanfioravanti/Qwen3.8-Flash-Next-DS4-Q4` replaced its Q4_0 routed-expert
build with a Q4_K imatrix one. Our `qwen38fnds4shim` cell -- 142 rows, the
current recommendation -- runs the withdrawn file, and the repo no longer
offers it.

Measuring the two turned out to be harder than it looked, in a way worth
recording. **Neither binary loads the other's weights**: `ds4-metal ba01f5d`
refuses the new build and `ivan/qwen3.8-flash-next bd9cfbc` refuses the old
one, both with `deepseek4.block_count missing`, against a control where each
engine loads its own weights on identical flags. So the quant and the engine
are welded together and the comparison is a **stack** comparison by
construction. `scripts/decode_ab_stack.sh` is the shape that expresses it --
each arm carries its own engine tree, GGUF and PLE sidecar -- and it prints
the two-variable warning beside the arms so the number cannot be quoted
without it.

Four runs:

    decode   q4kimat/q40old  1.095  (+9.5%)  spread 3.5 pp, 8/8 frontiers
    prefill  q4kimat/q40old  0.755 (-24.5%)  spread 7.5 pp, 0/8 frontiers
    prompt   promessi_sposi.txt (1298 KiB)

**k=3 is where both collapse**, a third independent confirmation of #136's
threshold on new data. One run would have put prefill anywhere from -18.4% to
-26.0%.

It is a trade, not an upgrade, and **the deciding variable is unmeasured**:
the author's claim was about accuracy, and nothing here measures accuracy.

Two findings fell out. The old stack's prefill decays **-22.3% within a run**
against the new one's -9.3%, so its advantage is largest cold. And the fans
held `auto` at ~3450/3730 rpm against a 5349/5777 maximum with the die at
66-69 C -- so #116's manipulation check would pass, which was the failure mode
most likely to void that experiment.

Along the way, two corrections and a guard:

* I reported `ds4-bench` as having no `--ple` flag, on two issues, off a
  `--help` grep. It has one, undocumented, at `ds4_bench.c:275`. Reporting a
  capability as absent is the expensive direction to be wrong in -- nobody
  re-checks something ruled out. `AGENTS.md` has it.
* PLE support is **absent from `antirez/ds4` entirely**; only ivanfioravanti's
  branches carry it. The model we recommend runs on no upstream ds4 build
  (#141).
* `save_transcript()` no longer overwrites. #112's pre-remedy transcripts were
  destroyed because later sweeps wrote the same filenames into
  `~/bench-logs/`, and the before-side of that issue's only question is
  unrecoverable. A colliding write now goes to `<name>.stdout.2.jsonl`.

**2026-09-04, evening. #112 item 2 cannot be measured where it was posed.**

The multi-turn death rate is 3/90 under the restart protocol; detecting a drop
to zero needs ~230 trials per arm, about 20 hours, and halving it ~1,400. The
8.9 pp gap either side of the remedy commit is not evidence about the remedy:
`f2fcc1f` bundles three changes and the protocol changed the same day, worth
+6 passes on its own.

`scripts/cohort_split.py` reproduces that split and -- because
`client_version`, `context_tokens` and `model` are all constant across it --
**prints what it cannot see**. No row records the measurement protocol, so a
reader who stops at "no SPLIT" concludes the opposite of the truth.

The redirect is cheap: a strip-toggle A/B read through
`tool_error_conditional.py`, 2 runs per arm, 4-8 hours, with the standing
caveat that the conditional is a proxy whose link to deaths has never been
measured.

**2026-09-04, later. Clients are recorded, not pinned (#131).**

The morning's work on #131 built a pin and made `preflight.py` refuse when an
installed client differed from it — the one place preflight refused rather
than warned. By evening the operator had reversed it, and the reason is worth
keeping: **this laptop is a daily driver first.** Pinning the agent clients
holds a developer's own tools back to serve a measurement, and a guard that
would be overridden every time is worse than none, because it teaches people
to type the override without reading it.

The rule is now *run the current version of everything*, so preflight asks the
opposite question. It warns when a client is **behind** its release and prints
the upgrade command; it never upgrades anything, because a harness that
updates the tool under test moves the version mid-batch. On its first run it
found two: codex 0.152.0 against 0.153.3, and opencode 1.18.27 against
1.18.28.

`client-pins.toml` became `client-versions.toml` and `scripts/client_pins.py`
became `scripts/client_versions.py`. A file named "pins" that pins nothing is
the kind of name that misleads a reader a month later.

**The enforcement moved to the row, where it belongs.** With nothing pinned,
`client_version` is the only thing that makes a comparison recoverable across
an update, so it cannot rest on discipline: `results.write_row()` now excludes
any row that does not carry one. Excluded, not refused — losing an expensive
trial to a missing field is worse than storing one that can never enter an
aggregate, which is the trade that function already makes for a schema
violation. The 979 rows that predate the field are grandfathered and are never
re-written.

Also recorded, in code rather than prose: how each client updates itself, read
out of the shipped binaries with `strings`. All three self-updaters are
deliberately left **on**. Knowing the switch is not for flipping it — it is so
preflight can say, in a log, that the version under a batch can move.

The claude pin moved 2.1.260 -> 2.1.261 before being removed entirely. Worth
noting what that revealed: the 326 claude rows on this machine span 2.1.233 to
2.1.252, and **no row was ever measured at 2.1.260**. The pin had been
protecting comparability with nothing.

**2026-09-04. A prefill figure now carries its prompt (#140).**

`scripts/decode_ab.sh` has taken `PROMPT` as an environment variable with a
default since it was written in 91ca9ff, and nothing recorded which prompt a
run used. That was invisible until @adamlawi posted on ds4#952 that the
prompt decides the Q4-vs-Q8 prefill answer: on one box with identical
binaries, +2.5% with a 135 kB prompt and parity with a 405 kB one. Our own
four-run recheck read parity on a 1298 KiB prompt, which fits his result — and
we had published that figure ninety minutes earlier without naming the file.

Three places changed, in the order a number travels:

* `scripts/prompt_meta.py` (new) stamps `prompt_file` and `prompt_bytes` onto
  every row of every ds4-bench CSV, so a CSV carries its own provenance when
  it is copied out of its directory. Re-stamping with a *different* prompt is
  refused rather than overwritten — that would mean one run directory holds
  two regimes.
* `decode_ab_report.py` names the prompt in the quotable line and **refuses to
  pool prefill across prompts**: runs that do not share one are two results,
  not one with more samples.
* `scripts/backfill_prompt_meta.py` (new) wrote a sidecar for the eight run
  directories measured before any of this. They are marked `inferred` and
  print as inferred wherever they are quoted, because a stamp claims the run
  recorded it and these did not. The inference is checked, not assumed: the
  PROMPT default has one entry in the log, each run's `start-state.txt`
  harness line carries no `PROMPT=` override, and the file is byte-identical
  in every ds4 tree here. A run whose harness line *does* override PROMPT is
  skipped and named.

`1298 kB` as quoted in this project was bytes/1024, so it is written `KiB`
now. The unit was wrong; the figure was not.

Item 3 of #140 — whether to amend the ds4#952 comment to name the prompt — is
an outward-facing edit and stays with the operator.

**2026-09-04. Backfilling one field found a confound in the published
recommendations ([#137](https://github.com/evanwtf/local-llm/issues/137)).**

`client_version` was added to new rows this morning so #104's finding could be
applied to a single row. Backfilling it onto the 1204 historical rows where it
is derivable took ten minutes and immediately showed something nobody had
looked for:

    1.18.25   ds4, ds4anthropic, gemma4, gemma426, glm53ds4, ornith15,
              qwen, qwen36, qwen36coding, qwen38fnq3, qwen38fnq3lms
    1.18.26   qwen38fnq3reap
    1.18.27   qwen38fnds4, qwen38fnds4mtp7shim, qwen38fnds4shim

**No cell mixes versions -- every backend is internally consistent. But the
split falls along the axis we compare.** The three newest ds4 backends ran
under OpenCode 1.18.27 and everything older under 1.18.25, so every
cross-backend OpenCode comparison involving them is also a comparison of two
client versions. 35 backend pairs are disjoint this way.

`RESULTS-agent.md` is clean. **`RECOMMENDATIONS.md` has two affected tables**,
both wall-time -- median/worst/spread, and seconds per 1k output tokens --
which is precisely the axis #104 measured the client moving, by roughly
doubling median turns on the other tier.

Nothing has been edited in either document. Whether this warrants a footnote,
a column, or re-running the older cells depends on measuring whether the
boundary matters on this machine, and no cell spans it so the rows cannot
say. The pin from this morning stops the next boundary being crossed
unnoticed; it cannot un-confound what is already taken.

**The data was always there.** The version sat on every row from the start,
keyed by client name inside `env` alongside every other client on the
machine, so seeing this needed a join across 1394 rows and nobody had a
reason to make it. That is the argument for `client_version` as a field
rather than a lookup: the first thing the lookup found was this.

---

**2026-09-04, late afternoon. The queue worked in order until the clock, and
what the tools said back.**

Continued from the entry below, under one instruction: no ad-hoc analysis,
everything committed and tested. Eleven more commits.

- **[#136](https://github.com/evanwtf/local-llm/issues/136) closed.** "How
  many runs are enough" is answerable from the two four-run datasets: take
  every k-subset and see how far the answer could have moved. **Three is
  where both collapse** -- #118 goes from 4.7 pp of spread at k=1 to
  **0.1 pp** at k=3, because a median over any three of "three tight, one
  outlier" lands in the cluster. That is the empirical form of the operator's
  rule of three, reached from the data rather than assumed. Item 3 is served
  by making the quotable line carry its own run count, since linting prose
  for it would be brittle.

- **[#4](https://github.com/evanwtf/local-llm/issues/4): two directions and
  the constraint.** `shape.py` records structural proxies over the lines a
  patch adds, so a quadratic and a linear solution that both pass differ at
  `max_loop_depth` 2 against 1. `prepare_env` runs `uv sync --frozen` before
  the agent sees the checkout, removing the confound where wall time includes
  working out how to run pytest. And `test_task_definitions.py` checks every
  task statically -- symbol present at the pinned commit, body removable,
  prompt naming its file -- so a typo no longer costs a twenty-minute trial.
  **Directions 1 and 4 turned out to be already done**, 3 of 15 tasks using
  `targets` and 1 with `keep_docstring = false`, which is worth recording so
  nobody re-derives it.

- **[#120](https://github.com/evanwtf/local-llm/issues/120) has evidence from
  rows we already hold.** Pass rate by trial index is flat pooled across all
  backends (90.6 / 94.7 / 90.6) and drops on both ds4-shim backends
  (89.3 / 93.3 / **70.0** and 58.3 / 60.0 / **40.0**). The effect is not a
  property of the harness or the task set, which would show everywhere. It
  does not identify the variable -- `trial` counts repetitions, not uptime.

- **[#112](https://github.com/evanwtf/local-llm/issues/112) remedy 2 is at
  least specified.** It shipped unmeasured and untested; five tests now pin
  what it does, including that a pure-degeneration turn is left verbatim so
  the evidence stays in the transcript rather than being erased by the thing
  meant to observe it.

- **[#96](https://github.com/evanwtf/local-llm/issues/96) is less ready than
  it looked.** The PR it cites, `jundot/omlx#3118`, is "rebuild distributed
  inference as Cluster v2 Beta" -- not tail continuation. A repository search
  does not surface the described change under any obvious name. The first
  step is identifying it, not building it.

- **[#64](https://github.com/evanwtf/local-llm/issues/64) audits itself now.**
  Both restart-between-trials cycles run `kv_prefix_audit.py` over their own
  server logs when the cycle ends. The four logs that produced the 443,974
  figure were the ones that happened to survive; measuring while the logs are
  in hand beats hoping someone runs it later.

**AGENTS.md gained three traps**, each hit more than once in a day: one
definition of "done" in one place, name both directions of a ratio, and
assert what a script *does* rather than what it *mentions*. The common shape
is a check that cannot tell its subject from something that merely resembles
it -- which also describes the CI failure earlier today, where an absent
client read as a drifted one.

---

**2026-09-04, afternoon. Six queue items advanced, each as committed code
rather than an answer in a terminal.**

The morning's work was guards. This was the queue, worked in order, with one
constraint from the operator: no ad-hoc analysis, everything durable and
tested. Seven new scripts and modules, all with tests.

- **[#64](https://github.com/evanwtf/local-llm/issues/64) has a number now**
  (`d2ad272`). `kv_prefix_audit.py` parses ds4-server's cache-miss lines and
  sums `prompt - common` as re-prefilled tokens: **443,974 tokens, ~21
  minutes of prefill at 360 t/s**, across the four logs we hold, every miss
  `reason=token-mismatch`. The issue previously rested on three lines from one
  trial. The detector was wrong first and the data said so -- it required
  `prompt` to rise while `common` stayed pinned, and a real log shows `common`
  pinned while `prompt` drifts *down*, re-prefilling ~830 tokens a turn. The
  direction of `prompt` is incidental.

- **[#50](https://github.com/evanwtf/local-llm/issues/50) is testable without
  a prompt dump** (`2e41c50`, `c66fcfb`). `prefix_stability.py` names the first
  block inside the cache horizon whose content changed, and flags the shape
  #50 describes -- a block both marked cacheable and carrying a number that
  moves every turn. `SHIM_PREFIX_LOG` makes payloads collectable as **digests
  only**; a test asserts a payload containing "SECRET CLAUDE.md CONTENTS"
  produces a line containing neither string. The shim's existing `SHIM_DUMP`
  writes whole payloads, which `.gitignore` says must never be committed.

- **[#112](https://github.com/evanwtf/local-llm/issues/112)'s cheapest remedy
  is measured** (`fb9690a`). Over 398 tool calls in 43 sessions the failure
  rate runs 2.9% with a clean context, 4.9% after one error, 6.2% after two.
  **Fourteen failures total**, so the tool prints "a direction to test, not a
  measured effect" on every run, unconditionally -- a row reading `1 / 1 |
  100%` is as easy to over-read as a comparison line.

- **[#136](https://github.com/evanwtf/local-llm/issues/136) item 4 answered,
  against my own guess** (`233f33d`). `--legacy` computes the pre-`98bc79b`
  ratio-of-medians beside the paired statistic. On both four-run datasets the
  legacy statistic reported a *wider* spread (6.1 vs 4.7 pp; 1.8 vs 1.5 pp).
  It did not make runs look falsely consistent -- it added noise on top of
  real noise. The issue had suggested the opposite.

- **[#131](https://github.com/evanwtf/local-llm/issues/131) refuses now**
  (`82205f0`, `0b164d9`). `client-pins.toml` plus a preflight check that
  **refuses** rather than warns, verified in both directions. OpenCode's
  updater is documented in the runbook -- `OPENCODE_DISABLE_AUTOUPDATE`, or
  `"autoupdate": false`, with `opencode upgrade` as the deliberate path.
  Neither switch is set on this machine, and preflight says so. Live proof
  the risk is not hypothetical: 1.18.27 installed against 1.18.28 latest,
  today.

- **[#116](https://github.com/evanwtf/local-llm/issues/116)/[#120](https://github.com/evanwtf/local-llm/issues/120):
  fan RPM rides with every temperature** (`7079428`). #118's run 4 recorded
  48.4 -> 67.6 C and could say nothing about cooling, because no fan speed sat
  beside it. Read only: a test parses the AST and asserts every `fancontrol`
  invocation's verb is `status`.

**Three tools found bugs in themselves during the work**, which is the
argument for building them rather than answering in a terminal: the prefix
detector's rising-prompt assumption, the between-run comparator measuring
frontier spread instead of repeatability, and a completeness check that read
six files as a finished run three separate times before it was made one
definition in one place (`a03ca8d`).

---

**2026-09-04. Four guards, and the reason each was missing
([#129](https://github.com/evanwtf/local-llm/issues/129) and
[#130](https://github.com/evanwtf/local-llm/issues/130) closed;
[#131](https://github.com/evanwtf/local-llm/issues/131),
[#84](https://github.com/evanwtf/local-llm/issues/84) and
[#133](https://github.com/evanwtf/local-llm/issues/133) advanced).**

A morning of the unglamorous half — nothing here makes a number better, and
three of the four exist because a measurement was already wrong and nobody
could tell.

- **CI redness reaches preflight** (`aa406f2`). Both red streaks this repo has
  had were found by a person going looking; the second ran 20 runs over 17
  hours and took seven minutes to fix. The check and its ten tests had already
  been written and were never called — `log_ci_status` was dead code, which is
  the failure #129 is *about*, one level up. The call site now has its own
  test, because ten tests proving a function behaves prove nothing about
  whether it runs.

- **Arm order alternates, and the row says where it sat** (`38956f2`). The
  suite ran `backends.items()` in the same order every trial, so one backend
  was always last. @adamlawi measured that positional bias on antirez/ds4#952
  as larger than three of the four effects being compared. `run_position` and
  `run_arms` are deliberately absent rather than defaulted to 1 on old rows:
  defaulting would claim every existing row ran first, which is the bias being
  looked for.

- **The client version is on the row** (`225b90c`). It was always in `env`,
  keyed by client name alongside every other client installed, so reading it
  back needed a join. #104's finding — OpenCode 1.18.26 → 1.18.27 roughly
  doubling median turns — has to be applicable to one row or it cannot be
  applied backwards at all. Stored exactly as the tool printed it; normalising
  would invent a format and lose the string a release note is looked up by.

- **The ollama sampler boundary is named before you cross it** (`e8262d2`,
  `797545a`). For every other tool `BEHIND` means "upgrade". Across ollama
  0.33.3 it does not, and preflight was saying `BEHIND` unqualified — nudging
  toward the one action that silently changes which sampler a row gets.
  `env.ollama` now records the build alongside the regime tag; 343 of 1394
  rows carried an ollama version and 1051 did not.

- **A machine-scoped run lock** (`108419c`, `c797ce8`, `5cc26aa`). preflight
  sees processes and cannot see intent; restart-between-trials spends minutes
  with the server deliberately down, and a scan in that window truthfully says
  "all clear" while the machine is committed for hours. `run.py`,
  `decode_ab.sh` and `decode_ab_engine.sh` now claim it. A stale lock is
  reported with everything it recorded and **not** taken; a corrupt one reads
  as held. This is the one place preflight refuses instead of warning, and the
  asymmetry is the point: process detection is inferential, a lock is a
  declaration.

**Two things found by running the code rather than reading it.** The lock's
first version recorded preflight's own pid — and preflight exits immediately,
so the lock was stale the instant it was written and the next acquire
cheerfully reported a dead owner; hence `--owner-pid`, with shell callers
passing `$$`. And ollama had *already* moved to 0.33.3 on this machine, on
2026-09-03 18:19. The ninety rows written since are all `qwen38fnds4shim` or
`qwen38fnds4mtp7shim`, which do not go through ollama, so the boundary is not
yet crossed in the data — the next ollama-backed row is the first under the
new precedence and must not be pooled with earlier ones.

**Still open, and named rather than implied.** #131 records the client and
still does not pin it; OpenCode self-updates on both machines and preflight
warns rather than refuses. #84 has the regime and the build but no row yet
carries the sampler *numbers* — `probe_ollama()` has no GGUF path and
`/api/show` does not reliably give one. #133's lock does not recompute the
memory guard at acquisition, so it says "busy" but not "and there is room".

---

**2026-09-04. ds4 PR #964 reproduces on this M5 Max: +16.5% paired decode,
bit-exact ([#118](https://github.com/evanwtf/local-llm/issues/118), closed).**

The one item where we advance someone else's project with data only we have:
every Apple measurement on antirez/ds4#964 was M3 Ultra, and @trueimage had
tagged Evan directly. Measured PR head `8969dbb` against `main` at `b0a147a`
(merge-base check: the PR is 42 ahead, 0 behind — we measured it against its
own base, so no stale-baseline discount applies). Two worktrees, each built in
place so each arm reads its own `metal/*.metal`; 3 repetitions with arm order
alternating per rep — the [#130](https://github.com/evanwtf/local-llm/issues/130) rule, applied here before the harness itself got it;
8 frontiers, 2048–16384; 128 greedy tokens each.

- **Decode: +16.5% paired median across frontiers** (per-frontier 1.154–1.205,
  faster at 8 of 8, flat across context — the same shape as the PR's own
  curve). Pooled over all 24 (frontier, rep) pairs: median +16.9%, mean
  +17.6%; reps 2–3 alone: median +17.3%, mean +18.1%. Rep 1 ran ~9% high on
  *both* arms (mmap pages still faulting in); pairing within a repetition is
  what absorbs that.
- **Prefill: −1.6% paired median across frontiers** (range 0.965–0.995, faster
  at 0 of 8; pooled mean −4.0% under the rep-1 cold drag). The PR's "prefill
  flat within 0.1%" reproduces approximately, not exactly: a consistent ~1.5%
  prefill cost on this hardware.
- **Output: byte-identical** between arms at both attention regimes (2048 full
  attention, 16384 compact DSA). Caveat stated on the issue: the greedy tail
  decodes to empty-text tokens on this corpus, so the printed bytes are proven
  identical and token-ID identity is strongly suggested, not strict. Compare
  the `decoded text:` lines only — the capture files also carry
  nondeterministic Metal timing banners, so a whole-file diff differs.

Direction, shape and exactness reproduce; the magnitude is ~45–50% of the M3
Ultra numbers. The cleanest available explanation: an occupancy win pays less
on **40 GPU cores than on 80**, which fits — a memory-traffic cut would pay
*more* on the narrower part, which does not fit. Not isolated, only recorded.

**Correction, same day.** The first report said +20.0%. That number came from
`scripts/decode_ab_report.py` dividing each arm's median independently — a
ratio of medians, not the paired statistic its own docstring promised. With
~9% rep-to-rep drift, the two medians can come from different repetitions, and
the drift re-enters as noise: the ratio divided rep 3's baseline by rep 2's
branch at ctx 2048. The error direction is data luck, not bias — the same
defect read +14.6% where the paired figure is +15.7% on #52's first August
pass. The peer session caught it before the draft reached antirez. The script
is fixed and tested (`tests/test_decode_ab_report.py`), and every affected
number on #118 was recomputed. The prior A/Bs summarized by the same script
were recomputed where their CSVs are committed — #52's two August passes
(`2bf44c1`, `95880b4`), which read 1.146/1.155 unpaired vs **1.157/1.141**
paired, with the pass-2 prefill going from 0.998 to 0.979. #91's September
runs kept their raw CSVs in /tmp, which is gone, so their figures stay
unrecomputable. The first propagation of this correction mapped #52's
directories onto #91's runs — the commit dates settle the ownership — and the
misattributed issue comments were rewritten in place, marked as corrections.

Two durable method facts fell out of it, both now in the
[runbook](m5max-runbook.md): the ds4 Makefile tracks no header dependencies, so
`make clean` after any checkout (the first launch linked 3 mixed-vintage
objects and was stopped before anything was measured), and the model loads by
mmap — there is no "load 2 is faster" signal to read.

Harness: `scripts/decode_ab_engine.sh` @ `c1c72b0` with
`scripts/decode_ab_report.py` for the paired statistics. Data:
`benchmarks/ds4/decode-ab-964/` (6 A/B CSVs, 4 exactness captures). Method,
results and a draft comment for Evan to post on the PR thread (written here,
not posted there) are on
[#118](https://github.com/evanwtf/local-llm/issues/118#issuecomment-5540223842).
The queue item is done and removed; `NEXT.md` is back to nine items.

---

**2026-09-04. `NEXT.md` is the queue and nothing more.**

The file had regrown into a diary: 605 lines holding four machine-state
snapshots, a ~150-line traps section, an answer table, and closed-work lists —
against the ~150 lines of actual queue. It went back to being what
`AGENTS.md` says it is: the ranked table, the goal it is ranked against, one
machine-state snapshot, and pointers. Nothing was deleted; everything moved to
its permanent home:

- **Done work** → this changelog, as the two entries below (the 2026-09-02
  closures and the late-2026-09-03 arms). The staging-area section is gone;
  a finding lands here the day it happens.
- **Durable machine operations** → the new
  [`docs/m5max-runbook.md`](m5max-runbook.md): the Metal ceiling (why it is
  required, the LaunchDaemon and its install trap, the `b0c31af` guard that
  cannot fire on 128 GiB hosts), the exact ds4 server argv the published rows
  were taken with, the two KV directories, the engine trees and preserved
  builds, the weights inventory, the client configs, and the download notes.
- **Traps** → `AGENTS.md`, as sections in its house style, minus the ones it
  already held. New there: never `pkill` `run.py` (restore via
  `run.restore_targets()`), MTP is not a speed-only flag, the wire-call rule
  extended with the `stream: true` recurrence, fast-and-unusable, ~10% session
  drift → bracket with A-B-A, coherence-check at temperature 0, the `pgrep`
  poll that never exits, nothing feeds `results.verdict()` except the oracle,
  force-pushed preview branches (ancestry, not count), and `excise()` writing
  the file.
- **The answer table** (best combination, pass rates and CIs) → dropped from
  `NEXT.md`; it was already persisted in `RESULTS-agent.md`, which is where a
  published figure belongs.
- **`TESTING-SET.md`** gained `qwen38fnds4shim` (135 valid rows) and
  `qwen38fnds4mtp7shim` (90) in its measured table, moving them out of
  "configured but unmeasured", where they no longer belonged.

Also filed: [#132](https://github.com/evanwtf/local-llm/issues/132) — preflight
flags the ds4-server behind the shim as stale when only a shim backend is
selected. The warning is correct about the ports and wrong about the
conclusion: stopping the named process would kill the run's only backend. It
had been sitting in a machine-state snapshot as "not yet filed".

And caught by the suite during the prune, so worth a line: the
`RECOMMENDATIONS.md` tables had gone stale after the 2026-09-03 evening runs —
the arm B re-run and kv-32768 rows (90 rows) landed in `results.jsonl` but the
splice was never re-run. Re-spliced: `qwen38fnds4shim` 116/135 and
`qwen38fnds4mtp7shim` 50/90. The splice being part of "finishing a batch" is
AGENTS.md's rule; this is the second red-streak-shaped miss found by a person —
or in this case a test — looking.

---

**2026-09-03, late. #77 closed in both directions: MTP is a net cost on this
workload, and the session decline is not disk KV.**

- **Arm A re-run under restart-between-trials: 42/45 (14/14/14)**, against
  36/45 (13/13/10) on a single continuous server. The third-trial collapse was
  server state, not model context. Full write-up and the paired statistics on
  [#77](https://github.com/evanwtf/local-llm/issues/77).
- **Arm B (MTP 7) under the same protocol: 25/45 (9/6/10)** — identical to its
  no-restart total, so the restart removed the session decline but not the
  loss. **MTP is a net cost on this workload, not a help.** With the sampler
  caveat in `AGENTS.md`, the pass-rate gap is not attributable to speculation
  alone; the wall-time comparison is the clean one, and it also shows no gain.
  The issue closed, and with it the cautionary case for the queue's ranking
  rule: a fully executed, well-instrumented speed comparison whose honest
  answer was "no difference measured".
- **The kv-32768 test: 38/45 (12/15, 15/15, 11/15)** — the disk-KV budget
  raised 4x, restart-between-trials held. Disk KV is not the mechanism behind
  the session decline. Which state degrades a session is now
  [#120](https://github.com/evanwtf/local-llm/issues/120).
- **Restart-between-trials is now the protocol** while the mechanism is
  unknown, and the cycle is scripted: `scripts/restart_between_trials.sh` and
  `restart_between_trials_armB.sh`, committed so the protocol does not live in
  anyone's head.
- **#112 was re-scoped** to the tool-call degeneration loop it is named after;
  its session-decline half moved to #120. Successors filed:
  [#119](https://github.com/evanwtf/local-llm/issues/119) (unsloth fork
  decision) and #120.
- **The queue was re-ranked against the goal, not against engine speed**
  (`7545d66`): decode rate does not predict agent wall time (measured three
  times), the pass-rate table has saturated (#4), and two of the loudest
  results measured our own setup rather than the model (#112, #120). A +35%
  decode claim now ranks below a defect that makes a real session slow, wrong,
  or unmeasurable.
- **[#131](https://github.com/evanwtf/local-llm/issues/131) filed** after
  [#104](https://github.com/evanwtf/local-llm/issues/104): OpenCode updated
  itself 1.18.26 → 1.18.27 on both machines unasked, and on the Linux tier that
  alone roughly doubled median turns on repository tasks. Nothing pins the
  client; no row records its version.

---

**2026-09-02. The fast-pack lands, CI goes green, and four issues close.**

- **The Qwen3.8-Flash-Next DS4 Q4 fast-pack arrived**: 113 GB (base 79 + PLE
  32 + MTP 1.6 + vision 0.5) at `~/models/qwen3.8-flash-next-ds4-q4` — a DS4
  fast-pack, not a llama.cpp GGUF. It carries a deliberate symlink
  `...Q4KExperts...gguf` → `...Q40RoutedExperts...gguf` because the manifest
  names the former; **keep the symlink**. Our copy of the manifest predates
  HF's `2026-09-02T23:07Z` update; the weights are identical
  (`tensor_manifest_sha256` unchanged) — re-fetch only the manifest before
  quoting its recipe.
- **`~/git/ds4-metal` cloned** (ivanfioravanti's fork), branch
  `qwen3.8-flash-next` @ `2021dda`; `make -j8` builds clean with no patches.
  Fast-forwarding to upstream `ba01f5d` later proved a **no-op for the binary**
  — both commits touch only docs, a test fixture and a repack script.
- **`~/.config/opencode/opencode.json`**: the `ds4` provider gained
  `qwen3.8-flash-next-q4`, and a new `ds4qwenshim` provider points at the
  tool-format shim on :8101 (backup alongside the file).
- **[#108](https://github.com/evanwtf/local-llm/issues/108) closed — CI green
  again after 21 red runs.** Two failure classes, both found by a person
  looking: 40 consecutive failures on a shallow clone (the workflow ran fine
  and the *history* it needed was absent), then a `w/` in a CPU string put a
  slash in a directory name. That is the case for
  [#129](https://github.com/evanwtf/local-llm/issues/129).
- **[#106](https://github.com/evanwtf/local-llm/issues/106) closed — the
  oracle deadlocked on its own output.** Past a 64 KiB pipe buffer the
  listener blocked forever, the watchdog killed it, and the kill was recorded
  as a **model** failure. Present since the function was written, reachable
  only once a backend did zero work. Fixed in `44c3519`; 1,181 rows audited, no
  evidence of past corruption.
- **[#98](https://github.com/evanwtf/local-llm/issues/98) closed** — thermals
  platform guard; preflight no longer invents a Metal ceiling on Linux.
  **[#85](https://github.com/evanwtf/local-llm/issues/85) closed** — the
  hardware restructure; `RECOMMENDATIONS.md` stayed at root.
  **[#91](https://github.com/evanwtf/local-llm/issues/91) closed** — ds4 PR
  #621 re-tested at `6a20b13`: decode 1.155x, 32/32 frontier points (a
  ratio-of-medians; its raw CSVs lived in /tmp and are gone, so no paired
  figure exists — see the 2026-09-04 correction); #952 supersedes it at the
  same commit. Earlier that day: #89, #87, #90.

---

**2026-09-03. The ds4 Qwen cell, from 0/45 to 36/45, and the variable nobody
had registered as one.** ([#94](https://github.com/evanwtf/local-llm/issues/94))

`qwen38fnds4shim` under OpenCode: **30/39 excision (median 139.9 s) + 6/6
script = 36/45**, harness `47a1d9f`. The previous measurement of the same
model, engine and pack was **0/45**.

**What the 0/45 measured was `stream: true`.** ds4 logs `invalid tool call
returned as assistant text finish=stop [text_len=231 ...]` -- it believes it is
handing back 231 characters of assistant text, and off-stream it does. On-stream
it does not: no content, no `tool_calls`, `finish_reason=stop`, an empty turn.
One identical request, arms interleaved, 12 samples each:

    stream:true    tool_calls 1/12   nothing at all 11/12
    stream:false   tool_calls 7/12   XML as text     5/12

OpenCode sets `stream: true`. Every one of those 45 trials ended on turn one
with `solution_empty: true` because the turn genuinely arrived empty.

**The lesson is one this repo already had, and still paid for.** The shim's
format instruction measured 12/12 synthetic and 0/6 -> 1/6 under OpenCode on the
same text, and three sessions were spent varying the instruction. The synthetic
harness sent `stream: false` and OpenCode sent `stream: true`: the two arms
differed in a variable that was never registered as one. That is exactly
*"Observe the wire call, not the status code"*, which was written after the same
mistake cost a 13-trial run in August. **A control that differs in an
unregistered variable is not a control**, and the tell was available the whole
time -- the server log said `text_len=231` while the client received zero bytes.

Also: **the engine numbers were never the problem.** Decode 40.2 t/s, prefill
1107 t/s, 74.3 GiB resident with the 32 GB PLE table genuinely streaming from
SSD. All of that was true on the day the same setup scored 0/45. A backend can
be fast, correctly quantised, thermally fine and completely unusable, and only
the agent harness says so.

The shim now asks upstream for a non-streaming completion when a request carries
tools, translates the XML dialect if it still appears, and synthesises the SSE
stream back. **The translator fired 28 times in 45 trials** and zero times on
the twelve synthetic samples -- so OpenCode's 26 KB prompt really does drive the
dialect, it just was not the thing breaking the runs. Three confounds now ride
on this backend and `tasks.toml` names all three; the biggest is that its rows
did not stream from the engine.

**Residual, and it is a different defect.** All nine failures are
`solution_empty`, none is wrong code. After a tool error enters the conversation
the model narrates about the format and then emits stacked bare `<tool_call>`
opens -- 38 of them in one transcript -- with no function name to recover. The
translator declines these on purpose; a fabricated tool call would be worse than
an empty turn because it would run. A third dialect (Claude's `<invoke
name=...>`) was found in the same transcripts and is now handled, **after** the
45 trials and so unmeasured.

**Rebuilding ds4-metal onto `ba01f5d` was a no-op for the binary.** Both commits
touch only `QWEN38_FLASH_NEXT.md`, a test fixture and a repack script; `make`
reports up to date. Our existing measurements were already on his commit
functionally -- worth knowing before anyone re-runs a batch to "get onto it".

**Still worth reporting upstream:** ds4's streaming path silently drops
assistant text it has explicitly decided to return. Any streaming client sees an
empty turn and no error.

---

**2026-09-01 late. Gemma 4 26B A4B, a machine outage, and a sweep tool that
paid for itself on its first run.**

- **`gemma426` 11/11** (Gemma 4 26B A4B, the MLX Fast leaderboard model). Against
  `gemma4:31b-mxfp8` on the same tasks and client: `script-transform` **40.7s
  against 219.2s**, `storage-blob-put` 158.0s against 358.1s, `mbox-scan` 123.1s
  against 464.1s, `script-reverse` 16.5s against 41.6s. 4B active against a 31B
  doing far more work. **It is now the fastest Gemma we hold and a serious
  candidate for RECOMMENDATIONS' third slot at 51 GB.**
- **One `gemma426` row is excluded as an intervention.** Its `mbox-scan`
  implementation buffered a file the task says to stream, and the *oracle* --
  `pytest tests/test_mbox.py` -- reached **49 GB RSS** and drove the machine into
  swap: 19.6 of 20.5 GB used, 97k pageouts, everything at 0% CPU. Killed at ~6
  minutes to give the machine back, so both the FAIL and the 791.1s were decided
  by the kill. **The pathology is the interesting part**: a plausible *wrong*
  answer, which [#4](https://github.com/evanwtf/local-llm/issues/4) says this task set cannot produce.
- **[#82](https://github.com/evanwtf/local-llm/issues/82) fixed, three items of four.** The oracle had been handed the agent's
  1800s timeout for a step that takes 0.1s; it now has `ORACLE_TIMEOUT = 300`
  and an 8 GiB ceiling. `RLIMIT_AS` is unusable on macOS -- measured -- so
  `memcap.py` polls the process tree and kills the group, reading **descendants**
  because the memory lives in a grandchild of `uv run pytest`. `peak_rss_gib` is
  now on every row, which turns this whole class from an operator noticing the
  machine is slow into a column.
- **[#84](https://github.com/evanwtf/local-llm/issues/84), and the tool that found it.** `scripts/upstream_sweep.py` sweeps all 18
  repos we depend on in one command; SOURCES.md renders its `WATCHED` dict and a
  test fails on drift. Its first real run found
  [ollama#16471](https://github.com/ollama/ollama/pull/16471), merged hours
  earlier, making GGUF sampler KVs outrank Ollama's built-in defaults.
  **Upgrading past 0.33.2 silently changes the sampler for `ornith15`** -- our
  fastest measured backend, quoted in RECOMMENDATIONS, and the only one with no
  Modelfile parameters. [#36](https://github.com/evanwtf/local-llm/issues/36) measured that this exact class of change took a pass
  rate from 20/21 to 7/15.
- **[#83](https://github.com/evanwtf/local-llm/issues/83): two models failed an easy prompt by spending the whole budget
  thinking.** `qwen3.6:27b-mlx` and `gemma4:12b-it` both returned no answer with
  `stop_reason=max_tokens`. Same size, same runtime and one generation apart,
  `qwen3.8:27b-mlx` answered the same prompt in 3.6s against 143.1s. [#63](https://github.com/evanwtf/local-llm/issues/63) settled
  that thinking *helps correctness on ds4*; it did not ask whether unbounded
  reasoning consumes the budget before an answer exists.
- **Three bugs caught by running rather than reading**, in one evening: the
  Metal tensor probe read stdout when ggml logs to stderr; `RLIMIT_AS` looked
  usable until it was called; and `proc.returncode or 1` would have recorded
  **every passing oracle run as a failure**. All three passed review by eye.

**2026-09-01 evening, second half. Two machines, and the first task-set
discrimination this project has produced.**

- **`gemma4` 12/12 under OpenCode ([#16](https://github.com/evanwtf/local-llm/issues/16)).** Medians: `script-reverse` 41.6s,
  `script-transform` 219.2s, `storage-blob-put` 358.1s, `mbox-scan` 464.1s.
  `mbox-scan` ran 317.5 / 464.1 / **1315.6s** -- a 4.1x spread on one task, one
  model, back to back, and the slow run still passed.
- **The dense 12B tier is dead, and the reason is not speed.** On `desktop`:
  `mistral-nemo:12b` **0/6**, `gemma4:12b-it` **1/6**. Both pass the smoke gate,
  which reads the answer out of the reply; both fail the same task when the
  answer has to be a file. `mistral-nemo` answers smoke prompts in **0.2s** --
  it is not struggling. It emits correct code in a markdown fence and says
  "save it as reverse.py", with **zero tool calls**. The distinction is
  **coding ability against tool-calling ability**, and no leaderboard measures
  the second.
- **`desktop` is scoped and running.** Ryzen 9 7900X, **30 GiB RAM**, RTX
  3080 Ti 12 GiB, Ubuntu 24.04, ollama 0.32.15 already present, **not
  always-on**. uv + OpenCode 1.18.26 installed user-local, both repos cloned,
  harness runs end to end. The 30 GiB figure is what [#20](https://github.com/evanwtf/local-llm/issues/20) was blocked on: it
  makes the `--n-cpu-moe` path real rather than theoretical.
- **Three defects Linux exposed, all fixed.** Preflight invented a Metal
  ceiling off Darwin -- it printed "107.5 GiB headroom under a 107.52 GiB Metal
  ceiling (stock)" on a 30 GiB box. `confinement` went unrecorded, so a Linux
  row and a macOS row looked identical while one had no sandbox. And
  `machine_facts()` now interrogates arch, os, cpu, cores, memory and GPU on
  every run, verified on both machines.
- **The pooling near-miss.** The first Linux run appended **13 rows to the
  tracked `results.jsonl`** and nothing in the harness objected; it surfaced
  only because a later `git pull` refused to merge over them. Now guarded --
  and rows that do not state their hardware count as unknown, not different,
  so the project's own 979-row history does not trip it.
- **`--dry-run` had crashed on every script task** since the class was added.
  `summary` is bound only in the excision branch. The one path that exists to
  verify a task before spending an agent on it was broken for a whole class,
  and stayed hidden because the path is cheap to skip.
- **RECOMMENDATIONS linked to a repo that 404s.** It named
  `evandhoffman/gmail-archive`; the remote is `evanwtf/gmail-archive`. A dead
  link in the one place a reader can check our work.
- **LM Studio retired** (see the engine scope note below), and **`[#80](https://github.com/evanwtf/local-llm/issues/80)` filed**:
  22 models, 518.6 GB, six ever measured.

**2026-09-01 evening. An upstream sweep, and a rebuild that changed nothing.**

- **`qwen4exp` is Qwen3.8-Flash-Next.** It entered llama.cpp as
  [#27742](https://github.com/ggml-org/llama.cpp/pull/27742), so every
  `qwen4exp:` commit upstream is work on the stack RECOMMENDATIONS.md lists as
  the fast pick. Four such commits landed in the 18h window. **This is the fact
  that made the sweep worth running**; nothing in our docs connected the two
  names.
- **[#76](https://github.com/evanwtf/local-llm/issues/76) is answered: no.** `b10729` against `b10751`, same weights, bracketed
  A-B-A so session drift is visible rather than assumed:

  | test | A1 b10729 | B b10751 | A2 b10729 | A1->A2 drift |
  |---|---|---|---|---|
  | pp512 | 1089.3 | 1087.3 | 1086.5 | -0.25% |
  | tg128 | 43.01 | 42.99 | 42.40 | -1.4% |
  | pp512 @ d16384 | 655.4 | 608.7 | 596.0 | **-9.1%** |
  | tg128 @ d16384 | 37.74 | 33.72 | 34.04 | **-9.8%** |
  | pp512 @ d32768 | 467.8 | 463.8 | 449.7 | -3.9% |
  | tg128 @ d32768 | 32.14 | 30.22 | 30.03 | -6.6% |

  **B falls inside the A1-A2 band on every row and the sign flips between
  rows.** That is what no effect looks like. `b10751` is worth adopting for
  #27941's correctness fixes; it is not worth re-measuring the agent suite for.

- **The bracket is the finding, not the verdict.** A single A-then-B run would
  have reported b10751 as **6% slower** at d16384 and been believed. The second
  A run is what turned a 6% regression into noise. Cost: five extra minutes.
- **The M5 tensor-API bug is not ours.**
  [#27461](https://github.com/ggml-org/llama.cpp/pull/27461) was found on an M5
  Max and reads exactly like our machine -- the Metal tensor API probe failing
  silently, prefill running matmuls on general-purpose ALUs instead of the
  Neural Accelerators. Both our builds report `has tensor = true`, because we
  compile with `GGML_METAL_EMBED_LIBRARY=ON`. Checked before writing it up.
- **We are outside #27941's silent wrong-output paths, by flags.** Losing
  indexer keys on a sequence copy needs the OpenAI `n` parameter; pooling a
  block from another sequence needs `--kv-unified` and more than one sequence.
  We serve `-np 1` and ask for one completion. **That makes those flags
  load-bearing, not defaults.**
- **Ruled out so nobody spends time on them:** Kimi K3 landed in mlx-lm at
  **2.78T parameters**; Rapid-MLX's GLM-5.3-Flash needs 165.4 GB active
  (192 GB tier); MTPLX 2.10.2 is mostly an Anthropic-bridge fix for a client we
  no longer test; the three new llama.cpp fa-vec tunings are M2 Pro, M2 Max and
  A18 Pro.
- **`b10729` is preserved** at `~/llamacpp-builds/b10729/bin` (740 MB) with its
  commit in `COMMIT` beside it. It is the binary behind every published
  llama.cpp number. `~/git/llama.cpp` is now at `b10751` with the new build in
  `build2/`; `build/` still holds b10729 as well.

**2026-08-31 evening. OpenCode was never broken, and a new task class found it in one night.**

- **`opencode run` ignores the caller's `cwd`.** It attaches to a persistent
  server holding its own working directory; `--dir` is how you tell that server
  where to work. `run.py` had always set `cwd=worktree` correctly. Fixed in
  `7356460`. Measured on the worst historical cell (`qwen38fnq3`): **1/15 ->
  3/3**, all wrote patches, zero escapes, 6-13 turn runs.
- **Found by accident, from a row that was excluded.** The script task reported
  `reverse.py was never created`; the transcript named
  `~/git/local-llm/benchmarks/agent/reverse.py`. The file was there, and it
  **passed all three oracle checks**. It had been solving the task and writing
  the answer where nobody looked.
- **Three invocation variants tested to be sure it was not us**: plain (as the
  operator runs it), `--format json`, and `--format json` + `sandbox-exec`.
  **All three pass.** Neither our JSON mode nor our confinement breaks it.
- **A new task class: `script-reverse`.** The agent starts in an **empty
  directory** and must produce a runnable CLI script -- filename, argv, stdout.
  No repo, so no export, no fixture, no stash, no history to leak, nothing to
  tamper with. 21 trials in 40 minutes against [#65](https://github.com/evanwtf/local-llm/issues/65)'s 11 in two hours.
- **The client is the dominant cost on a large local model.** Same weights,
  same server, same task:

  | backend | Aider | Claude Code |
  |---|---|---|
  | GLM-5.3-Flash | **6.4 s** | 103.3 s |
  | DeepSeek V4 Flash | 11.7 s | 73.6 s |
  | Qwen3.8-FN Q3 | 12.8 s | 196.5 s |
  | Qwen3.6 (31 GB) | 42.6 s | 43.4 s |

  On the small model the two clients are indistinguishable; on the large ones
  the gap is 6x to 15x. **Weeks of model-level work bought 3-15%; this axis
  moved 16x.**
- **A local pairing beat hosted Opus.** GLM-5.3-Flash under Aider: **6.4 / 6.3 /
  6.4 s** against Opus 5 at 12.6 / 9.7 / 8.7 s. Ranges do not overlap. [#23](https://github.com/evanwtf/local-llm/issues/23)'s
  +/-27.9% band was bootstrapped from the excision suite's variance and is too
  conservative for a class with 2% spreads -- **that interval needs re-deriving
  per class**, not borrowing.
- **`script-transform` written and run by hand.** Qwen3.8-FN Q3 through
  OpenCode produced a correct multi-flag CLI in **36 s including its own
  verification**, and got the fixed-order rule right under three flag
  permutations. **The prediction recorded before running -- that it would pass
  everywhere -- held.** The ceiling is not task size ([#4](https://github.com/evanwtf/local-llm/issues/4)).
- **Five harness defects fixed**: `--dir` (`7356460`), `agent_error` now
  auto-excludes (`74567da`, after counting 16 opus5 client crashes as model
  failures and making the hosted reference read 64% instead of 28/29),
  `tasks.toml` and `results.jsonl` denied to the agent (`456cae3` -- they carry
  the answers), a client naming its own binary no longer reads as an escape
  (`d5d4731`), and `wait_ready.py` replaces a `curl /health` loop that was
  wrong twice over (`9e80454`).
- **`/health` lies.** llama.cpp answered `{"status":"ok"}` with HTTP 200 while
  every completion returned 503, and `curl` exits 0 on a 503. Probe with the
  kind of request the benchmark will actually send.
- **A style rule, in AGENTS.md**: never write "N times faster" -- it is
  ambiguous about direction. Write the time, or the bare pair of numbers.
- **Upstream sweep.** ds4 gained 10 issues/PRs in 24h (PR #920 accelerates
  width-2 MTP verification on Metal; #917 publishes M3 Max 128 GB results worth
  cross-checking). llama.cpp is **49 commits behind** and has shipped fa-vec
  tunings for seven Apple chips with **none for M5** ([#68](https://github.com/evanwtf/local-llm/issues/68)).

**2026-08-31. GLM-5.3 works as an agent. Two of our own defects were hiding it, and a third was inflating every Claude Code time.**

- **GLM-5.3-Flash drives a coding agent**: **10/15 under Aider** (full 3-trial
  cell, 0 escapes, every pass one turn) and **6/7 under Claude Code**. Engine is
  `upstream/main @ ec7642c` — the `glm-5.3-flash` branch **merged today**.
  Write-up: `benchmarks/agent/GLM-5.3-FLASH.md`.
- **It solves two tasks DeepSeek cannot.** `mbox-scan` is **0/3 for DeepSeek**
  (the same wrong 62-byte patch three times, 265/269/270 s) and **6/6 for GLM
  across two clients**. `storage-blob-put` is 0/3 for DeepSeek and passes on
  Claude Code in 607 s. **First result here that is a model difference rather
  than plumbing.**
- **[#63](https://github.com/evanwtf/local-llm/issues/63): thinking was off, and off is worse.** ds4 defaults to high-effort
  thinking; our shim rewrote Claude Code's `adaptive` to **`disabled`**. Measured
  across 8 trivial functions executed against assertions: **off 4/8, on 8/8**,
  and off was **not cheaper** (548 tokens to on's 431 on one task, still wrong).
  Fixed in `218cc5a`. The agent-level proof: three failures became three passes,
  and `storage-blob-put` went from **18,080 tokens and zero bytes** to 8,560
  tokens and a working patch.
- **[#64](https://github.com/evanwtf/local-llm/issues/64) filed: the KV prefix stalls at 20,398 on the Claude Code path.** Twelve
  consecutive turns, `memory_token_reusable: 0`, prompt growing 25 k → 38 k.
  Cost measured on real work: **`mbox-scan` 193 s via Aider, 931 s via Claude
  Code — 4.8x, same model, same task.** Ruled out: the token counter (238 pinned
  occurrences, the `tasks.toml` comment blaming it is **wrong**) and
  `cache_control` (stripping it changed nothing). Open lead: `messages[1]`
  alternates between a block list and a bare string.
- **Aider is exonerated and is now a trusted instrument.** All 15 ds4 failures
  traced to the model, free: `mbox-scan` applied a patch three times with
  identical wrong content; the timeouts were thinking-block generation, **not**
  the "repetition loops" recorded earlier; and `storage-blob-put-3` emitted no
  code at all while claiming *"I've already updated storage.py"*.
- **`smoke.gate()` ships** (`ffe7aca`): every batch now makes the backend write
  `reverse_string`, `fib` and `merge_sorted` and **executes** them. All three are
  tasks the degraded arm failed. **7.3 s**, and it would have refused the bad run
  in under a minute instead of after four trials.
- **Provenance was wrong and is fixed** (`273c499`, `fe1ed96`). The harness
  stamped `ds4_head=399acbb` from the fork while serving from a worktree at
  `ec7642c`; it now asks the **running server** which tree it came from. Rows
  also gained `gguf_path/bytes/mtime`, `server_argv`, `harness_head` and
  `metal_ceiling_mb` — `model` is a server-side alias and identifies nothing.
- **Three classes of bad row quarantined**, 22 in total: 15 dead `glm53ds4shim`
  rows from 08-30, 4 degraded-shim rows, 2 `--trace` diagnostics, and **16
  `opus5` client errors** that made the hosted reference read **28/44 (64%)**.
  It is **28/29**. `agent_error=True` still does not set `excluded`; that is the
  underlying bug and it is [#55](https://github.com/evanwtf/local-llm/issues/55).
- **`--trace` is the tool for cache questions** — it records prompts, cache
  decisions and the diverging token IDs. Three hand-built minimal repros all
  cached *correctly*; only a real traced trial reproduced the stall.
- **SIGTERM does not run `atexit`.** The repo-restore guard does not cover the
  most likely way a long run is stopped; we hit it twice today.
- **RECOMMENDATIONS.md archived** to `docs/archive/RECOMMENDATIONS-2026-08-29.md`
  pending a from-scratch rewrite ([#2](https://github.com/evanwtf/local-llm/issues/2) in the queue).
- **SOURCES.md now carries GitHub and website links** for all 23 accounts, 19
  verified live. `@0xSero` moved tier 3 → tier 2: 271 repos including a
  13-chapter GLM-5.3 low-bit quantization wiki and a pinned recipe for **our
  exact DeepSeek 0731 checkpoint**. The row had said "no GitHub found" because
  nobody tried the handle as the login.

**2026-08-30/31 overnight. OpenCode went from 1/15 to 12/20, and the harness grew the guards it never had.**

- **The client was not the whole story.** A model asked for
  `src/gmail_archive/parser.py` guesses `~/git/gmail-archive` -- and that path
  held the **real, un-excised** checkout. It looked, saw green tests, correctly
  concluded there was nothing to do, and wrote nothing. Recorded as a model
  failure with the control's exact test counts.
- **Fix: stand the export where the model expects the repo.** Real checkout to
  `<name>-real`; `git archive` puts the excised tree at the guessed path, with
  no `.git` history the original body was ever in. **In-place was rejected** --
  `git show 56e55cc:...` hands over the answer.
- **12/20 (60%), Wilson 39-78%**, against **1/15 (7%)**. `mbox-scan` and
  `parser-mbox-quoting` are **3/3**; `mbox-strip-envelope` is **0/3** after
  passing earlier. Four of five tasks flipped verdict between runs -- **per-task
  rates are not stable at n=3.**
- **Why OpenCode alone:** 27 of 35 of its trials worked outside the checkout;
  **Codex 0 of 135, Claude Code 0 of 106** -- and Claude Code runs with
  `bypassPermissions`, so nothing was stopping it. `external_directory` defaults
  to `ask`; headless there is nobody to ask. **Its safety model assumes a human,
  and we removed the human.**
- **`sandbox-exec` confinement below the client**, since the client cannot
  confine itself (#41067 reproduced on 1.18.25). Verified against symlinks, hard
  links and local clone; inherited through `bash -> sh -> cat`.
- **`ensure_pristine()`** refuses rather than warns: pinned commit must be
  reachable from an `origin/*` ref, then reset, clean, assert clean.
- **Crash recovery was needed within an hour of being written**, when a `pkill`
  bypassed `atexit` and left the repo renamed.
- **Three false negatives caught, two self-inflicted:** `source_repo_intact`
  inverted, `paths_outside` handed a key that is never set, and denying
  `~/git/local-llm` killed every trial in 0.4s. **Confinement has to leave the
  agent able to run.**
- **The harness leaks its own answers**: `~/bench-solutions` holds 186 correct
  patches and tracked `results.jsonl` names their paths. Four trials reached
  them; excluded with cause. Measured: one enumerated 39 and read **zero**.

**2026-08-30 evening. The project changed shape.**

- **OpenCode is now the primary harness** (README, AGENTS.md, `65210c7`). An
  open model on an open engine driven by a **proprietary client is not a
  fallback** -- it fails with the vendor. Claude Code and Codex become
  reference points that establish a task's ceiling; a gap between them and
  OpenCode is a **defect to chase, not a result to publish**.
- **And it does not work.** [#54](https://github.com/evanwtf/local-llm/issues/54): `opencode run` is headless,
  `external_directory` defaults to `ask`, and with nobody to ask it read the
  operator's real un-excised repo -- seeing green tests and correctly
  concluding nothing needed fixing. That is why every OpenCode failure has
  `patch=0` and the control's exact test counts.
- **It also writes outside the workspace.** One trial deleted **33 lines** from
  a working `scan()` in a checkout it was never pointed at. The dedicated
  `~/git/local-llm-testing/` checkouts contained it; the real repos were
  verified clean. **That isolation is what prevented data loss.**
- **Configuration cannot fix it on 1.18.25.** The deny rule loads and orders
  last (`merge` is `flat()`, lookup is `findLast`) and is still bypassed --
  [#41067](https://github.com/anomalyco/opencode/issues/41067) submits
  out-of-worktree paths as `../...`.
- **Partial confinement is worse than none.** Blocked from pytest, the agent
  said so and **routed around it**: *"I ran the test bodies programmatically
  against the real module."*
- **The harness leaks its own answers.** `~/bench-solutions` holds 186 correct
  patches; tracked `results.jsonl` names their paths. Four trials reached them,
  one of which passed. Excluded with cause.
- **Escape detection shipped** (`paths_outside`, `f3adb06`, 8 tests) with
  auto-exclusion for answer trees. **27 of 35 OpenCode trials touched
  `~/git/local-llm`; Claude Code 0 of 106, Codex 0 of 135.**

**2026-08-30 afternoon. [#52](https://github.com/evanwtf/local-llm/issues/52) replicated and reported upstream; [#53](https://github.com/evanwtf/local-llm/issues/53) half-answered.**

- **[#52](https://github.com/evanwtf/local-llm/issues/52) closed.** ds4 PR#621 AProjQ4 on this M5 Max, measured **twice**:
  **53.35 t/s** at isolated ctx-2048 (clears 50 by 6.7%) and **q4/q8 = 1.155**
  across **32/32** frontiers (superseded 2026-09-04: paired figure from the
  same CSVs is 1.141 — see that date's entry). Reported to
  [ds4#621](https://github.com/antirez/ds4/pull/621#issuecomment-5470605362).
- **One sub-claim was withdrawn upstream rather than left standing.** Pass 1
  read prefill as "slightly ahead on Q8" from a 2.7% gap; three reps give
  824 vs 825, ratio **0.998**. The gain is **decode and only decode**.
- **`ds4-bench` precision is now measured**: **+/-0.4-0.6%** within a session,
  ~50x tighter than the agent suite's +/-27.9% ([#23](https://github.com/evanwtf/local-llm/issues/23)). But **between** sessions
  both arms drifted **3-4.5%** after unrelated heavy work -- so **quote the
  ratio, not the absolute**.
- **[#53](https://github.com/evanwtf/local-llm/issues/53): OpenCode = 1/15 on llama.cpp + Qwen `UD-Q3_K_XL`, and 14 of 15 wrote
  no file at all** -- `agent_error` and `stop_reason` both `None`, controls
  live, tests untouched, 80-250 s and thousands of tokens per trial. **This is
  not bad code, it is no code**, on weights that score **15/15 under Codex**.
  That lifts the standing "do not generalise OpenCode's ds4 result" caveat.
- **LM Studio installed (0.4.23) but not yet launched** -- its CLI registers
  only on first GUI launch, and this is a shared machine. Operator is doing it.
  Full resume checklist is on [#53](https://github.com/evanwtf/local-llm/issues/53).

**2026-08-30 12:01. [#52](https://github.com/evanwtf/local-llm/issues/52) closed: AProjQ4 on ds4#621 breaks 50 t/s here.**

- Isolated `--ctx-max 2048` (ctx alloc 2177): Q4 **51.03** `gen_steady_tps`, Q8 44.27 (**+15.3%**). Both coherent at `--temp 0`.
- Sweep 2048→65536, 3 reps, 64k allocation: Q4 > Q8 on **32/32** frontiers, paired median **+14.6%** (superseded 2026-09-04: +15.7% under the true paired statistic, same CSVs — see that date's entry). Under that alloc the ctx-2048 frontier is 45.95 / 40.37 — do not pool with the isolated run.
- Engine `2669a8e` in `~/git/ds4-pr621`. CSVs in `benchmarks/ds4/pr621-m5max/`. Not posted upstream.
- `decode_ab.sh` must run with cwd = the engine tree or Metal shaders are missing.

**2026-08-30 overnight. [#48](https://github.com/evanwtf/local-llm/issues/48) run and closed: refuted, by reading the engine.**

- **The F16 tensors our primary spends 11.5% of per-token traffic on are
  *required* F16 by `ds4.c`** -- `attn_compressor_*`, `indexer_compressor_*`,
  `hc_attn_fn`, `hc_ffn_fn`, `indexer.proj`. Only `indexer.attn_q_b` accepts
  q8_0, worth **1.7%**. The Metal fused kernels branch on the type too, so a
  build that accepted q8_0 would fall off the fast path.
- **The GLM finding did not transfer.** antirez's BF16 choice for GLM-5.3 was a
  *choice*; F16 here is a *constraint*. Same-sounding tensors, different code
  path, and the only way to tell was to read `ds4.c`.
- **Two of my own numbers were wrong and are corrected**: `token_embd` is a
  lookup (~8 KB/token, not 0.99 GiB), so the F16 share is **11.5%, not 20.2%**,
  and the saving was **4.8%, not 9.5%**.
- **A control caught a confound before it cost the experiment.**
  `--compare-tensor` fails against the published GGUF on an expert tensor *and*
  on `attn_q_a`, which has no imatrix dependency -- our pipeline does not
  reproduce the shipped bytes. That forced generating **both** arms; comparing
  against the shipped file would have varied all 1328 tensors, including 82 GiB
  of experts, and I would have blamed the 271.
- **The pipeline is validated**: arm A reproduces the published tensor-type
  structure exactly, loads, and writes correct Python at `--temp 0`, **45.39
  t/s**.
- **[#49](https://github.com/evanwtf/local-llm/issues/49) filed:** we still do not know what binds decode, and two levers are now
  closed. Four cheap probes listed; one is free.
- **~330 GiB left on disk** (148.7 GiB safetensors + two 90 GiB arms). Nothing
  deleted -- weights are kept unless removal is a deliberate decision.

**2026-08-29 22:50. Full sweep of 26 open issues and every tracked upstream.**

- **ds4#892 changes the plan: [#39](https://github.com/evanwtf/local-llm/issues/39) is unblocked and now first.** GLM-5.3 Flash
  brought up on an **M5 Max 128 GB** -- this machine -- decode **33.0 -> 40.5
  t/s** with `--mtp`, 89.6% acceptance. Our note that "no flag reaches a working
  model" is obsolete.
- **ds4#893 kills half of [#40](https://github.com/evanwtf/local-llm/issues/40).** A fixed 110 GiB GLM-5.3 budget stands for
  128 GiB hosts; our 112.00 GiB wired limit is already above it, so **resident q4
  is unreachable here** and no sysctl changes that.
- **Two runbooks contradicted their own tables.** README and RECOMMENDATIONS both
  still told the reader to start Codex, though the primary pick became
  `ds4` + Claude Code in [#44](https://github.com/evanwtf/local-llm/issues/44). Both fixed, with the `ANTHROPIC_API_KEY`
  precedence trap written down.
- **[#21](https://github.com/evanwtf/local-llm/issues/21) closed** (session state, long since landed in the machine-state section
  below) and **[#13](https://github.com/evanwtf/local-llm/issues/13) closed** (Ollama 0.33.1 re-baseline, overtaken -- preflight
  now stamps versions into `env` on every trial, so the series boundary is
  recorded rather than remembered).
- **[#35](https://github.com/evanwtf/local-llm/issues/35) given its admission criteria**, including a fourth the data forced:
  a candidate is a model x engine x **client** triple, because the same weights
  under two clients separated 2.14x on Swift.
- **[#14](https://github.com/evanwtf/local-llm/issues/14) cross-referenced to ds4#816.** Same failure shape on both engines: a
  stateless client meeting a server that keys its cache on an exact prefix. Not
  a llama.cpp quirk.

**2026-08-29 22:00. [#45](https://github.com/evanwtf/local-llm/issues/45) run: 8 trials, and the finding is not the one it asked for.**

- **The hypothesis is unconfirmed. 8/8 passed, no compile failures.** The
  unbuildable result from [#44](https://github.com/evanwtf/local-llm/issues/44) did not recur in four harder attempts on the pair
  that produced it.
- **The verbosity gap widens with difficulty.** Between `ornith15 x codex` and
  `ds4 x claude`: **5.42x -> 8.26x on tokens**, 1.77x -> 2.93x on time. Per pair,
  easier set -> harder set: `ds4 x claude` **1.34x** tokens, `ornith15 x codex`
  **2.05x**. The terse pair degrades gracefully; the verbose one inflates
  further. [#44](https://github.com/evanwtf/local-llm/issues/44) left open whether inflation was a fixed pair trait -- **it is
  not**, and easy-task measurements under-estimate the spread on hard work.
- **Throughput did not move: 15.3 -> 15.2 s/1k** for `ornith15 x codex`, with
  time 2.03x and tokens 2.05x. Harder tasks did not slow decoding measurably.
  Third time here a wall-time difference resolved to a token count.
- **Screening run, 2 trials per cell**, under [#23](https://github.com/evanwtf/local-llm/issues/23)'s bar. Rescoped mid-run: the
  harder tasks cost 571-999s per trial against a planned ~94s, so 16 trials
  needed 3.5 h. Stopped Phase A balanced at 2-per-task rather than finish one
  pair and never measure the other.
- **[#46](https://github.com/evanwtf/local-llm/issues/46) filed:** Swift rows report `gates_delta = {"ruff": 0}` from linters that
  never ran.
- **Correction:** the monitor suite is **215 tests**, not the 202 stated in the
  [#42](https://github.com/evanwtf/local-llm/issues/42) close comment and an earlier note. Fixed here and on [#42](https://github.com/evanwtf/local-llm/issues/42).

**2026-08-29 evening. [#44](https://github.com/evanwtf/local-llm/issues/44), [#43](https://github.com/evanwtf/local-llm/issues/43), [#42](https://github.com/evanwtf/local-llm/issues/42) closed; [#45](https://github.com/evanwtf/local-llm/issues/45) opened and running.**

- **[#44](https://github.com/evanwtf/local-llm/issues/44) closed: the Swift repo did not raise difficulty, and that is the finding.**
  45 trials, five pairs, **44/45** -- as saturated on 11,265 Swift lines as on
  1,833 Python ones. [#4](https://github.com/evanwtf/local-llm/issues/4)'s hypothesis is **not supported on correctness.**
- **It changed the primary recommendation anyway.** On Python, `ds4` under Claude
  Code and under Codex were indistinguishable (982s vs 975s) and the honest
  advice was "pick on habit". On Swift they separate **2.14x**. RECOMMENDATIONS
  now says **`ds4` + Claude Code**, not "either".

  | pair | pass | suite | out_tok | s/1k |
  |---|---|---|---|---|
  | **`ds4` x claude** | **9/9** | **522s** | **3,835** | 47.6 |
  | `ornith15` x codex | 8/9 | 844s | 20,788 | **14.7** |
  | `qwen38fnq3` x codex | 9/9 | 1,086s | 5,932 | 61.5 |
  | `ds4anthropic` x codex | 9/9 | 1,115s | 9,082 | 39.6 |
  | `qwen36coding` x claude | 9/9 | 1,393s | 5,232 | 84.3 |

- **The unexpected number: token inflation on unfamiliar ground varies 2.3x
  across pairs.** Python -> Swift, same tasks: `ds4 x claude` **1.19x**,
  `ornith15 x codex` **2.73x**. Since wall time tracks output tokens at r=0.98,
  *how gracefully a pair degrades off its comfort zone* may predict real use
  better than a saturated pass rate. **Caveat recorded:** the Swift tasks are not
  difficulty-matched to the Python ones, so the ordering is sound and the
  absolute ratios are not.
- **The single failure is the interesting row, and it is now [#45](https://github.com/evanwtf/local-llm/issues/45).**
  `ornith15 x codex` produced Swift that **did not compile**, from a run that
  looked entirely normal -- 18,694 output tokens, 30 tool calls, clean
  `turn.completed`, no `agent_error`. **Python cannot produce this failure in
  this harness:** a syntax error is a pytest collection error, not a separate
  build step.
- **[#45](https://github.com/evanwtf/local-llm/issues/45) opened and running.** Two harder Swift tasks added, each leaning on a
  construct with no Python equivalent -- `ScaleLadder.snap` (if-as-expression
  assigned to a `let`) and `SevenSegment.glyphs` (in-place mutation of an array
  of value types). Controls verified: both stub to `fatalError` and fail the
  suite before the agent runs. Running the two **extremes** -- 2.73x against
  1.19x -- not the whole field.
- **[#43](https://github.com/evanwtf/local-llm/issues/43) closed:** README, AGENTS.md and RECOMMENDATIONS all updated. Doing it
  *after* [#44](https://github.com/evanwtf/local-llm/issues/44) was right -- the docs would otherwise have been accurate and wrong.
- **[#42](https://github.com/evanwtf/local-llm/issues/42) closed:** `~/git/monitor` is pinned at `local-llm-benchmark` @ `cbb85ca`,
  215 hermetic tests, five tasks.
- **Trap found the hard way:** `swift_excise.excise(path, symbol)` **writes the
  file** and returns the removed text. Calling it to inspect a span modifies the
  real working tree. Use `body_source()` to look; only `run.py`'s worktrees
  should ever see `excise()`.

**Overnight 2026-08-28/29. Seven evaluations, 190 trials.**

- **[#28](https://github.com/evanwtf/local-llm/issues/28) closed: there is no engine difference.** On byte-identical weights
  (Ollama's own ornith-1.5 GGUF served by both) llama.cpp and Ollama decode at
  the same rate -- 14.1 vs 15.0 s/1k tokens. A measured **+66%** collapsed to
  **+5-10%** once four sampler parameters were matched. `repeat_penalty` was the
  missing one: Ollama 1.1, llama.cpp 1.0, `llamacpp-up` never set it.
- **[#36](https://github.com/evanwtf/local-llm/issues/36) closed: `top_p` moves pass rate, and it is coupled to `repeat_penalty`.**
  36 trials: `top_p 0.95` no-rp **17/18**; `top_p 0.90` no-rp **7/12**;
  `top_p 0.90` + `rp 1.1` **6/6**. Temperature and top_k are innocent.
- **[#34](https://github.com/evanwtf/local-llm/issues/34) closed: expert streaming is -60% memory for +76% wall time**, lossless
  across 31 trials. It does **not** make a fitting model faster; it makes a
  non-fitting model possible.
- **[#33](https://github.com/evanwtf/local-llm/issues/33) closed: the PLE offload does not pay** -- 4-bit `-M64` is +28% slower
  than 3-bit and saves **nothing**, because mmap already makes every weight page
  evictable (footprint ~5 GB against ~92 GiB RSS).
- **[#35](https://github.com/evanwtf/local-llm/issues/35) answered: GLM-5.2 runs.** 196.6 GiB streams into **30.8 GiB** and passes
  a real agent task -- in 2,585 s, **14x** ds4. Possible, not practical.
- **[#23](https://github.com/evanwtf/local-llm/issues/23) closed:** three trials pins a suite to **+/-12.9%**; nothing under a ~26%
  gap is a finding. 35 consecutive passes for a >90% claim.
- **[#4](https://github.com/evanwtf/local-llm/issues/4) answered, and the answer is the repository.** 18/18 on the harder tasks.
  gmail-archive has one function with the surface that produced the one defect.
- **Infrastructure moved to latest** (Codex 0.150.1, OpenCode 1.18.25, llama.cpp
  mainline `d7bd3bfca` after PR #27742 merged). Codex 0.150.1 broke the
  llama.cpp path within minutes; `fold_developer()` in the shim fixes it.

- **[#34](https://github.com/evanwtf/local-llm/issues/34) closed. The cost curve exists.** MoE expert streaming: **91.0 -> 36.7 GiB
  (-60%) for +76% suite wall time**, 16/16, no correctness cost across 31 trials.
  Memory is *bounded* (36.7 GiB after one request, 37.1 after ten trials), and
  startup drops 16-30s to **2s**. The PLE offload ([#33](https://github.com/evanwtf/local-llm/issues/33)) by contrast saved
  **nothing** and cost 28%. **Streaming does not make a fitting model faster; it
  makes a non-fitting model possible** -- which reopens the "too big" tier.
  Independently lands within 1% of the 37 GB @EyalToledano reported for the same
  technique on a different model.
- **Trap:** `ds4-up` hardcoded `--warm-weights`, which touches every page and
  contradicts `--ssd-streaming`. Together they report **90.9 GiB -- full
  residency, streaming apparently doing nothing**, with no warning. `WARM` is now
  overridable; both launchers take `EXTRA_FLAGS`.

- **[#28](https://github.com/evanwtf/local-llm/issues/28) answered, and the headline is an artifact -- do not quote "+66%".** First
  fixed-model engine comparison here, using the identical GGUF out of Ollama's
  blob store. Suite: Ollama 523.1s vs llama.cpp 870.8s. **The entire gap is one
  task** -- minus `parser-date` it is +9%, inside the noise. **Throughput is
  identical**: 14.1 vs 15.0 s per 1k output tokens. llama.cpp was slower because
  it emitted **29,906 tokens against 7,449** on that task, because `llamacpp-up`
  hardcoded `--temp 1.0` while Ollama's modelfile sets nothing. Matching the
  sampler halved both tokens and clock (422s -> 212s) and closed **half** the
  gap; the residual 1.9x is unexplained. **`storage-blob-put` went 3/3 at t=1.0
  and 0/3 at t=0.8** -- sampler settings move pass rate, not just wall time.
- **[#33](https://github.com/evanwtf/local-llm/issues/33) closed: the PLE offload does not pay.** 4-bit `-M64` is **+28% slower**
  than 3-bit on an identical stack, 16/16 vs 15/15. The memory saving was never
  available: `-M64` changes no tensors (1224 both, 3 shards vs 33), and `vmmap`
  shows mmap already makes every weight page evictable -- physical footprint
  **~5 GB against ~92 GiB RSS**, with or without pinning the table to CPU.
- **Infrastructure moved to latest**, and it broke something within minutes:
  Codex 0.150.1 sends `instructions` **and** a `role=developer` item, which
  llama-server turns into two system messages and the Qwen template rejects.
  `fold_developer()` in the shim fixes it; all llama.cpp codex profiles now go
  through the shim. **PR #27742 merged upstream** -- `~/git/llama.cpp` is on
  mainline `d7bd3bfca`, old build tagged `benchmark-pr27742-2026-08-26`.

- **[#4](https://github.com/evanwtf/local-llm/issues/4) measured: 18/18 pass.** Three new tasks x ds4 x {Claude Code, Codex} x 3.
  **The ceiling is not an artifact of easy tasks.** Per-task median rose
  194.6 -> 270.6 s (**+39%**) with **no** additional failures. Suites 813.4 s vs
  701.1 s, a 16% gap that is inside [#23](https://github.com/evanwtf/local-llm/issues/23)'s +/-12.9% band -- **no difference
  measured**. `restored_verbatim` **0/18**, 18 distinct solutions: nothing is
  recalled, and with `unquote_mbox`'s docstring removed the model re-derived the
  mboxrd reasoning from scratch. One real defect, in 5 of 6 trials on the
  multi-file task and reproducible across both clients: a callback annotated
  `re.Match` instead of `re.Match[bytes]`, which adds 2 `mypy --strict` errors
  while all 71 tests pass. First "passes but is worse" result recorded here.
- **A latent harness defect, found by running unattended.** `agent_env()` never
  set `CODEX_API_KEY`, so every Codex row ever recorded depended on the operator
  having exported it in the launching shell. Unattended, Codex dies at config in
  0.7 s and the row looks exactly like the model giving up. Fixed and tested.
  The 4 rows it produced are marked excluded; **the historical record is
  unaffected** -- all 140 Codex trials audited, all 3 failures genuine, none
  under 10 s.
- **[#23](https://github.com/evanwtf/local-llm/issues/23)** closed. **Three trials is a screening run, not a measurement.** Pass
  rate: an unbroken run's Wilson bound is `n/(n+z^2)`, so >90% needs **35**
  consecutive passes, >95% needs 73. One failure costs ~20 trials. Wall time,
  bootstrapped over 198 observations: n=3 pins a task median to **+/-27.9%** and
  a 5-task suite to **+/-12.9%**, so suites separate only above a ~26% gap. Every
  published speed claim was re-checked against that -- all survive, but Q3-vs-Q2
  ([#31](https://github.com/evanwtf/local-llm/issues/31)) clears by a hair and rests on winning all five tasks separately.
  `sizing.py` is re-runnable. The rule is in AGENTS.md.
- **[#34](https://github.com/evanwtf/local-llm/issues/34) step 1** done: the NVMe is measured for the first time
  (`benchmarks/disk/RESULTS.md`). Sequential **9.45 GiB/s**; random 1 MiB
  **198 us / 6.32 GiB/s**; random 4 KiB **61 us / 0.10 GiB/s**. **Block size is
  what costs, not randomness** -- 1 MiB random reads reach 67% of sequential,
  4 KiB reads reach 1.1%. Streaming MoE expert blocks is arithmetically viable
  (~2 ms per fully-cold token, a 500 tok/s ceiling); the n-gram PLE table is the
  hard case and its cost depends on lookups per token, which is **unmeasured**.
- **[#4](https://github.com/evanwtf/local-llm/issues/4)** build half done and merged. `run.py` had deleted every worktree in a
  `finally`, so **398 trials of produced code were thrown away**; solutions are
  now saved and hashed, ruff and mypy run as deltas against the excised tree, and
  `restored_verbatim` checks the authorship contamination METHODOLOGY has warned
  about since day one. Three new tasks, each moving one variable.
- **The empty-virtualenv confound is withdrawn -- it was never real.** The
  control has run `uv run pytest` before the agent since the first commit, and
  all 482 rows carry a control result. The new tasks **do not start a new
  series**.
- **[#26](https://github.com/evanwtf/local-llm/issues/26)** answered and its hypothesis refuted: not the KV cache, not warm-up
  (first trial of a batch is 0.98x the rest over 92 batches). Wall time tracks
  output tokens at r=0.98. The server samples at **temperature 1.0 with a fresh
  seed per request**, which [#23](https://github.com/evanwtf/local-llm/issues/23) has now turned into a trial-count rule.
- **[#24](https://github.com/evanwtf/local-llm/issues/24)** published verdicts corrected, after two live reader bugs -- a timeout
  writes no `passed` key, and `summarize.py` still hand-rolled its exclusion
  filter over fourteen `confound` rows. **Do not test `row["passed"]` directly.**
- **[#30](https://github.com/evanwtf/local-llm/issues/30)/#31/#32/#22/#25/#16**: Metal ceiling raised to 112.00 GiB; Qwen3.8-Flash-Next
  is best at `UD-Q3_K_XL` (15/15); GLM-5.3-Flash works (15/15) and is the fifth
  lineage. Details in RESULTS.md and RECOMMENDATIONS.md -- all have landed.
