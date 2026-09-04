# What to do next, in order

> ## ⚠️ OpenCode results before 2026-08-31 21:47 EDT are INVALID
>
> Any OpenCode trial recorded before `2026-08-31T21:47:18-04:00` measures a
> harness bug -- the client was never told which directory to work in, so it
> solved each task and wrote the answer somewhere else. **Do not quote, pool,
> or compare against those numbers.** Cause, cutover and replacements:
> [docs/archive/results-opencode-pre-dir.md](docs/archive/results-opencode-pre-dir.md). Other clients are unaffected.

Updated **2026-09-04**. The queue for **this machine** — MacBook Pro, M5 Max,
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

Three items closed on 2026-09-04 evening — #140, #137, and #131's build half.
The client-pinning decision was reversed the same day: this laptop is a daily
driver, so clients are **recorded, not pinned**, and `preflight.py` now warns
when one is *behind* its release rather than refusing when it has moved. See
`docs/changelog.md`.

### Finishable in a session with machine time

1. **[#138](https://github.com/evanwtf/local-llm/issues/138)** Q4_K imatrix vs the Q4_0 build we publish -- **speed and the gate done, the ranking not**
   Stack A/B: +9.5% decode, -24.5% prefill, four runs. Both builds pass a
   six-question `ds4-eval` gate 6/6, which says neither is broken and ranks
   nothing. Tokens-to-answer is the variable that decides whether the decode
   gain reaches a session, and five cases established no direction.
   *Done when:* one agent cell on the new stack, giving pass rate and wall time against the published cell -- or a stated reason we accept the trade blind.
2. **[#112](https://github.com/evanwtf/local-llm/issues/112)** The tool-call degeneration loop
   Item 2 cannot be measured at the outcome level: ~230 trials/arm to detect a
   drop to zero. Redirected to a strip-toggle A/B on the conditional.
   *Done when:* 2 runs per arm strip-on vs strip-off under one protocol, read with `tool_error_conditional.py`, at >=30 failures per arm.
3. **[#116](https://github.com/evanwtf/local-llm/issues/116)** Does maxing the fans change measured pass rates or timings?
   Design pre-registered on the issue, thresholds and all five outcome
   sentences. #138's session confirmed auto sits at ~3450/3730 rpm against a
   5349/5777 max, so the arms would be two real cooling regimes.
   *Done when:* four runs auto against four max-fans, with the thermal log beside each. The fans are the operator's call.

### Measurements, not builds

4. **[#131](https://github.com/evanwtf/local-llm/issues/131)** item 4: does a client-version boundary move anything here?
   Items 1-3 are done. No (backend, task) cell on this machine holds two client
   versions, so this needs one deliberate comparison, not more incidental data.
   *Done when:* one backend is measured under two client versions, or the question is dropped on the record.
5. **[#141](https://github.com/evanwtf/local-llm/issues/141)** PLE support exists only on ivanfioravanti forks
   The model we recommend runs on no upstream `ds4` build, so a clean-machine
   reproduction of our headline result is currently impossible.
   *Done when:* the recommendation says which fork it requires, or upstream carries PLE.

### Standing problems, not finishable in one sitting

5. **[#64](https://github.com/evanwtf/local-llm/issues/64)** KV cache prefix stalls at ~20,400 tokens
   The cost is measured. The fix is in a client we do not own, so this ends in an upstream report, not a patch.
6. **[#4](https://github.com/evanwtf/local-llm/issues/4)** Harder tasks: the current set cannot measure code quality
   Months, not hours. Every other result is measured against it, which is why it stays visible.
7. **[#120](https://github.com/evanwtf/local-llm/issues/120)** What ds4 server state degrades a session?
   Trial 3 drops on both ds4-shim backends. Isolating the variable needs several controlled arms; #116 is the first.
8. **[#96](https://github.com/evanwtf/local-llm/issues/96)** oMLX bit-exact tail continuation, TTFT 3-4 s -> 0.3 s
   Blocked on finding the change at all -- the cited PR is a different feature. Ask, or diff releases.

After these: the engine-speed queue and the rest of the backlog, in the tracker.

## Not queued

- **[#40](https://github.com/evanwtf/local-llm/issues/40)** Mixed-precision GLM-5.3 — right question, behind a working agent path. Has a recipe from ds4#964 and local numbers from [#118](https://github.com/evanwtf/local-llm/issues/118).
- **GLM thinking/tool-replay** (ds4#894, #897, #899, #904, #906) — defects we would inherit while ds4#569 and #816 stand.
- **Vision, vector steering, ROCm** — out of scope, and not shipped.
- **More trials on saturated cells** — new axes, not more samples.
