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

1. **[#138](https://github.com/evanwtf/local-llm/issues/138)** ivanfioravanti: Q4_K imatrix Qwen3.8-Flash-Next for ds4, measured on M5 Max
   Not a new model beside ours -- a **replacement of the build we run**. Ours is
   `Q40RoutedExperts`, which its author has withdrawn as less accurate.
   *Done when:* four-run decode A/B against our current Q4, or a stated reason it will not load.
2. **[#112](https://github.com/evanwtf/local-llm/issues/112)** The tool-call degeneration loop
   *Done when:* remedy 2 is measured on the affected cell -- it shipped 2026-09-03 and has never been.
3. **[#116](https://github.com/evanwtf/local-llm/issues/116)** Test whether maxing the fans changes measured pass rates or timings
   The design is pre-registered on the issue, thresholds and all five outcome
   sentences included. Only the run is left, and the fans are the operator's call.
   *Done when:* four runs auto against four runs max-fans, with the thermal log beside each.

### Measurements, not builds

4. **[#131](https://github.com/evanwtf/local-llm/issues/131)** item 4: does a client-version boundary move anything here?
   Items 1-3 are done. No (backend, task) cell on this machine holds two client
   versions, so this needs one deliberate comparison, not more incidental data.
   *Done when:* one backend is measured under two client versions, or the question is dropped on the record.

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
