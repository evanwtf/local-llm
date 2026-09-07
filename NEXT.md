# What to do next, in order

> ## ⚠️ OpenCode results before 2026-08-31 21:47 EDT are INVALID
>
> Any OpenCode trial recorded before `2026-08-31T21:47:18-04:00` measures a
> harness bug -- the client was never told which directory to work in, so it
> solved each task and wrote the answer somewhere else. **Do not quote, pool,
> or compare against those numbers.** Cause, cutover and replacements:
> [docs/archive/results-opencode-pre-dir.md](docs/archive/results-opencode-pre-dir.md). Other clients are unaffected.

Updated **2026-09-07**. The queue for **this machine** — MacBook Pro, M5 Max,
128 GB. Everything here is labeled `macOS`; nine issues filed on 2026-09-05
and 2026-09-06 carried no label at all until this sweep, so the label can be
trusted as a filter again. The one exception is [#154](https://github.com/evanwtf/local-llm/issues/154),
which is CI rather than a machine and is labeled `bug`.

Each issue is self-contained. This file sets the order and nothing else.
Machine operations live in [`docs/m5max-runbook.md`](docs/m5max-runbook.md),
what shipped in [`docs/changelog.md`](docs/changelog.md), traps in `AGENTS.md`.

**The goal is a coding agent you would actually use when the hosted ones are
gone** — not the fastest engine. Decode rate has failed three times to predict
agent wall time, so a speed claim ranks below a defect that makes a real
session slow, wrong, or unmeasurable.

**One axis only: local model performance for coding agents, on this Mac.** 70
issues are open, down from 82: **twenty leads were closed on 2026-09-06**, and
four more items finished overnight, each
with the reason on the issue, because a tracker nobody can read is not a
backlog. Seven of the survivors carry only the `Nvidia` label and three more
are shared; the Linux/RTX tier is a fallback plan, not this queue, and nothing
from it appears below.

## Priority labels

Every open issue carries exactly one, re-applied 2026-09-07 -- seven open
issues carried none until that sweep, so the label can be trusted as a filter
again. **The labels are
this file, made queryable** — they are not a second opinion about what matters,
and they drift the moment this file is re-ranked without them.

- **`P0`** (3) — blocks or invalidates measurement. Do before anything that needs the machine. Items 1-3 below.
- **`P1`** (7) — the rest of the top 10: item 0, which is running, and items 4-9.
- **`P2`** (49) — a real task with a stated reason it is not now: the "below the line" items, harness defects nobody is blocked on, ops and housekeeping, and the Linux/RTX tier.
- **`P3`** (11) — a lead. Somebody else's unverified claim about a quant, an engine, or an MTP number. **A lead earns a run by beating a P1 on expected information, not by being new.** Twenty more were closed on 2026-09-06; what is left is the set with a mechanism attached to one of our own models or engines.

The invariant: **`P0` + `P1` is exactly the top 10** -- items 0-9 below, counting item 0 -- so
`gh issue list --label P0 --label P1` and the list below can be checked against
each other. If they disagree, this file is the one that was edited.

```sh
gh issue list --state open --label P0          # what is in the way
gh issue list --state open --label P1          # what is next
gh issue list --state open --label P3          # the lead backlog
```

## The top 10

Ranked by **what can actually be finished**, not by what is most interesting.
Each item says what *done* looks like. Moving ten things five percent leaves
nothing finished and no way to tell.

**Four items closed overnight on 2026-09-06** — #145 (the server leak), #149
(the Metal route), #155 (unanswerable, recorded as such) and #142 (the ranking
that rewarded failing fast). Their slots are filled from below the line, and
two of the promotions are things that overnight run walked into.

### The machine queue

Updated **2026-09-07 03:40 EDT**, overnight. One thing runs at a time; the run
lock is what enforces it, and the suite now refuses to start beside a held lock
rather than voiding a measurement quietly.

1. ~~**#171 cold large-chunk prefill, `8c22d667` vs head.**~~ **Done: a null.**
   Three 4-rep runs, prefill head/base = +0.5%, −0.9%, +0.0%. @adamlawi's CUDA
   figure is −12.23% [−12.43, −12.03]; **the f309990 Q4 prefill regression has
   no Metal analogue.** The blocking question the issue posed — whether each
   frontier is a single chunk or re-chunked — is settled in the source and
   confirmed by the engine's own output (`prefill_cap=8192 raw_kv_rows=8192`,
   `prefill_tokens=8192` at all four frontiers). — opus
2. ~~**#190 ds4 cold-prefill / prefix reuse.**~~ **Done, with two corrections
   on the issue.** The disk budget is inert (8 GiB ≡ 32 GiB, byte-identical)
   and so is ctx (32k ≡ 128k). Reuse lands on multiples of the continued
   checkpoint step, 10240. Both retractions are worth reading before trusting
   any number here: the second was found by reading the server log the harness
   had been capturing all along. — opus
3. ~~**#162 Task 2 — `q4/q8` at head.**~~ **Done: decode unchanged, prefill
   slipped.** Three runs at `20d5dff6`, 48 pairs. Decode `q4/q8` 1.145 / 1.152
   / 1.147 against the 1.155 of record — inside the old spread. Prefill 0.988 /
   0.979 / 0.983 against a 0.999 parity of record, with q8 ahead at **12 of 12
   frontiers**. The cross-backend question is closed: @iammac2 showed by diff
   that ROCm is byte-identical across `f309990`, and #171 showed Metal is flat
   across it, so **the −12% is CUDA-only** despite the commit editing
   `ds4_metal.m`. Metal's Q4 prefill deficit (−1.7%) is the mildest of the three
   backends, against −7.46% on CUDA and −3.0…−7.7% on ROCm. **Ours is the
   appended interval at each frontier, theirs is a cold large-chunk prefill —
   not the same quantity.** — opus
4. ~~**#190 follow-up: isolated vs sequential reuse.**~~ **Done, and it is the
   night's result.** Two ds4 KV ceilings, each one flag wide, each confirmed
   against a prediction over three runs:
   - **A cold checkpoint stops existing past 30,000 tokens**
     (`cold_max_tokens`). At 53,845 and 77,845 tokens a fresh session gets
     **0.0% reuse — no store is written at all.** Raise the cap and the same
     prompts read **98.9%** and **97.3%**.
   - **A continued checkpoint lands only on an exact multiple of 10,240**, and
     prefill advances in 8,192-token chunks, so from any resume point the next
     landing is **five chunks — 40,960 tokens — away**. At 29,845 tokens the
     default gives **34.3%**; step 2,048 gives **89.2%**, storing at 26,624 =
     10,240 + 2 × 8,192, the number the arithmetic named before the run
     produced it.

   For a coding agent both bite at once: a new session over 30k gets nothing,
   and a continuing one must grow by ~41k tokens **in a single turn** to leave
   a new checkpoint. — opus

6. **#201 REPS=4 as the default** before the next sub-1% comparison. No machine
   time; it is a two-line change plus a refusal on an odd rep count.

**#146 is at the bottom of the queue, by the operator's decision.** Its clean
4-run repeat read out as **NO CALL** — per-sweep 14/15, 15/15, 11/15, 15/15,
paired difference −1/+4, and the sweeps disagree in direction, which is the
issue's own third branch. The arm totals (legacy 29/30, sandbox 26/30) would
have produced a "do not cut over" the pre-registration does not support. Do not
re-run it to break the tie; that is the tie.

**A quiet machine is not optional for any of these.** No builds, no large-file
reads, no CPU-heavy work from any session while a batch holds the lock. A build
landing in one arm and not the other is the confound that made #146's first
attempt uninterpretable, and a test suite landing in one arm and not the other
is what voided run 2 of #162 Task 3.

**Position bias is now measured, not assumed** (#130, #201): 9 of 12 reps
favour whichever arm ran first, median +0.9%, but **+5.9% on the first rep of a
cold session**, decaying over about an hour. Every comparison below ~1% is
inside that. `REPS` still defaults to 3 in both harnesses, and an odd rep count
does not cancel a decaying bias — pass `REPS=4` explicitly until #201 lands.

Durations are estimates. Nothing here is anchored to a clock — each step starts
when the one before it releases the lock.

### Top of the queue: Qwen on ds4 (#212)

**Operator decision, 2026-09-07: this outranks GLM.** [#212](https://github.com/evanwtf/local-llm/issues/212)
is the parent, and the Qwen work that was scattered across five issues at three
priorities now hangs off it: **#170, #151, #210, #39, #158, #188, #191, #211**.
#138 closed as finished rather than being dragged in, and #190 and #199 closed
answered the same morning.

Why it is first: Qwen3.8-Flash-Next on ds4 is the primary of this project —
`qwen38fnds4shim` is the largest backend in the corpus at 262 rows — and every
recent upstream event lands on it. `ds4#991` proposes our fork upstream,
`ds4#990` adds a native Metal port, and @ivanfioravanti published **MTPLX 25 t/s
against ds4 18 t/s on an M5 Max**, our exact machine class and our exact model.
Meanwhile the machine has produced **no Qwen row since 2026-09-05**.

Done so far today:

- **#170 — done. Six of six model-free suites pass on an M5 Max** at
  `9803df46`: `gdn`, `qsa`, `hc`, `ple-hash`, `ple-store`, `indexer`. Six, not
  the four the issue named — the PR gained two while it sat. Reported with one
  real build finding: `tests/test_qwen38_qsa.c:148` uses `-INFINITY` as a
  softmax sentinel, and the `-fno-finite-math-only` that would make it defined
  sits in the Makefile's **non-Darwin** branch. Latent, not active.
  `scripts/qwen38_metal_suites.py` re-runs it; the PR moves daily.
- **#210 — the row now records the split.** `drafting_share`, `bypassed` and
  `drafting` land beside `accept_rate`, and an arm that drafted in **zero**
  cycles is refused like one that accepted nothing. Read it next to item 3
  below: the split is not random, it tracks **tool-bearing requests**.
- **#151 — the recorded blocker was wrong.**
  `Youssofal/Qwen3.8-Flash-Next-MTPLX-Optimized-Speed` exists, declares
  `mtp_depth_max: 3` and a real 1.68 GB `mtp.safetensors`, and is fetching.
  MTPLX updated **2.7.2 → 2.11.2**.

Next, in order: a lock-held MTP batch that writes `drafting_share` on every row
(which is what #148 and #39 are both waiting for), then MTPLX against ds4 on
this machine at **full power** — @ivanfioravanti's numbers were taken in Low
Power, which is not our regime.

### First, because someone upstream is waiting on it

0. **[#162](https://github.com/evanwtf/local-llm/issues/162)** Re-test ds4#952 on Metal, at head `77a054e1`
   @GiorgioOppo has asked the three third-party testers to run again, and **we
   are the only Metal report in that thread**. 77 commits have landed since the
   `6a20b13` we published 1.155 / 0.999 against, 31 of them touching Metal.
   The part worth doing is not the re-run: @adamlawi has localised a **−12% Q4
   prefill regression on CUDA** to `f309990`, a commit whose subject says it
   also touches *Metal SSD decode kernels*, and nobody has a mechanism for it.
   Measuring `f309990^` against `f309990` on Metal says whether the fault is in
   shared code or confined to the CUDA path. **Done** is that answer, stated
   with the `prefill_tps` definition beside it, drafted on #162 for the
   operator to post. Peer has the builds and the flag semantics.

### First, because everything else is measured through it

1. **[#158](https://github.com/evanwtf/local-llm/issues/158)** Qwen3.8-Flash-Next is upstreamed as `antirez/ds4#991`
   The fork our entire ds4 stack stands on is proposed upstream, **open and
   mergeable**, and three of our issues move with it. Our own build fell out of
   the history while we watched: `ds4-metal @ ba01f5d` is **not an ancestor**
   of the PR head, so for the second time our ds4 numbers come from a build
   that no longer exists upstream. The first checks cost minutes, not hours:
   done 2026-09-06, and it inverts the premise this item was written on. The
   GGUF loader accepts exactly one Qwen architecture string and it is the
   **unhyphenated** `qwen4exp` (`ds4.c:6789 at ds4-pr991 236cb2a`, exact
   `memcmp`); the hyphenated `qwen4-exp` is a JSON pack-manifest field checked
   elsewhere (`ds4_qwen4.c:1244 at ds4-pr991 236cb2a`), not
   `general.architecture`. Our Q4_K
   imatrix file declares `qwen4exp` and **matches**; the Q4_0 file declares
   `qwen4-exp` and **does not**. So the fork dependency may be retirable for
   `qwen38fnds4kimat` — our strongest ds4 row at 90/90, 97s — and permanent
   for `qwen38fnds4shim`, whose build was already withdrawn upstream. The
   opposite of what was assumed, and the better of the two outcomes.
   *Done when:* the PR build loads (or refuses) each of our weight files with the architecture strings compared first, prefill is measured here separating appended-token rate at depth from cold prefill, and `RECOMMENDATIONS.md`'s fork caveat is updated to match reality.

2. **[#148](https://github.com/evanwtf/local-llm/issues/148)** Prove the MTP draft head drafts, per row
   The precondition now reads the **server's** argv rather than this process's
   environment, and an MTP arm that drafts nothing is refused. What is still
   missing is the evidence: **no lock-held measurement run has yet written the
   per-row `draft` field.** Until one has, the gate is code that has never
   fired in anger.
   *Done when:* a real batch writes `draft` on every row of an MTP backend, and one deliberately broken arm is shown to be refused.

3. **[#151](https://github.com/evanwtf/local-llm/issues/151)** ds4 chat may have no MTP at all
   Measured 2026-09-06: on agent-shaped traffic ds4's MTP arm **is engaged and
   is rejected** — ~0.01 accepted per cycle with tools in the prompt against
   ~3.3 without, 6/6 tool-bearing requests at `cycles=297 accepted=3`.
   Unexplained and still open: a full harness trial through OpenCode produced
   **zero** counter lines, not merely low acceptance, and it could not be
   reproduced outside the client.
   *Done when:* the zero-counter case is explained or shown to be an artefact of how the log is read.

### The live performance question

4. **[#112](https://github.com/evanwtf/local-llm/issues/112)** The tool-call degeneration loop
   **The shim's scaffolding strip is worth 23 points of pass rate** — 53/60
   against 39/60, deaths 7 against 21, Fisher p = 0.004, every strip-on run
   beating every strip-off run over 8 runs on 2026-09-06. That was a
   *secondary* reading: the pre-registered conditional came in at 13 failures
   against a bar of 30 and reads "could not tell", because a trial death
   produces no tool call and so cannot appear in a table that counts calls.
   *Done when:* the 23 points are confirmed by a run designed for the outcome (now cheap to power), and items 3 and 4 — ds4's imitable error text, a repetition penalty — are tried or dropped on the record.

5. **[#39](https://github.com/evanwtf/local-llm/issues/39)** The `--mtp-exact-sampling` arm
   Promoted because #142 closed and handed it the question it could not
   answer. MTP-on loses 30 points of pass rate on our own published cell, and
   **nothing can attribute that loss** while ds4's MTP defaults do not preserve
   the sampling distribution: the model is simply being sampled differently.
   One arm separates "sampled differently" from "speculation loses work".
   *Done when:* a third arm runs with exact sampling on, under #112's protocol, and the pass-rate loss is attributed or ruled out.

6. **[#143](https://github.com/evanwtf/local-llm/issues/143)** Settle the ds4#964 prefill disagreement
   Decode is re-measured and holds at **+18.7%** over four runs. Prefill does
   not: we measure **-1.0%** against a claimed 16.6-26.0%, and the reason may
   be that `ds4-bench` prefills the step increment, so `prefill_tokens` is 2048
   at every frontier and cold prefill may never have been measured here.
   Prefill is our bottleneck, so this is not a bookkeeping question. The route
   field #149 added is now available to pin the rows.
   *Done when:* cold prefill is measured deliberately, with the Metal route recorded on the rows, and the upstream reply is sent or the claim is accepted.

7. **[#201](https://github.com/evanwtf/local-llm/issues/201)** `REPS=3` does not cancel a decaying position bias
   Promoted on measurement, not argument. Across the twelve reps of #171,
   whichever arm ran **first** was faster in 9 of them, median +0.9% — and
   **+5.9% on the first rep of a cold session**, decaying over about an hour.
   Alternation only cancels on an even rep count, and both harnesses still
   default to 3, so reps 1 and 3 run A-first and only rep 2 runs B-first. Every
   comparison below ~1% is inside that, which is most of what the ds4 threads
   are now arguing about.
   *Done when:* `REPS` defaults to 4, an odd rep count is refused or loudly warned, and `decode_ab_engine.sh` writes `run-order.txt` the way `decode_ab.sh` already does.

8. **[#189](https://github.com/evanwtf/local-llm/issues/189)** The run lock is checked at session start only
   The guard works — it refused three suite runs tonight while a batch held the
   lock, which is exactly what it is for. The hole is a suite **already
   running** when a batch starts: `pytest_sessionstart` is the only check, so
   an in-flight suite runs to completion beside the measurement. That is how
   run 2 of #162 Task 3 was voided, landing on arm A and not arm B.
   *Done when:* a batch can tell whether a suite is in flight, and either waits for it or refuses to start — rather than the suite alone being polite.

### Standing problems, kept visible because everything is measured against them

9. **[#64](https://github.com/evanwtf/local-llm/issues/64)** KV cache prefix misses cost ~22% of a batch
   Now measured across eight fresh server logs in one night: **1.14M tokens
   re-prefilled, ~53 minutes of a 4h04m batch**, 85-127 misses per run, and
   **every miss is `token-mismatch`** — one mechanism, not several. No stall
   occurred in eight OpenCode runs, so the original ~20,400-token Claude Code
   symptom is neither confirmed nor cleared by this sample. The fix is in a
   client we do not own, so this ends in an upstream report.
   *Done when:* the report is filed with our numbers, or the client is dropped for agent work on the record.

## Below the line, with the reason

**[#4](https://github.com/evanwtf/local-llm/issues/4) The current task set cannot
measure code quality** — moved out of the ten on 2026-09-07, displaced by #162,
which two people upstream are waiting on. It is still the ceiling on every claim
this project makes: #138 could say a stack is 44% faster and 8 passes better; it
could not say the code was any good. It moves down because it is months of work
and nothing else is blocked on it, **not** because it stopped mattering. If it
is still here in a month, that is the finding.
*Done when:* a task class exists where a wrong-but-passing solution is detectable.


Not "later" in the vague sense — each of these has a specific reason it is not
in the ten.

- **[#116](https://github.com/evanwtf/local-llm/issues/116)** fans as a controllable variable — **demoted on new evidence.** #138 logged five hours and 120 trials with a 73-79 GB model resident: fans held 3450-3455 rpm across all eight windows, GPU median drifted **-1.2 °C**, and there was no thermal ramp. The baseline is flat, so there is little headroom for `fancontrol max` to recover. Still worth confirming with the paired design one day; no longer worth a session now.
- **[#120](https://github.com/evanwtf/local-llm/issues/120)** what ds4 server state degrades a session — promoted this morning when #149 closed and unblocked it, demoted the same day when #158 arrived. It needs several controlled arms and the machine now has a bigger question in front of it.
- **[#131](https://github.com/evanwtf/local-llm/issues/131)** the client-version boundary — the boundary exists whether or not we measure it, and `results.jsonl` now holds two versions for two backends. A real question, but it moves no stack ranking.
- **[#96](https://github.com/evanwtf/local-llm/issues/96)** oMLX bit-exact tail continuation — blocked on finding the change at all; the cited PR is a different feature. Ask upstream or diff releases.
- **[#154](https://github.com/evanwtf/local-llm/issues/154)** ruff and mypy have never run in CI — real, and it came from a real analysis bug, but it is repo hygiene rather than model performance. Land the narrow ruff rule set when something else is already blocked.

## Not queued

- **[#40](https://github.com/evanwtf/local-llm/issues/40)** Mixed-precision GLM-5.3 — right question, behind a working agent path. Has a recipe from ds4#964 and local numbers from [#118](https://github.com/evanwtf/local-llm/issues/118).
- **The sweep backlog** — nine open issues are somebody else's unverified claim, down from twenty-nine. They are leads, and `SOURCES.md` says how they are gathered. One earns a run when it beats item 4 on expected information, not because it is new.
- **Twenty leads closed on 2026-09-06**, each with its reason on the issue. Four umbrellas absorbed most of them: #60 (the engine survey) took #18, #57, #72, #105, #115; #19 (does native MTP retire the mtplx stack?) took #86, #117, #121, #122, #139; #20 took the 12 GB tier's #113 and #124; #126 took #150. The rest closed on their own analysis: #51 and #95 had already concluded "do not spend a measurement slot", #88 was corroboration without a number, #114 was **finished** and its write-up is in `hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/RESULTS.md`. **Closed is not rejected** — reopening one costs nothing, and the sweep will resurface anything that starts mattering.
- **GLM thinking/tool-replay** (ds4#894, #897, #899, #904, #906) — defects we would inherit while ds4#569 and #816 stand.
- **Anything on the Linux/RTX tier** — 10 open issues carry the `Nvidia` label. Different machine, different queue.
- **Vision, vector steering, ROCm** — out of scope, and not shipped.
- **More trials on saturated cells** — new axes, not more samples.

## Recently done, listed so the next reader does not re-open them

**Overnight 2026-09-06/07** — full reasoning in [`docs/changelog.md`](docs/changelog.md).

- **[#171](https://github.com/evanwtf/local-llm/issues/171) closed: a null.** The f309990 Q4 prefill regression has **no Metal analogue** — three 4-rep runs at +0.5%, −0.9%, +0.0% against @adamlawi's CUDA −12.23%. Do not re-run it looking for the effect; the three runs straddling zero *are* the result.
- **[#182](https://github.com/evanwtf/local-llm/issues/182) closed**: 208 bare citations to an argued 9, with a lint that has already caught four regressions.
- **[#192](https://github.com/evanwtf/local-llm/issues/192) closed**: every row names its engine build. Two defects caught in review, both a default answering for a caller it did not know — a **hosted** model would have been stamped with a local ds4 sha, and four backends whose descriptions say `ds4-metal` would have been stamped with a different tree.
- **[#130](https://github.com/evanwtf/local-llm/issues/130) measured, not just argued**: whichever arm runs first is faster in 9 of 12 reps, median +0.9%, **+5.9% on the first rep of a cold session**. Filed as [#201](https://github.com/evanwtf/local-llm/issues/201).
- **[#190](https://github.com/evanwtf/local-llm/issues/190): two of my own claims withdrawn.** The budget and ctx nulls stand; the mechanism I published did not. Read the corrections before quoting any number from that issue.
- **[#191](https://github.com/evanwtf/local-llm/issues/191) retitled**: "identical weights" is unachievable — mlx-lm only *writes* GGUF, and mlx-serve reads `.gguf` through an embedded llama.cpp, so the comparison as framed would have measured ds4 against llama.cpp under an MLX label.
- **[#154](https://github.com/evanwtf/local-llm/issues/154) measured**: CI runs no lint at all — **42** ruff findings and **21** unformatted Python files have been landing green. Three are `SIM115` (leaked handles) and three `PLW1510` (a `subprocess.run` whose failure is ignored), which in this repo means a benchmark step can fail and still produce a number. I filed #197 for the same thing without searching first; it is closed as a duplicate and its measurements are a comment on #154.

- **[#138](https://github.com/evanwtf/local-llm/issues/138) — the measurement is done and the verdict held.** The issue is still open, but the queue treats the run as finished. Paired superiority run, 4 sweeps per arm, 120 trials, pre-registered before any row existed. New stack **60/60 passes against 52/60**, wall ratio **0.56 (95% CI 0.45-0.71)**, all four sweep-pair medians agreeing in direction, every void condition passing on one harness head and one client version. The effect is far outside the ~12-18% the design resolves. Two findings came out of it that are now items above: the old arm's eight deaths are **not thermal** (item 6), and the result cannot be attributed to engine or quant (item 4).
- **[#147](https://github.com/evanwtf/local-llm/issues/147)** the hardcoded `client_version 1.18.27` — closed; the check asserts uniformity instead of a literal.
- **[#153](https://github.com/evanwtf/local-llm/issues/153)** the reporter's verdict line said the opposite of its data — an `abs()` read an 8-task *lead* as a gap and printed `SCREEN FAIL` on the run that passed. Found and fixed during #138. **This is the sentence someone would have copied into an issue**, which is why it is listed here rather than in the changelog alone.
- **[#152](https://github.com/evanwtf/local-llm/issues/152)** the 19 candidate sources — closed; six accounts added to `SOURCES.md`, six rejected with reasons, and every account added carries a GitHub identity checked against the profile rather than guessed from a matching name.
