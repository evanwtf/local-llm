# What to do next, in order

> ## ⚠️ OpenCode results before 2026-08-31 21:47 EDT are INVALID
>
> Any OpenCode trial recorded before `2026-08-31T21:47:18-04:00` measures a
> harness bug -- the client was never told which directory to work in, so it
> solved each task and wrote the answer somewhere else. **Do not quote, pool,
> or compare against those numbers.** Cause, cutover and replacements:
> [docs/archive/results-opencode-pre-dir.md](docs/archive/results-opencode-pre-dir.md). Other clients are unaffected.

Updated **2026-09-06**. The queue for **this machine** — MacBook Pro, M5 Max,
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

**One axis only: local model performance for coding agents, on this Mac.** 58
issues are open, down from 82: **twenty leads were closed on 2026-09-06**, and
four more items finished overnight, each
with the reason on the issue, because a tracker nobody can read is not a
backlog. Seven of the survivors carry only the `Nvidia` label and three more
are shared; the Linux/RTX tier is a fallback plan, not this queue, and nothing
from it appears below.

## Priority labels

Every open issue carries exactly one, applied 2026-09-06. **The labels are
this file, made queryable** — they are not a second opinion about what matters,
and they drift the moment this file is re-ranked without them.

- **`P0`** (3) — blocks or invalidates measurement. Do before anything that needs the machine. Items 1-3 below.
- **`P1`** (7) — the rest of the top 10. Items 4-10.
- **`P2`** (39) — a real task with a stated reason it is not now: the "below the line" items, harness defects nobody is blocked on, ops and housekeeping, and the Linux/RTX tier.
- **`P3`** (9) — a lead. Somebody else's unverified claim about a quant, an engine, or an MTP number. **A lead earns a run by beating a P1 on expected information, not by being new.** Twenty more were closed on 2026-09-06; what is left is the set with a mechanism attached to one of our own models or engines.

The invariant: **`P0` + `P1` is exactly the top 10**, so
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
   the PR declares the hyphenated `qwen4-exp`, which is the lineage of the
   weights we hold — and **not** the `qwen4exp` lineage behind #138's 44%, so
   upstreaming may retire the fork dependency for one of our two ds4 stacks and
   not the other.
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

7. **[#146](https://github.com/evanwtf/local-llm/issues/146)** Cut over to the sandbox target layout
   Built and merged behind `--targets sandbox`, **not enabled**. The guarded
   checkout is out of `~/git`, but the export still stands where the agent
   guesses (#54); under sandbox that guess must fail closed instead of being
   satisfied — a behavior change that lands on the pass rate. Do it before more
   runs pool, not after: it is a cohort boundary.
   *Done when:* a paired run against the legacy layout says the pass rate is within 1 task across 2 sweeps of 15, or the cutover is abandoned on the record.

8. **[#78](https://github.com/evanwtf/local-llm/issues/78)** A row does not record what produced it
   Promoted on new evidence from the overnight run. Two arms of a published
   A/B differed only in an **environment variable no row records**, so the
   arms are separable solely by a hand-kept manifest of run times; and all 120
   rows carry `metal_route: unrecorded` because one driver forgot one call.
   Both were caught by hand. The next one will not be.
   *Done when:* a backend's server identity and its arm-defining switches are on the row, and a row that cannot say what produced it is refused rather than published.

### Standing problems, kept visible because everything is measured against them

9. **[#64](https://github.com/evanwtf/local-llm/issues/64)** KV cache prefix misses cost ~22% of a batch
   Now measured across eight fresh server logs in one night: **1.14M tokens
   re-prefilled, ~53 minutes of a 4h04m batch**, 85-127 misses per run, and
   **every miss is `token-mismatch`** — one mechanism, not several. No stall
   occurred in eight OpenCode runs, so the original ~20,400-token Claude Code
   symptom is neither confirmed nor cleared by this sample. The fix is in a
   client we do not own, so this ends in an upstream report.
   *Done when:* the report is filed with our numbers, or the client is dropped for agent work on the record.

10. **[#4](https://github.com/evanwtf/local-llm/issues/4)** The current task set cannot measure code quality
    Months, not hours — and the ceiling on every claim this project makes. #138
    could say a stack is 44% faster and 8 passes better; it could not say the
    code was any good. Kept at the bottom of the ten so it is never the reason
    nothing else ships, and never quietly dropped either.
    *Done when:* a task class exists where a wrong-but-passing solution is detectable.

## Below the line, with the reason

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

- **[#138](https://github.com/evanwtf/local-llm/issues/138) — the measurement is done and the verdict held.** The issue is still open, but the queue treats the run as finished. Paired superiority run, 4 sweeps per arm, 120 trials, pre-registered before any row existed. New stack **60/60 passes against 52/60**, wall ratio **0.56 (95% CI 0.45-0.71)**, all four sweep-pair medians agreeing in direction, every void condition passing on one harness head and one client version. The effect is far outside the ~12-18% the design resolves. Two findings came out of it that are now items above: the old arm's eight deaths are **not thermal** (item 6), and the result cannot be attributed to engine or quant (item 4).
- **[#147](https://github.com/evanwtf/local-llm/issues/147)** the hardcoded `client_version 1.18.27` — closed; the check asserts uniformity instead of a literal.
- **[#153](https://github.com/evanwtf/local-llm/issues/153)** the reporter's verdict line said the opposite of its data — an `abs()` read an 8-task *lead* as a gap and printed `SCREEN FAIL` on the run that passed. Found and fixed during #138. **This is the sentence someone would have copied into an issue**, which is why it is listed here rather than in the changelog alone.
- **[#152](https://github.com/evanwtf/local-llm/issues/152)** the 19 candidate sources — closed; six accounts added to `SOURCES.md`, six rejected with reasons, and every account added carries a GitHub identity checked against the profile rather than guessed from a matching name.
