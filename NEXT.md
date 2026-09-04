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

1. **[#64](https://github.com/evanwtf/local-llm/issues/64)** KV cache prefix stalls at ~20,400 tokens — every turn re-prefills the whole conversation
   Inflates wall time on every Claude Code trial we hold, and kills trials that would otherwise pass.
2. **[#112](https://github.com/evanwtf/local-llm/issues/112)** The tool-call degeneration loop
   Nine failures with no wrong code. First remedy needs no new code and no machine time.
3. **[#131](https://github.com/evanwtf/local-llm/issues/131)** Nothing pins the agent client, and it updates itself between batches
   A silent client update rewrites results and reads as a model regression.
4. **[#4](https://github.com/evanwtf/local-llm/issues/4)** Harder tasks: the current set cannot measure code quality
   The pass-rate table has saturated, so no result can show a better agent is better.
5. **[#120](https://github.com/evanwtf/local-llm/issues/120)** What ds4 server state degrades a session?
   Six pass-rate points hide in an operational variable. Start with #116, fan RPM.
6. **[#96](https://github.com/evanwtf/local-llm/issues/96)** oMLX bit-exact tail continuation, TTFT 3–4 s → 0.3 s
   Median conversation is 9 turns, so ~30 s a task spent waiting rather than working.

After these: the engine-speed queue and the rest of the backlog, in the tracker.

## Not queued

- **[#40](https://github.com/evanwtf/local-llm/issues/40)** Mixed-precision GLM-5.3 — right question, behind a working agent path. Has a recipe from ds4#964 and local numbers from [#118](https://github.com/evanwtf/local-llm/issues/118).
- **GLM thinking/tool-replay** (ds4#894, #897, #899, #904, #906) — defects we would inherit while ds4#569 and #816 stand.
- **Vision, vector steering, ROCm** — out of scope, and not shipped.
- **More trials on saturated cells** — new axes, not more samples.
