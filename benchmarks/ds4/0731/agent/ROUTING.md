# Routing process: local DS4 vs frontier model

The deliverable of [epic #9](https://github.com/evanwtf/ds4/issues/9). Derived
from two capability ladders ([`AGENT_FINDINGS.md`](AGENT_FINDINGS.md),
[`LADDER2_FINDINGS.md`](LADDER2_FINDINGS.md)) plus the model benchmarks in
[`../REPORT.md`](../REPORT.md).

**Model:** DeepSeek V4 Flash, mixed q2/q4 0731, resident, via `ds4-server`.

---

## The one-line rule

> **Route locally when the work is mechanical or verifiable. Keep it on a
> frontier model when the deliverable is a judgement you cannot cheaply check.**

DS4's limits are not where intuition puts them. It is *strong* at editing,
refactoring and debugging; it is *weak* at knowing whether its own claims are
true, and at holding a correct position when challenged.

---

## Green — route locally

Evidence: passed cleanly, verified independently.

| task | evidence |
|---|---|
| Read and explain code | ladder 1 step 1 — all 7 named functions verified real |
| Mechanical edits, including byte-exact | ladder 1 step 2 — identical to a perfect `s///g`, 32 s |
| Multi-file mechanical refactor | ladder 1 step 4 — 5 binaries, no regressions |
| Debug from a reproducible symptom | ladder 2 rung A — found a distant cause, fixed it properly |
| Investigation where a human reads the conclusion | ladder 2 rung B — resolved an anomaly `REPORT.md` got wrong twice |

Also reasonable by extension, though untested: commit messages, boilerplate,
test scaffolding, log and data analysis.

**Why these are safe:** each has a cheap verification step. The build either
compiles or it does not; the test either passes or it does not; the named
function either exists or it does not. DS4's failure mode is confident wrong
claims, and all of these make a wrong claim immediately visible.

## Yellow — route locally, but review before acting

| task | why |
|---|---|
| Any claim about **runtime behaviour** derived from reading code | The documented failure mode. In the audit it was 4-of-4 on greppable structure and wrong on consequence — presented with identical confidence. |
| **Ranked** lists or prioritised audits | Ranking was worse than the individual findings. Its top-ranked item was its only clearly false one. |
| Anything where you may **push back** | It capitulates. Told one claim was wrong, it retracted four — three of which were true. |

**Mitigations, both cheap:**

1. **Demand execution:** *"run it, don't just read it"*. Rung A shows it verifies
   well (ASan/UBSan unprompted) when the task frames verification as the job.
2. **When challenging, isolate:** ask it to re-verify *each claim separately*
   rather than reconsider the set. Challenging one claim collapsed four.

## Red — keep on a frontier model

| task | why |
|---|---|
| Deliverables that **are** an unverifiable judgement | Its confidence is uncalibrated; you cannot tell a good answer from a bad one without doing the work yourself. |
| Code review where findings must be **defended** | It abandons correct positions under pressure. A reviewer that folds is worse than none. |
| Long autonomous runs | Longest measured is 450 s. Multi-hour unattended behaviour is unknown. |
| Anything irreversible or outward-facing | Force-pushes, deploys, published writing, deletions. Not a capability judgement — the cost of being wrong is asymmetric. |
| Work needing memory across sessions | Each `claude-ds4 -p` is a fresh session (see below). |

---

## Mechanics

**Start the server** (once; leave running):

```sh
cd ~/git/ds4 && ./ds4-server \
  -m gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf \
  --warm-weights --ctx 100000 \
  --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192
```

Do **not** add `--trace` unless debugging cache behaviour — it logs full prompts
including your `CLAUDE.md`. See the warning in `AGENT_FINDINGS.md`.

**Switch model:**

```sh
~/bin/claude-ds4      # local DS4   (~/bin is not on PATH by default)
claude                # frontier
```

The wrapper preflights the server and fails clearly if it is down.

---

## When local work goes wrong

**Escalate, do not iterate.** Each `-p` invocation is a fresh session with no
memory of previous ones, so "try again with a hint" restarts from zero rather
than building on context. If the first attempt is wrong in substance — as
opposed to needing a mechanical retry — hand the task to a frontier model with
the failed output as context rather than nudging DS4 twice.

**Watch for the two signatures:**

- *Narrates instead of acting* — describes what it would do rather than calling
  a tool. Seen with weak system prompts; Claude Code's is directive enough that
  it did not surface, but thinner harnesses may hit it.
- *Confident behavioural claim with no execution* — "this accepts negative
  values", "this is silently ignored". Ask it to run the thing.

---

## What this costs and saves

Not yet quantified — that is [#16](https://github.com/evanwtf/ds4/issues/16),
and it is only worth doing now that green-list work exists.

Known inputs:

- **Speed:** 488 t/s prefill, 35 t/s generation, context validated to 256k.
  Comfortable for interactive work.
- **Prefix caching works:** 84 hits vs 16 misses across both ladders, 7.27M
  tokens served from cache. Agent turns resend a large unchanged prefix and it
  is being reused.
- **Thermals:** 1234 MHz / 26.4 W mean under agent load — *less* demanding than
  benchmark sweeps, because agent work alternates compute with waiting on tools.
- **Latency per task:** 32 s (single edit) to 450 s (large audit).

Not counted yet: API spend avoided, and the supervision overhead of reviewing
yellow-list output.

---

## Honest summary

DS4 cleared the mechanical bar far more convincingly than predicted — the
prediction record across both ladders was **1 of 5, then wrong again**. Byte-
exact editing, multi-file refactors and debugging from a symptom all passed
first try.

What it cannot yet be trusted with is **knowing when it is wrong**. That is a
narrower limitation than "not good enough for real work", and it maps cleanly
onto a routing rule: give it work whose correctness is cheap to check, and check
it.

*Untested and worth revisiting:* a real coding benchmark
([#14](https://github.com/evanwtf/ds4/issues/14)), long-context *quality* as
opposed to speed, and multi-hour session stability.
