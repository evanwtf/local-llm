# What to do next, in order

> ## ⚠️ OpenCode results before 2026-08-31 21:47 EDT are INVALID
>
> Any OpenCode trial recorded before `2026-08-31T21:47:18-04:00` measures a
> harness bug -- the client was never told which directory to work in, so it
> solved each task and wrote the answer somewhere else. **Do not quote, pool,
> or compare against those numbers.** Cause, cutover and replacements:
> [docs/archive/results-opencode-pre-dir.md](docs/archive/results-opencode-pre-dir.md). Other clients are unaffected.

Updated **2026-09-05**. The queue for **this machine** — MacBook Pro, M5 Max,
128 GB. Everything here is labelled `macOS`; the Linux/RTX 3080 Ti tier has
its own issues under `Nvidia`.

Each issue is self-contained. This file sets the order and nothing else.
Machine operations live in [`docs/m5max-runbook.md`](docs/m5max-runbook.md),
what shipped in [`docs/changelog.md`](docs/changelog.md), traps in `AGENTS.md`.

**The goal is a coding agent you would actually use when the hosted ones are
gone** — not the fastest engine. Decode rate has failed three times to predict
agent wall time, so a speed claim ranks below a defect that makes a real
session slow, wrong, or unmeasurable.

## Order

Ranked by **what can actually be finished**, not by what is most interesting.
Each item says what *done* looks like. Moving ten things five percent leaves
nothing finished and no way to tell.

**Closed on 2026-09-05 overnight:** #143 (ds4#964 re-tested, four runs per arm)
and #138's agent cell (screen passed). Both are below, reduced to what remains.
The overnight session also voided and re-ran the #138 screen, moved the guarded
checkout out of `~/git`, and found four checks that were guaranteed to fire or
guaranteed to mislead. See `docs/changelog.md`.

### First, because everything else is measured through it

1. **[#149](https://github.com/evanwtf/local-llm/issues/149)** ds4's Metal 4 tensor route flips greedy tokens on long prompts -- **reproduced here**
   ivanfioravanti measured it on an M5 Max with GLM-5.3-Flash-Q2; we reproduced
   his numbers to three significant figures (`rms 1.38592` vs `1.39`,
   `max_abs 7.26952` vs `7.27`). Short prompts are bit-exact; long prompts flip
   the **first sampled token**, and one failing case is a code audit -- the
   shape of the whole agent suite. All four of our ds4 arms run the route,
   there is no opt-out env var in those builds, and preflight has been logging
   `Metal tensor API is on` for weeks without anyone reading it as a warning.
   This is the "defect that makes a real session wrong or unmeasurable" the
   goal statement ranks above every speed claim.
   *Done when:* the route is recorded on every row, `ds4_test --metal-tensor-equivalence` runs in preflight as a gate rather than a log line, and the standing regime is chosen on the record -- reference kernels and re-measure, or fast route and stop calling any ds4 Metal quality number exact.

2. **[#148](https://github.com/evanwtf/local-llm/issues/148) / [#151](https://github.com/evanwtf/local-llm/issues/151)** Assert the MTP draft head is actually used
   **Two independent reports in one day**, neither looking for the other: an
   oMLX recipe whose apparent 2x was mostly repairing an MTP config that was
   enabled with no usable draft head, and ivanfioravanti saying *"In ds4 I've
   not cooked support for MTP in ds4 chat"* while measuring MTPLX 25 t/s
   against ds4 18 t/s on an M5 Max. We run `qwen38fnds4mtp7shim` with an MTP
   gguf on disk and have **never asserted the draft head is used** -- only that
   the flag was passed. Same shape as #116's fan integrity check and #149's
   route: assert the treatment, do not assume it.
   *Done when:* draft acceptance is recorded per row for every MTP backend, and a run where an MTP arm reports zero accepted draft tokens is refused.

3. **[#147](https://github.com/evanwtf/local-llm/issues/147)** `stack_agent_report` hardcodes `client_version 1.18.27`
   **Now live, not theoretical:** OpenCode shipped 1.18.28 and 1.18.29. The
   check packs two assertions into one line; only `len(versions) > 1` is the
   one we want. The literal voids any future run on a newer client, and quietly
   reintroduces the client pin at the analysis end -- where it is less visible
   than a preflight refusal -- against the standing decision to record and not
   pin. One line, and it prevents a void run.
   *Done when:* the check asserts uniformity, or the pin is made explicit as a named constant and stated in the pre-registration.

### Finishable in a session with machine time

4. **[#138](https://github.com/evanwtf/local-llm/issues/138)** the paired 3+3 the screen authorised
   The screen passed: new arm 30/30, old 27/30, wall ratio 0.56
   (95% CI 0.42-0.75), 12/1/1. That is far outside what n=30/arm resolves
   (~18-27 pp pass, ~17-26% wall), which is exactly why it stays a screen --
   promoting it because the number came back large is what pre-registration
   exists to prevent. Engine and quant move together, so this can only ever say
   the *stack* is better, not which half.
   *Done when:* 3 sweeps per arm under one harness commit, alternating order, pre-registered for superiority before it runs -- and read after #149 is settled, since both arms ran the drifting route.

5. **[#112](https://github.com/evanwtf/local-llm/issues/112)** The tool-call degeneration loop
   Item 2 cannot be measured at the outcome level: ~230 trials/arm to detect a
   drop to zero. Redirected to a strip-toggle A/B on the conditional, and
   **unblocked** -- `SHIM_NO_STRIP=1` is the off arm. Nothing to build; ~4-8 h
   of machine time.
   *Done when:* 2 runs per arm strip-on vs strip-off under one protocol, read with `tool_error_conditional.py`, at >=30 failures per arm.

6. **[#116](https://github.com/evanwtf/local-llm/issues/116)** Does maxing the fans change measured pass rates or timings? -- **unblocked**
   The earlier "blocked, no passwordless sudo" report was **wrong**: it tested
   `sudo -n fancontrol status`, a command the sudoers grant deliberately does
   not cover. `sudo -n fancontrol max --json` works, returns `mode: forced` at
   5349/5777. Two constants measured since, both needed by the wrapper: fans
   reach ~99% of target **between 5 and 10 seconds** after `max` and settle by
   16 s, while `auto` drops to 0 within 5 s; and `actual_rpm` reads **0 at idle
   even in forced mode**, so the integrity check must sample under real Metal
   load and treat `mode` as the primary signal. `fancontrol max` also exits **0
   on failure** without `--json`.
   *Done when:* four runs auto against four max-fans with a per-segment thermal log, the commanded mode verified from `status` after every switch, and a discarded settle window at each boundary.

7. **[#146](https://github.com/evanwtf/local-llm/issues/146)** Cut over to the sandbox target layout
   Built and merged behind `--targets sandbox`, **not enabled**. The guarded
   checkout no longer lives in `~/git`, but the export still stands at the path
   the agent guesses (#54). Under sandbox nothing does, so the guess must fail
   closed at the sandbox profile instead of being satisfied -- a behaviour
   change that lands on the pass rate.
   *Done when:* a paired run against the legacy layout says the pass rate is within 1 task across 2 sweeps of 15, or the cutover is abandoned on the record. It is a cohort boundary and cannot be assumed.

### Measurements, not builds

8. **[#131](https://github.com/evanwtf/local-llm/issues/131)** item 4: does a client-version boundary move anything here?
   Items 1-3 are done. No (backend, task) cell on this machine holds two client
   versions. **Now cheaper than when filed:** OpenCode is two releases ahead of
   what we record, so the boundary exists whether or not we measure it.
   *Done when:* one backend is measured under two client versions, or the question is dropped on the record.

9. **[#142](https://github.com/evanwtf/local-llm/issues/142)** The stack table rewards failing fast
   The warning shipped; the ranking did not change. A table sorted by median
   wall time promotes a 55.6% backend above every stack that passes
   everything, because a failed trial is a short one.
   *Done when:* the table sorts on something that does not reward failure, or we decide on the record that prose is enough.

10. **[#145](https://github.com/evanwtf/local-llm/issues/145)** A finished run leaves its last model server holding 79 GB
   Observed twice on 2026-09-05. `stack_agent_ab.sh` restarts the server per
   sweep and stops none of them; the script then cannot even exit, because the
   child outlives it. It blocked the next run and preflight reported the
   machine healthy, because a leftover from a finished run is indistinguishable
   from a server a current run needs.
   *Done when:* a trap stops the server on exit, interrupt and error paths, and preflight can say how long a server has been resident and what claims it.

11. **[#141](https://github.com/evanwtf/local-llm/issues/141)** PLE support exists only on ivanfioravanti forks
   `RECOMMENDATIONS.md` now names the fork and says to prefer llama.cpp when
   either stack would do. Open as a standing note on durability, not a task.
   *Done when:* upstream carries PLE, or we stop depending on it.

### Standing problems, not finishable in one sitting

12. **[#64](https://github.com/evanwtf/local-llm/issues/64)** KV cache prefix stalls at ~20,400 tokens
   The cost is measured. The fix is in a client we do not own, so this ends in an upstream report, not a patch.
13. **[#4](https://github.com/evanwtf/local-llm/issues/4)** Harder tasks: the current set cannot measure code quality
   Months, not hours. Every other result is measured against it, which is why it stays visible.
14. **[#120](https://github.com/evanwtf/local-llm/issues/120)** What ds4 server state degrades a session?
   Trial 3 drops on both ds4-shim backends. Isolating the variable needs several controlled arms; #116 is the first, and #149 is now a candidate mechanism.
15. **[#96](https://github.com/evanwtf/local-llm/issues/96)** oMLX bit-exact tail continuation, TTFT 3-4 s -> 0.3 s
   Blocked on finding the change at all -- the cited PR is a different feature. Ask, or diff releases.

### Done overnight, listed so the next reader does not re-open them

- **[#143](https://github.com/evanwtf/local-llm/issues/143)** ds4#964 re-tested on head `4b00b59`: decode **+18.7%** over four runs (unchanged from #118's +17.6%), prefill **-1.0%** -- the claimed 16.6-26.0% prefill gain does not appear here. Two caveats stated rather than left silent: the PR's bit-exactness claim was untested (`scripts/bitexact_ab.py` now exists but has not been run), and `ds4-bench` prefills the **step increment**, so `prefill_tokens` is 2048 at every one of our frontiers and we may not have measured cold prefill at all. **The only thing outstanding is the reply upstream, which is the operator's to send.**

After these: the engine-speed queue and the rest of the backlog, in the tracker.

## Not queued

- **[#40](https://github.com/evanwtf/local-llm/issues/40)** Mixed-precision GLM-5.3 — right question, behind a working agent path. Has a recipe from ds4#964 and local numbers from [#118](https://github.com/evanwtf/local-llm/issues/118).
- **GLM thinking/tool-replay** (ds4#894, #897, #899, #904, #906) — defects we would inherit while ds4#569 and #816 stand.
- **Vision, vector steering, ROCm** — out of scope, and not shipped.
- **More trials on saturated cells** — new axes, not more samples.
