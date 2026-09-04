# Where to pick up

> ## ⚠️ OpenCode results before 2026-08-31 21:47 EDT are INVALID
>
> Any OpenCode trial recorded before `2026-08-31T21:47:18-04:00` measures a
> harness bug -- the client was never told which directory to work in, so it
> solved each task and wrote the answer somewhere else. **Do not quote, pool,
> or compare against those numbers.** Cause, cutover and replacements:
> [docs/archive/results-opencode-pre-dir.md](docs/archive/results-opencode-pre-dir.md). Other clients are unaffected.

Updated **2026-09-04 00:29 EDT**. **This file is the queue for _this machine_ —
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

Ranked by **value per hour against the goal above**. Ten items; everything else
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
| 3 | **[#130](https://github.com/evanwtf/local-llm/issues/130)** alternate arm order between rounds | **Nearly free, and it protects every A/B we will ever run.** @adamlawi measured positional bias up to **0.53 pp** on GB10 — larger than three of the four effects being compared, and at one context point it decides the *sign*. Not thermal on their side: clocks pinned, temperatures logged. They cite our own within-session ratio (1.19 → 1.13) as the second data point. Our arms ran A-then-B, never interleaved. The fix is to alternate and average pair ratios, and to record run order on the row. Do it before [#39](https://github.com/evanwtf/local-llm/issues/39) item 3, whose expected effect is small enough for order to matter. |
| 4 | **[#131](https://github.com/evanwtf/local-llm/issues/131)** nothing pins the agent client | **A silent client update rewrites results and looks like a model regression.** OpenCode moved 1.18.26 → 1.18.27 by itself on both machines; on the Linux tier that alone roughly **doubled median turns** on repository tasks, 12.0 → 27.5, everything else held ([#104](https://github.com/evanwtf/local-llm/issues/104)). The symptom was the `edit` tool failing to match `oldString` — which reads as the model writing bad patches. No row records a client version, so it cannot be applied backwards. We learned this once with Codex 0.148 → 0.150 and caught it only because someone noticed. Pin it, record it on the row, and make `preflight.py` refuse rather than warn. |
| 5 | **[#4](https://github.com/evanwtf/local-llm/issues/4)** harder tasks: the current set cannot measure code quality | **The meta-blocker, and the reason the published pass-rate tables have stopped being useful.** [#55](https://github.com/evanwtf/local-llm/issues/55) A/4 flagged three cells at 100% for `gemma426` over five trials. Two combinations clear 90% and cannot be told apart; three more sit at 15/15 and would need ~35 consecutive passes to prove anything. **We cannot currently show that a better agent is better.** Not cheap — the gmail-archive suite has a floor and a Swift class needs `swift_excise.py` care — but every item above and below is measured against it. |
| 6 | **[#120](https://github.com/evanwtf/local-llm/issues/120)** which `ds4-server` state degrades a session | **Six pass-rate points hide in an operational variable.** 36/45 on a continuous server, 42/45 with a restart between trials, 38/45 with the disk-KV budget raised 4x — so disk KV is not it. For an agent you actually use, a server that gets worse the longer it runs is a product defect, not a benchmark artifact. **Start with [#116](https://github.com/evanwtf/local-llm/issues/116)** (fan RPM in `thermals.py`, then a max-fans cycle): cheapest candidate, and `evanwtf/fancontrol` now exists to drive it. Read [#130](https://github.com/evanwtf/local-llm/issues/130) first — a within-session decline is not by itself evidence of throttling. |
| 7 | **[#118](https://github.com/evanwtf/local-llm/issues/118)** reproduce ds4#964 on this M5 Max | **The one item where we advance someone else's project with data only we have.** @trueimage tagged @evandhoffman directly. Q2 now measures **+34.8% to +37.3% decode with prefill flat within 0.1%** at four context points, ABBA order, `main` built in a separate worktree so Metal shaders match. Every other Apple measurement on that PR is M3 Ultra; nobody has an M5 Max on it. Their follow-up — requantizing **only KDA and head, BF16 → Q8, inside the Q4_K file** — reports "Q2 speed with a Q4 file" with **quality untested**, which is [#40](https://github.com/evanwtf/local-llm/issues/40)'s question with a recipe attached. Still waiting on merge. |
| 8 | **[#96](https://github.com/evanwtf/local-llm/issues/96)** oMLX bit-exact tail continuation, TTFT 3-4 s → 0.3 s | **Item 1's problem attacked from the other end.** @Spangler3000's claim is per-turn time-to-first-token, lossless by construction, and it is far above our resolution bar. Median conversation here is **9 turns**, so 3 s of dead air per turn is ~30 s a task spent waiting rather than working — the difference between an agent that feels usable and one that does not. Rust build plus a 3-trial restart-between cycle. The metric already shipped in `ee0228e`, so this pays for itself even if the claim fails. |
| 9 | **[#84](https://github.com/evanwtf/local-llm/issues/84)** record the resolved sampler, not just the regime | **Cross-engine pass rates stay provisional until this lands.** `top_p 0.95` is 20/21 and `top_p 0.90` is 7/15 on the same task, model, engine and client ([#36](https://github.com/evanwtf/local-llm/issues/36)) — a default nobody chose can halve the pass rate, and Ollama and ds4 rows still record no sampling at all. The regime tag shipped (`a7b9a0f`) but says only which side of a boundary a row was taken on. `scripts/gguf_meta.py` already reads the KVs; wire it into `probe_ollama()`. Small code, and it closes the half of #84 that matters. |
| 10 | **[#129](https://github.com/evanwtf/local-llm/issues/129)** nothing tells us CI has gone red | **Cheap insurance on everything above.** Both red streaks were found by a person looking — 40 of 40 runs, then 20 runs over 17 hours that took **7 minutes to fix**. A green local suite is not evidence: `d9a223e` broke only on hosts that are not this laptop. Put a `gh run list` check in `preflight.py`, which already runs before every measurement session. |

**Dropped out of the top ten, and why.** [#117](https://github.com/evanwtf/local-llm/issues/117) (MTPLX runner),
[#115](https://github.com/evanwtf/local-llm/issues/115) (mlx-serve 1M context) and [#39](https://github.com/evanwtf/local-llm/issues/39) item 3 are all
engine-speed work. They were items 5, 6 and 7. Nothing about them got worse —
the ranking rule changed. A third engine serving the same model faster does not
demonstrably produce a better agent while [#4](https://github.com/evanwtf/local-llm/issues/4) stands, and
[#77](https://github.com/evanwtf/local-llm/issues/77) is the cautionary case: a fully executed, well-instrumented
speed comparison that ended in "no wall-time difference measured". Bring them
back up when the suite can tell two good backends apart.

**Behind these:** the engine-speed queue, which is real work with a lower ceiling — [#117](https://github.com/evanwtf/local-llm/issues/117), [#115](https://github.com/evanwtf/local-llm/issues/115), [#39](https://github.com/evanwtf/local-llm/issues/39) item 3, [#119](https://github.com/evanwtf/local-llm/issues/119) (unsloth fork: recommend *not now*), [#109](https://github.com/evanwtf/local-llm/issues/109), [#105](https://github.com/evanwtf/local-llm/issues/105), [#95](https://github.com/evanwtf/local-llm/issues/95) (author's own number moved to +3.5%, below our resolution), [#127](https://github.com/evanwtf/local-llm/issues/127) and [#128](https://github.com/evanwtf/local-llm/issues/128) (new llama.cpp Metal/MTP work from the 03:34Z sweep), [#126](https://github.com/evanwtf/local-llm/issues/126) (VQ quants — a format we have never measured), [#121](https://github.com/evanwtf/local-llm/issues/121)–[#125](https://github.com/evanwtf/local-llm/issues/125). Then [#55](https://github.com/evanwtf/local-llm/issues/55) (halting plausibility gate; A/3 and A/4 shipped, A/1 and A/2 remain), [#105](https://github.com/evanwtf/local-llm/issues/105) (Perplexity's Lily — HTTP API confirmed, greedy-only decode is the confound; needs a fresh 19 GB pull after the prune), [#109](https://github.com/evanwtf/local-llm/issues/109) (llama.cpp mmap PLE — the discriminating experiment does not need the PR, but does need ds4 stopped), [#40](https://github.com/evanwtf/local-llm/issues/40) (GLM q2 vs q4 — revisit after [#118](https://github.com/evanwtf/local-llm/issues/118) lands), [#86](https://github.com/evanwtf/local-llm/issues/86) (subsumed by item 7 above), [#60](https://github.com/evanwtf/local-llm/issues/60) (its engine-isolation cell is now reachable at 42/45 — items 7-9 above deepen it), [#95](https://github.com/evanwtf/local-llm/issues/95) (author's own number moved to +3.5%, below our resolution), [#51](https://github.com/evanwtf/local-llm/issues/51) (measured at +15.5% in [#91](https://github.com/evanwtf/local-llm/issues/91); waiting on ds4#952 to merge), [#99](https://github.com/evanwtf/local-llm/issues/99) (which machine generates the published tables — decision, not code), [#110](https://github.com/evanwtf/local-llm/issues/110) (watch only via `upstream_sweep.py`), [#83](https://github.com/evanwtf/local-llm/issues/83), [#64](https://github.com/evanwtf/local-llm/issues/64), [#65](https://github.com/evanwtf/local-llm/issues/65), [#66](https://github.com/evanwtf/local-llm/issues/66), [#62](https://github.com/evanwtf/local-llm/issues/62), [#56](https://github.com/evanwtf/local-llm/issues/56), [#57](https://github.com/evanwtf/local-llm/issues/57), [#72](https://github.com/evanwtf/local-llm/issues/72), [#50](https://github.com/evanwtf/local-llm/issues/50), [#41](https://github.com/evanwtf/local-llm/issues/41), [#45](https://github.com/evanwtf/local-llm/issues/45), [#46](https://github.com/evanwtf/local-llm/issues/46), [#70](https://github.com/evanwtf/local-llm/issues/70), [#71](https://github.com/evanwtf/local-llm/issues/71), [#78](https://github.com/evanwtf/local-llm/issues/78), [#27](https://github.com/evanwtf/local-llm/issues/27), [#35](https://github.com/evanwtf/local-llm/issues/35), [#16](https://github.com/evanwtf/local-llm/issues/16), [#18](https://github.com/evanwtf/local-llm/issues/18), [#19](https://github.com/evanwtf/local-llm/issues/19), [#75](https://github.com/evanwtf/local-llm/issues/75), [#88](https://github.com/evanwtf/local-llm/issues/88), [#92](https://github.com/evanwtf/local-llm/issues/92), [#93](https://github.com/evanwtf/local-llm/issues/93), [#97](https://github.com/evanwtf/local-llm/issues/97), and the older operational backlog ([#3](https://github.com/evanwtf/local-llm/issues/3), [#6](https://github.com/evanwtf/local-llm/issues/6), [#7](https://github.com/evanwtf/local-llm/issues/7), [#9](https://github.com/evanwtf/local-llm/issues/9)). [#111](https://github.com/evanwtf/local-llm/issues/111) is effectively done (1.3 TB pruned, exclusions set) but stays open for the operator's Time Machine cleanup and the lunix backup verification.

## Not queued

Open issues that are not in the table, and why they stay off it:

- **[#40](https://github.com/evanwtf/local-llm/issues/40) mixed-precision GLM-5.3.** Right question, behind a working agent path — but it now has a concrete recipe and numbers from ds4#964 (KDA + head BF16 → Q8 inside the Q4_K file, "Q2 speed with a Q4 file", quality untested). Rides along with item 7's rebuild.
- **GLM thinking/tool-replay (ds4#894, #897, #899, #904, #906).** Defects we would inherit while #569 and #816 stand.
- **Vision, vector steering, ROCm.** Out of scope, and not shipped.
- **More trials on saturated cells.** New axes, not more samples.

## Machine state

### As of 2026-09-04 00:29 EDT

**Nothing is benchmarking.** The #77 arm B re-run finished 2026-09-03 21:42 at
25/45 (9/6/10), identical to its no-restart total — MTP is a net cost on this
workload, and #77 is closed. The kv-32768 test ran 38/45 (12/15, 15/15, 11/15)
— disk KV is not the mechanism, so the session decline is [#120](https://github.com/evanwtf/local-llm/issues/120).

**Still up, holding memory, from that last arm:**

| what | where |
|---|---|
| `ds4-server` | :8000, **MTP on** `--mtp-draft 7 --mtp-timing`, `--kv-disk-dir ~/.ds4/server-kv-mtp`, ~74 GiB |
| `ds4_qwen_tool_shim.py` | :8101 -> :8000 |

**Stop both before any batch that is not `qwen38fnds4*`.** Run
`uv run python benchmarks/agent/preflight.py` first; it names them. One known
false alarm while it does: selecting only a shim backend flags the upstream
ds4-server as stale ([#132](https://github.com/evanwtf/local-llm/issues/132)).

Both reference repos are restored and clean (`gmail-archive` @ `56e55cc`,
`monitor` @ `cbb85ca`). The tree is `main`, clean.

**Top-of-queue items 1 and 2 need no machine time** — item 1 is a trace of one
real Claude Code trial, item 2 is counting in the shim — and neither requires
the servers above. Stop them first.

The exact argv, the two KV directories, and the Metal ceiling procedure are in
[`docs/m5max-runbook.md`](docs/m5max-runbook.md).