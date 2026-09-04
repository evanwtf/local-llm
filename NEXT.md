# Where to pick up

> ## ⚠️ OpenCode results before 2026-08-31 21:47 EDT are INVALID
>
> Any OpenCode trial recorded before `2026-08-31T21:47:18-04:00` measures a
> harness bug -- the client was never told which directory to work in, so it
> solved each task and wrote the answer somewhere else. **Do not quote, pool,
> or compare against those numbers.** Cause, cutover and replacements:
> [docs/archive/results-opencode-pre-dir.md](docs/archive/results-opencode-pre-dir.md). Other clients are unaffected.

> ### In flight right now
>
> **Nothing.** The machine is idle, the run lock is free, and no download or
> benchmark is running. The last work finished 2026-09-04 ~14:40 EDT.
>
> Two upstream comments were posted today and both are settled:
> ds4#964 (the M5 Max reproduction of PR #964, four runs, median +17.6%) and
> ds4#952 (correcting our own statistic label; the numbers reproduced at
> 1.155 over four runs). The 171 GB of q4/q8 weights re-acquired for the
> second were deleted afterwards ([#135](https://github.com/evanwtf/local-llm/issues/135), closed).
>
> *Pick up from the table below.*

Updated **2026-09-04 14:55 EDT**. **This file is the queue for _this machine_ —
the MacBook Pro, M5 Max, 128 GB.** Every item below is labelled `macOS` in the
tracker. The Linux/RTX 3080 Ti tier has its own nine open issues ([#20](https://github.com/evanwtf/local-llm/issues/20), [#79](https://github.com/evanwtf/local-llm/issues/79),
[#98](https://github.com/evanwtf/local-llm/issues/98)–[#104](https://github.com/evanwtf/local-llm/issues/104)) and they are deliberately **not** here; see `hardware/` and the
`Nvidia` label.

This file holds the ranked queue and one machine-state snapshot. Everything
else has a permanent home: results in `hardware/<machine>/RESULTS-agent.md`,
what shipped in [`docs/changelog.md`](docs/changelog.md), traps in
`AGENTS.md`, machine operations in [`docs/m5max-runbook.md`](docs/m5max-runbook.md).

**The goal is a coding agent you would actually use when the hosted ones are
gone.** Not the fastest engine. That distinction decides the order, because
this project has measured three times that **decode rate does not predict agent
wall time**, and the pass-rate tables have saturated to the point where they
cannot separate backends ([#4](https://github.com/evanwtf/local-llm/issues/4)). So a +35% decode claim ranks *below* a defect that
makes a real session slow, wrong, or unmeasurable.

**The measurement rule that keeps mattering.** A 3-trial median carries
**±27.9%**, so two medians must differ by roughly **56%** before the gap is
real — measured against the *smaller* median. `scripts/report.py` applies it.
Most claims arriving from outside do not clear this bar, and saying so before
the run saves the run: [#95](https://github.com/evanwtf/local-llm/issues/95)'s +4–7% prefill gain is on our exact chip and we
still cannot see it at three trials.

Each issue is self-contained; this file only sets priority and records machine
state that is not in git. The table is the queue. It has no calendar.

**Where the rest lives.**

| document | holds |
|---|---|
| [`docs/changelog.md`](docs/changelog.md) | **what shipped, and why** — the running record, newest first. Look here for how a result was reached or why a guard exists. |
| [`docs/m5max-runbook.md`](docs/m5max-runbook.md) | **how to run this machine** — the Metal ceiling, the exact server argv, the engine trees, the weights, the client configs. |
| [`TESTING-SET.md`](TESTING-SET.md) | what is measured and on what: hardware, client, engine, model axes; which backends hold valid data. |
| [`docs/upstream.md`](docs/upstream.md) | upstream issues and PRs we track. A snapshot; prefer `scripts/upstream_sweep.py` for current state. |
| [`docs/sources/`](docs/sources/) | **one file per source sweep**, newest last. Read the newest before sweeping: it records where each surface stood, so a later sweep can diff rather than re-derive. |
| [`docs/candidates-by-vram.md`](docs/candidates-by-vram.md) | **what to try next, by memory class** — one outside bench book, quoted and attributed. Claims, not results; nothing in it is measured here. Its 12 GB rows are the live ones ([#79](https://github.com/evanwtf/local-llm/issues/79)). |
| [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) | the current picks, and how to run them. |

## Order

Ranked by **value per hour against the goal above**. Seven items; everything else
is in the tracker.

**What changed in this ranking.** Engine-speed items moved down and agent-defect
items moved up. Three findings drove it: decode rate does not predict wall time
(measured three times); the pass-rate table has saturated, so a faster backend
cannot be shown to be a better one ([#4](https://github.com/evanwtf/local-llm/issues/4)); and two of our loudest
results turned out to be measurements of our own setup rather than of a model
([#112](https://github.com/evanwtf/local-llm/issues/112), [#120](https://github.com/evanwtf/local-llm/issues/120)). Chasing another +35% decode number
buys less than fixing a client that re-prefills 67 k tokens before every reply.

| # | issue | why here |
|---|---|---|
| 1 | **[#64](https://github.com/evanwtf/local-llm/issues/64)** the KV prefix stalls at ~20,400 tokens, and [#50](https://github.com/evanwtf/local-llm/issues/50) says why | **The largest defect we have found, and it is in the agent, not the model.** `common` freezes at 20,398 while `prompt` grows 25 k → 67 k: every turn re-prefills everything past that point. At ~360 t/s prefill that is **~186 s before a single output token**, and it grows with the conversation. #50 identifies a mechanism and it is cheap to test — Claude Code injects a live token counter as a system message *with* `cache_control`, and the number changes every turn, so the cached prefix can never match. #64's own filing says it plainly: **every Claude Code cell we hold may be a measurement of a broken KV cache rather than of a model.** Fixing it makes the agent faster in real use *and* repairs the data. Upstream-worthy for both Claude Code and ds4 once there is a minimal case. |
| 2 | **[#112](https://github.com/evanwtf/local-llm/issues/112)** the tool-call degeneration loop | **The agent's own failure mode, and the first step is free.** Nine failures on our best new cell, none of them wrong code — the model stops calling tools, narrates about the format, and emits stacked bare `<tool_call>` opens. Restart-between-trials recovered six of nine; the loop itself is untouched. Remedy 1 needs **no new code and no machine time**: the shim already sees every request and response, so count tool errors already in the conversation against the probability the next call is malformed. Remedy 2 shipped 2026-09-03 and is **still unmeasured**, which is how a fix quietly becomes a belief. |
| 3 | **[#136](https://github.com/evanwtf/local-llm/issues/136)** a single A/B run is not a measurement | **Found by accident, and it bears on everything we publish.** Four identical runs of the [#118](https://github.com/evanwtf/local-llm/issues/118) A/B spanned **4.7 pp** — +16.5%, +21.2%, +17.6%, +17.7% — with three inside 1.2 pp and one 3.5 pp outside for no reason we could find. A deliberate cold-start test refuted the obvious explanation. **The trap is that each run looked tight from inside**: per-frontier ranges of 1.154–1.205 and 1.207–1.238, exactly the spread we would have quoted as precision. Every A/B in this repo is one run or a few, and nothing reports between-run spread because nothing had ever run the same A/B twice on purpose. [#23](https://github.com/evanwtf/local-llm/issues/23) covers agent-suite medians and [#130](https://github.com/evanwtf/local-llm/issues/130) covers order *within* a run; neither touches this axis. Cheap to state, and it sets the cost of every future upstream claim. |
| 4 | **[#131](https://github.com/evanwtf/local-llm/issues/131)** nothing *pins* the agent client | **The recording half landed `225b90c`; the part that actually protects a batch did not.** Rows now carry `client_version` directly, so #104's finding — OpenCode 1.18.26 → 1.18.27 roughly **doubling median turns**, 12.0 → 27.5, everything else held — can at last be applied to a single row. What remains is the whole point of the issue: OpenCode still updates itself unasked on both machines, nothing pins it, and `preflight.py` still warns rather than refuses on client drift. A version you record and do not pin is a post-mortem, not a control. |
| 5 | **[#4](https://github.com/evanwtf/local-llm/issues/4)** harder tasks: the current set cannot measure code quality | **The meta-blocker, and the reason the published pass-rate tables have stopped being useful.** [#55](https://github.com/evanwtf/local-llm/issues/55) A/4 flagged three cells at 100% for `gemma426` over five trials. Two combinations clear 90% and cannot be told apart; three more sit at 15/15 and would need ~35 consecutive passes to prove anything. **We cannot currently show that a better agent is better.** Not cheap — the gmail-archive suite has a floor and a Swift class needs `swift_excise.py` care — but every item above and below is measured against it. |
| 6 | **[#120](https://github.com/evanwtf/local-llm/issues/120)** which `ds4-server` state degrades a session | **Six pass-rate points hide in an operational variable.** 36/45 on a continuous server, 42/45 with a restart between trials, 38/45 with the disk-KV budget raised 4x — so disk KV is not it. For an agent you actually use, a server that gets worse the longer it runs is a product defect, not a benchmark artifact. **Start with [#116](https://github.com/evanwtf/local-llm/issues/116)** (fan RPM in `thermals.py`, then a max-fans cycle): cheapest candidate, and `evanwtf/fancontrol` now exists to drive it. Read [#130](https://github.com/evanwtf/local-llm/issues/130) first — a within-session decline is not by itself evidence of throttling. |
| 7 | **[#96](https://github.com/evanwtf/local-llm/issues/96)** oMLX bit-exact tail continuation, TTFT 3-4 s → 0.3 s | **Item 1's problem attacked from the other end.** @Spangler3000's claim is per-turn time-to-first-token, lossless by construction, and it is far above our resolution bar. Median conversation here is **9 turns**, so 3 s of dead air per turn is ~30 s a task spent waiting rather than working — the difference between an agent that feels usable and one that does not. Rust build plus a 3-trial restart-between cycle. The metric already shipped in `ee0228e`, so this pays for itself even if the claim fails. |

**Dropped out of the top ten, and why.** [#117](https://github.com/evanwtf/local-llm/issues/117) (MTPLX runner),
[#115](https://github.com/evanwtf/local-llm/issues/115) (mlx-serve 1M context) and [#39](https://github.com/evanwtf/local-llm/issues/39) item 3 are all
engine-speed work. They were items 5, 6 and 7. Nothing about them got worse —
the ranking rule changed. A third engine serving the same model faster does not
demonstrably produce a better agent while [#4](https://github.com/evanwtf/local-llm/issues/4) stands, and
[#77](https://github.com/evanwtf/local-llm/issues/77) is the cautionary case: a fully executed, well-instrumented
speed comparison that ended in "no wall-time difference measured". Bring them
back up when the suite can tell two good backends apart.

**Behind these:** the engine-speed queue, which is real work with a lower ceiling — [#117](https://github.com/evanwtf/local-llm/issues/117), [#115](https://github.com/evanwtf/local-llm/issues/115), [#39](https://github.com/evanwtf/local-llm/issues/39) item 3, [#119](https://github.com/evanwtf/local-llm/issues/119) (unsloth fork: recommend *not now*), [#109](https://github.com/evanwtf/local-llm/issues/109), [#105](https://github.com/evanwtf/local-llm/issues/105), [#95](https://github.com/evanwtf/local-llm/issues/95) (author's own number moved to +3.5%, below our resolution), [#127](https://github.com/evanwtf/local-llm/issues/127) and [#128](https://github.com/evanwtf/local-llm/issues/128) (new llama.cpp Metal/MTP work from the 03:34Z sweep), [#126](https://github.com/evanwtf/local-llm/issues/126) (VQ quants — a format we have never measured), [#121](https://github.com/evanwtf/local-llm/issues/121)–[#125](https://github.com/evanwtf/local-llm/issues/125), [#134](https://github.com/evanwtf/local-llm/issues/134) (uzu — an Apple-only Rust engine reachable by `uv add`, in none of our sources until 2026-09-04). Then [#80](https://github.com/evanwtf/local-llm/issues/80) (Ollama holds 22 models and 518 GB and six have ever been measured — sharper now that #111 pruned 1.3 TB and #135 is re-downloading 171 GB of it). Then [#55](https://github.com/evanwtf/local-llm/issues/55) (halting plausibility gate; A/3 and A/4 shipped, A/1 and A/2 remain), [#105](https://github.com/evanwtf/local-llm/issues/105) (Perplexity's Lily — HTTP API confirmed, greedy-only decode is the confound; needs a fresh 19 GB pull after the prune), [#109](https://github.com/evanwtf/local-llm/issues/109) (llama.cpp mmap PLE — the discriminating experiment does not need the PR, but does need ds4 stopped), [#40](https://github.com/evanwtf/local-llm/issues/40) (GLM q2 vs q4 — revisit after [#118](https://github.com/evanwtf/local-llm/issues/118) lands), [#86](https://github.com/evanwtf/local-llm/issues/86) (subsumed by [#118](https://github.com/evanwtf/local-llm/issues/118), now measured), [#60](https://github.com/evanwtf/local-llm/issues/60) (its engine-isolation cell is now reachable at 42/45 — items 7-8 above deepen it), [#95](https://github.com/evanwtf/local-llm/issues/95) (author's own number moved to +3.5%, below our resolution), [#51](https://github.com/evanwtf/local-llm/issues/51) (measured at +15.5% in [#91](https://github.com/evanwtf/local-llm/issues/91); waiting on ds4#952 to merge), [#99](https://github.com/evanwtf/local-llm/issues/99) (which machine generates the published tables — decision, not code), [#110](https://github.com/evanwtf/local-llm/issues/110) (watch only via `upstream_sweep.py`), [#83](https://github.com/evanwtf/local-llm/issues/83), [#64](https://github.com/evanwtf/local-llm/issues/64), [#65](https://github.com/evanwtf/local-llm/issues/65), [#66](https://github.com/evanwtf/local-llm/issues/66), [#62](https://github.com/evanwtf/local-llm/issues/62), [#56](https://github.com/evanwtf/local-llm/issues/56), [#57](https://github.com/evanwtf/local-llm/issues/57), [#72](https://github.com/evanwtf/local-llm/issues/72), [#50](https://github.com/evanwtf/local-llm/issues/50), [#41](https://github.com/evanwtf/local-llm/issues/41), [#45](https://github.com/evanwtf/local-llm/issues/45), [#46](https://github.com/evanwtf/local-llm/issues/46), [#70](https://github.com/evanwtf/local-llm/issues/70), [#71](https://github.com/evanwtf/local-llm/issues/71), [#78](https://github.com/evanwtf/local-llm/issues/78), [#27](https://github.com/evanwtf/local-llm/issues/27), [#35](https://github.com/evanwtf/local-llm/issues/35), [#16](https://github.com/evanwtf/local-llm/issues/16), [#18](https://github.com/evanwtf/local-llm/issues/18), [#19](https://github.com/evanwtf/local-llm/issues/19), [#75](https://github.com/evanwtf/local-llm/issues/75), [#88](https://github.com/evanwtf/local-llm/issues/88), [#92](https://github.com/evanwtf/local-llm/issues/92), [#93](https://github.com/evanwtf/local-llm/issues/93), [#97](https://github.com/evanwtf/local-llm/issues/97), and the older operational backlog ([#3](https://github.com/evanwtf/local-llm/issues/3), [#6](https://github.com/evanwtf/local-llm/issues/6), [#7](https://github.com/evanwtf/local-llm/issues/7), [#9](https://github.com/evanwtf/local-llm/issues/9)). [#111](https://github.com/evanwtf/local-llm/issues/111) is effectively done (1.3 TB pruned, exclusions set) but stays open for the operator's Time Machine cleanup and the lunix backup verification.

## Not queued

Open issues that are not in the table, and why they stay off it:

- **[#40](https://github.com/evanwtf/local-llm/issues/40) mixed-precision GLM-5.3.** Right question, behind a working agent path — but it now has a concrete recipe and numbers from ds4#964 (KDA + head BF16 → Q8 inside the Q4_K file, "Q2 speed with a Q4 file"; quality measured on the PR: perplexity improves slightly). [#118](https://github.com/evanwtf/local-llm/issues/118) has now measured the full-precision PR itself on this machine over **four runs — median +17.6% paired decode, spread 16.5-21.2, bit-exact** — so the recipe builds on local data too.
- **GLM thinking/tool-replay (ds4#894, #897, #899, #904, #906).** Defects we would inherit while #569 and #816 stand.
- **Vision, vector steering, ROCm.** Out of scope, and not shipped.
- **More trials on saturated cells.** New axes, not more samples.

## Machine state

### As of 2026-09-04 12:05 EDT

**A 171 GB download is running; nothing is benchmarking.** `hf download` is
re-acquiring the two `#52`/`#91` AProjQ4/AProjQ8 GGUFs from
`antirez/deepseek-v4-gguf` at `refs/pr/22` ([#135](https://github.com/evanwtf/local-llm/issues/135)),
started 11:54:40 at roughly 30 MB/s. **Do not start a measurement until it
finishes** — the run lock does not know about downloads, and 171 GB of disk
traffic under a decode benchmark is exactly the quiet corruption this repo
keeps being caught by.

**[#118](https://github.com/evanwtf/local-llm/issues/118) is measured four
times, not once.** PR head `8969dbb` vs `main` at `b0a147a`: **+16.5%,
+21.2%, +17.6%, +17.7% — median +17.6% paired decode**, `pr964` faster at 32
of 32 frontier-pairs, prefill a small consistent cost (0 of 32 faster),
bit-exact. Three runs fall inside 1.2 pp and one sits 3.5 pp out with no
cause found; a deliberate cold-start test refuted the obvious explanation.
That is [#136](https://github.com/evanwtf/local-llm/issues/136).

**We have posted upstream.** A summary went to
[antirez/ds4#964](https://github.com/antirez/ds4/pull/964#issuecomment-5542955929)
on 2026-09-04, quoting the median with all four runs and the spread shown,
and linking back to the full data on #118. That is the only thing this
project has posted outside `evanwtf`/`evandhoffman`.

**The ds4#952 correction is drafted in intent but NOT posted.** Its audit is
on [#91](https://github.com/evanwtf/local-llm/issues/91#issuecomment-5543081440):
the "paired median" was a ratio of independent medians, the "reproduces
1.155" coincidence does not survive, prefill was not 1.003, and the stated
mechanism — "q4 loses more to drift than q8" — is **inverted** in both
datasets we can still recompute (q8 moves more: +2.0/+4.0% and −3.1/−7.5%).
Four fresh q4/q8 runs are required before anything is sent.

**No server is up.** The `ds4-server` that held 71.6 GiB idle for ten hours,
and the shim on `:8101`, are both stopped.

**`.run-lock.json`** ([#133](https://github.com/evanwtf/local-llm/issues/133)).
`run.py`, `decode_ab.sh`, `decode_ab_engine.sh` and both restart-between-trials
cycles claim the machine before loading anything and refuse if another live
process holds it. A `SIGKILL`ed run leaves the file; preflight reports it
stale, names what it was doing, and does **not** take it. Remove it
deliberately. `--no-lock` opts out.

**ollama is 0.33.3**, installed 2026-09-03 18:19, which crosses the
sampler-precedence boundary in [#84](https://github.com/evanwtf/local-llm/issues/84).
Ninety rows have been written since and all ninety are `qwen38fnds4shim` or
`qwen38fnds4mtp7shim`, which do not go through ollama — so the boundary has
not been crossed in the data. **The next ollama-backed row will be the first
under the new precedence** and must not be pooled with earlier ollama rows.

**Fans are on `auto` and have never been set.** `fancontrol status` is read
during runs; `max` has not been used. The controlled fan experiment
([#116](https://github.com/evanwtf/local-llm/issues/116)/[#120](https://github.com/evanwtf/local-llm/issues/120))
is deliberately still pending. Run 4 of #118 incidentally logged die 48.4 °C
at start and 67.6 °C at finish with the usual within-session decline present
— suggestive against the thermal story, not a test of it.
