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

**One axis only: local model performance for coding agents, on this Mac.** 82
issues are open. Ten of them carry only the `Nvidia` label and three more are
shared; the Linux/RTX tier is a fallback plan, not this queue, and nothing from
it appears below. Neither does anything that is a lead rather than a task —
roughly thirty open issues are unmeasured claims from the sweep, and they wait
in the tracker until one of them is worth a run.

## The top 10

Ranked by **what can actually be finished**, not by what is most interesting.
Each item says what *done* looks like. Moving ten things five percent leaves
nothing finished and no way to tell.

### First, because everything else is measured through it

1. **[#149](https://github.com/evanwtf/local-llm/issues/149)** Record which Metal route produced every row
   The hard part is settled: the fast Metal 4 tensor route is the standing
   default, `scripts/ds4-fast.sh` and `scripts/ds4-vanilla.sh` share port 8000
   so the OS keeps them exclusive, and `confirm_route()` kills a server whose
   route cannot be asserted from its log. **What remains is the part that makes
   old numbers comparable to new ones**: rows still carry no route field, so a
   fast-route number and a reference-kernel number sit in `results.jsonl`
   indistinguishable. The #138 arms were confirmed same-route **by hand**,
   which is exactly the check that will not be repeated next time.
   *Done when:* the route is recorded on every row, `ds4_test --metal-tensor-equivalence` runs in preflight as a gate rather than a log line, and the route is pinned in `bitexact_ab.py` before its first use.

2. **[#145](https://github.com/evanwtf/local-llm/issues/145)** A finished run leaves its last model server holding 98 GB
   Deterministic, not a race: `stack_agent_ab.sh` restarts the server between
   sweeps and never stops the last one, so **every** clean finish leaks it —
   four in a row now, most recently 97.9 GiB after the #138 run. It blocks the
   next run, and preflight calls the machine healthy because a leftover is
   indistinguishable from a server in use. **First in machine-time order:**
   it is a `trap`, and every item below it needs the memory.
   *Done when:* a trap stops the server on exit, interrupt and error paths, and preflight can say how long a server has been resident and what claims it.

3. **[#151](https://github.com/evanwtf/local-llm/issues/151) / [#148](https://github.com/evanwtf/local-llm/issues/148)** Assert the MTP draft head is actually used
   Two independent reports in one day, neither looking for the other: an oMLX
   recipe whose apparent 2x was mostly repairing an MTP config enabled with no
   usable draft head, and ivanfioravanti saying *"In ds4 I've not cooked
   support for MTP in ds4 chat"* while measuring MTPLX 25 t/s against ds4
   18 t/s on an M5 Max. We run `qwen38fnds4mtp7shim` with an MTP gguf on disk
   and have **never asserted the draft head is used** — only that the flag was
   passed. That stack is also the 50/91 row #142 is about, so a no-op MTP arm
   would explain two open problems at once. Assert the treatment, do not assume
   it (#116's fan check, #149's route, same shape).
   *Done when:* draft acceptance is recorded per row for every MTP backend, and a run where an MTP arm reports zero accepted draft tokens is refused.

### The live performance question

4. **[#155](https://github.com/evanwtf/local-llm/issues/155)** Which half of the #138 gain is the engine and which is the quant?
   #138 is the largest agent-level effect this project has measured — **44% off
   wall time, 60/60 against 52/60** — and it cannot be attributed, because the
   engine build and the quantization arrived together and were tested together.
   The answer decides where weeks of work go: chase MXFP4/imatrix builds of
   other models, or track one fork's commits. **Start with the cheap half** —
   whether a cross combination loads at all, given PLE lives only on the fork
   (#141). If neither loads, that is an answer and this closes.
   *Done when:* one cross arm is measured under #138's design, or both are shown unloadable and the question is recorded as unanswerable on this hardware.

5. **[#142](https://github.com/evanwtf/local-llm/issues/142)** The published ranking rewards failing fast
   `RECOMMENDATIONS.md` still sorts stacks by median wall time, which puts
   `qwen38fnds4mtp7shim` (**50/91**, 84s) second, above `qwen38fnds4kimat`
   (**90/90**, 97s) — because a failed trial is a short one. This is the file a
   stranger reads to decide what to install, and #138 just made it worse by
   producing a stack that passes everything and sorts below one that passes
   55%. Cheapest item here with a user-visible consequence.
   *Done when:* the table sorts on something that does not reward failure, or we decide on the record that the prose warning is enough.

6. **[#112](https://github.com/evanwtf/local-llm/issues/112)** The tool-call degeneration loop
   Turn-1 deaths with no tool call, none of them wrong code. Unblocked and
   nothing to build — `SHIM_NO_STRIP=1` is the off arm. **Now more than a
   hygiene item:** #138's old arm produced eight deaths, five of them turn-1
   `solution_empty` at 6-7 seconds, and #138 ruled out heat as the cause. This
   is the mechanism most likely to be behind them.
   *Done when:* 2 runs per arm strip-on vs strip-off under one protocol, read with `tool_error_conditional.py`, at >=30 failures per arm. The current data has 19.

7. **[#143](https://github.com/evanwtf/local-llm/issues/143)** Settle the ds4#964 prefill disagreement
   Decode is re-measured and holds at **+18.7%** over four runs. Prefill does
   not: we measure **-1.0%** against a claimed 16.6-26.0%, and the reason may
   be that `ds4-bench` prefills the step increment, so `prefill_tokens` is 2048
   at every frontier and cold prefill may never have been measured here.
   Prefill is our bottleneck, so this is not a bookkeeping question.
   *Done when:* cold prefill is measured deliberately, with the Metal route recorded on the rows (#149 item 1), and the upstream reply is sent or the claim is accepted.

8. **[#146](https://github.com/evanwtf/local-llm/issues/146)** Cut over to the sandbox target layout
   Built and merged behind `--targets sandbox`, **not enabled**. The guarded
   checkout is out of `~/git`, but the export still stands where the agent
   guesses (#54); under sandbox that guess must fail closed instead of being
   satisfied — a behavior change that lands on the pass rate. Do it before more
   runs pool, not after: it is a cohort boundary.
   *Done when:* a paired run against the legacy layout says the pass rate is within 1 task across 2 sweeps of 15, or the cutover is abandoned on the record.

### Standing problems, kept visible because everything is measured against them

9. **[#64](https://github.com/evanwtf/local-llm/issues/64)** KV cache prefix stalls at ~20,400 tokens
   Every turn re-prefills the whole conversation on Claude Code trials. The
   cost is measured; the fix is in a client we do not own, so this ends in an
   upstream report, not a patch.
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
- **[#120](https://github.com/evanwtf/local-llm/issues/120)** what ds4 server state degrades a session — needs several controlled arms, and #149 is now a candidate mechanism. Blocked behind item 1 more than behind machine time.
- **[#131](https://github.com/evanwtf/local-llm/issues/131)** the client-version boundary — the boundary exists whether or not we measure it, and `results.jsonl` now holds two versions for two backends. A real question, but it moves no stack ranking.
- **[#96](https://github.com/evanwtf/local-llm/issues/96)** oMLX bit-exact tail continuation — blocked on finding the change at all; the cited PR is a different feature. Ask upstream or diff releases.
- **[#154](https://github.com/evanwtf/local-llm/issues/154)** ruff and mypy have never run in CI — real, and it came from a real analysis bug, but it is repo hygiene rather than model performance. Land the narrow ruff rule set when something else is already blocked.
- **[#141](https://github.com/evanwtf/local-llm/issues/141)** PLE only exists on ivanfioravanti forks — a standing note on durability, not a task. #155 depends on it being true.

## Not queued

- **[#40](https://github.com/evanwtf/local-llm/issues/40)** Mixed-precision GLM-5.3 — right question, behind a working agent path. Has a recipe from ds4#964 and local numbers from [#118](https://github.com/evanwtf/local-llm/issues/118).
- **The sweep backlog** — roughly thirty open issues are somebody else's unverified claim: new quants, new engines, new MTP numbers. They are leads, and `SOURCES.md` says how they are gathered. One earns a run when it beats item 4 on expected information, not because it is new.
- **GLM thinking/tool-replay** (ds4#894, #897, #899, #904, #906) — defects we would inherit while ds4#569 and #816 stand.
- **Anything on the Linux/RTX tier** — 13 open issues carry the `Nvidia` label. Different machine, different queue.
- **Vision, vector steering, ROCm** — out of scope, and not shipped.
- **More trials on saturated cells** — new axes, not more samples.

## Recently done, listed so the next reader does not re-open them

- **[#138](https://github.com/evanwtf/local-llm/issues/138) — the measurement is done and the verdict held.** The issue is still open, but the queue treats the run as finished. Paired superiority run, 4 sweeps per arm, 120 trials, pre-registered before any row existed. New stack **60/60 passes against 52/60**, wall ratio **0.56 (95% CI 0.45-0.71)**, all four sweep-pair medians agreeing in direction, every void condition passing on one harness head and one client version. The effect is far outside the ~12-18% the design resolves. Two findings came out of it that are now items above: the old arm's eight deaths are **not thermal** (item 6), and the result cannot be attributed to engine or quant (item 4).
- **[#147](https://github.com/evanwtf/local-llm/issues/147)** the hardcoded `client_version 1.18.27` — closed; the check asserts uniformity instead of a literal.
- **[#153](https://github.com/evanwtf/local-llm/issues/153)** the reporter's verdict line said the opposite of its data — an `abs()` read an 8-task *lead* as a gap and printed `SCREEN FAIL` on the run that passed. Found and fixed during #138. **This is the sentence someone would have copied into an issue**, which is why it is listed here rather than in the changelog alone.
- **[#152](https://github.com/evanwtf/local-llm/issues/152)** the 19 candidate sources — closed; six accounts added to `SOURCES.md`, six rejected with reasons, and every account added carries a GitHub identity checked against the profile rather than guessed from a matching name.
