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

1. **[#64](https://github.com/evanwtf/local-llm/issues/64)** — KV prefix stalls at ~20,400 tokens, so every turn re-prefills the whole conversation. ~186 s before the first output token, growing with the conversation. [#50](https://github.com/evanwtf/local-llm/issues/50) names a mechanism and it is cheap to test.
2. **[#112](https://github.com/evanwtf/local-llm/issues/112)** — the tool-call degeneration loop. Nine failures with no wrong code. Remedy 1 needs no new code and no machine time; remedy 2 shipped and is still unmeasured.
3. **[#136](https://github.com/evanwtf/local-llm/issues/136)** — a single A/B run is not a measurement. Four identical runs spanned 4.7 pp. Report between-run spread, establish how many runs are enough, audit the A/Bs computed before `98bc79b`.
4. **[#131](https://github.com/evanwtf/local-llm/issues/131)** — nothing pins the agent client. OpenCode still self-updates on both machines; `preflight` warns where it should refuse. A version recorded and not pinned is a post-mortem.
5. **[#4](https://github.com/evanwtf/local-llm/issues/4)** — the task set cannot measure code quality. Two backends clear 90% and cannot be told apart. Until this moves, no result can show that a better agent is better.
6. **[#120](https://github.com/evanwtf/local-llm/issues/120)** — find which `ds4-server` state degrades a session. Six pass-rate points hide in it. Start with [#116](https://github.com/evanwtf/local-llm/issues/116), fan RPM in `thermals.py`, then a controlled max-fans cycle.
7. **[#96](https://github.com/evanwtf/local-llm/issues/96)** — oMLX bit-exact tail continuation, TTFT 3–4 s → 0.3 s. At 9 turns median that is ~30 s a task spent waiting. Rust build plus one restart-between cycle.

After these: the engine-speed queue and the rest of the backlog, in the tracker.

## Not queued

Open issues deliberately not in the list, and why:

- **[#40](https://github.com/evanwtf/local-llm/issues/40) mixed-precision GLM-5.3.** Right question, behind a working agent path — but it now has a concrete recipe and numbers from ds4#964 (KDA + head BF16 → Q8 inside the Q4_K file, "Q2 speed with a Q4 file"; quality measured on the PR: perplexity improves slightly). [#118](https://github.com/evanwtf/local-llm/issues/118) has now measured the full-precision PR itself on this machine over **four runs — median +17.6% paired decode, spread 16.5-21.2, bit-exact** — so the recipe builds on local data too.
- **GLM thinking/tool-replay (ds4#894, #897, #899, #904, #906).** Defects we would inherit while #569 and #816 stand.
- **Vision, vector steering, ROCm.** Out of scope, and not shipped.
- **More trials on saturated cells.** New axes, not more samples.
