# Harder ladder — where DS4 actually stops being reliable

Follow-up to [`AGENT_FINDINGS.md`](AGENT_FINDINGS.md), which recorded four
straight passes and concluded *"the rungs were too easy"*. This ladder targets
what the first could not reach: debugging, ambiguity, large context, and
recovery from failure.

**Model:** DeepSeek V4 Flash, mixed q2/q4 0731, resident, via `ds4-server`
**Date:** 2026-08-09 · **Scope:** [#9](https://github.com/evanwtf/ds4/issues/9)

---

## Result

| rung | task | wall | result |
|---|---|---|---|
| A | debug: symptom visible, cause elsewhere | 263 s | **PASS** |
| B | ambiguous investigation, judgement required | 295 s | **PASS** |
| C | large-context audit (36,598 lines, 5 files) | 450 s | **MIXED** — 2 of 5 solid |
| D | recovery from its own error | 359 s | **MIXED** — corrected, then over-corrected |

**The wall is not where I expected it.** Mechanics are strong. The failure mode
is **asserting runtime consequences from reading code without executing it**,
and then **capitulating too readily when challenged**.

---

## Rung A — debugging (PASS)

Planted a truncation bug in a 30-line C program: the symptom (`... | q2`) is
visible in `main`, the cause is a `#define` conflating two different buffer
sizes. The agent was told only the symptom.

It:

- correctly identified that the `strncat` bounds in `build_summary` were
  **already correct**, avoiding the obvious wrong fix
- traced the byte-by-byte fill to explain the exact cut point (20 → 42 → 45
  chars, 2 bytes left, so `q2q4_0731: …` became `q2`)
- chose **dynamic sizing** over bumping the constant, with reasoning: *"stays
  fragile if rows or label lengths grow"*
- verified under `-Wall -Wextra -Werror` **and ASan/UBSan, unprompted**
- volunteered the tradeoff (double-format per row) and offered the simpler
  alternative

Independently verified: output correct, code sound (malloc + null check + free).

This is the strongest single result across both ladders.

---

## Rung B — ambiguity (PASS)

Asked it to determine whether the `kvcache_bytes=0` anomaly is a bug or expected
behaviour — a **genuinely open question** I had gotten wrong twice in
`REPORT.md` (first "artifact at the final frontier", then noting the pattern
did not hold, then leaving it unexplained).

It found **two independent mechanisms**, which explain both observations exactly:

1. `ds4_bench.c:713` — `need_restore_after_generation = gen_tokens > 0 && frontier < ctx_max`.
   The **final row of every sweep** is therefore always 0.
2. `ds4_bench.c:721` — payload above `snapshot_max_bytes` (1 GiB) skips the
   snapshot, so **every row past the crossing point** is 0 in long sweeps.

All claims verified against source. Line numbers were off by 2–10 (`817` cited
as `807`), the mechanism exactly right.

Its judgement — *"expected behaviour of a flawed metric, not an accidental
bug"* — is defensible and better than my own analysis was. It proposed a minimal
fix, explained why existing non-zero values would be unchanged, flagged that
`ds4_session_payload_bytes` returns 0 in distributed mode (verified:
`ds4.c:50087`), and offered a truer-but-larger alternative. It respected "no code
changes yet".

---

## Rung C — large context (MIXED)

Audit argument parsing across five files, 36,598 lines, report the top 5 real
inconsistencies. Verification of each claim:

| # | claim | verdict |
|---|---|---|
| 1 | ds4-bench accepts 0/negative `--prefill-chunk` | ❌ **false** — `parse_int` (`ds4_bench.c:103`) already guards `v <= 0` |
| 2 | `--expert-profile` only in cli/bench | ✅ **true** — confirmed by grep *and* runtime (`unknown option`) |
| 3 | dir-steering flags missing from eval/bench | ✅ **true** |
| 4 | server float parser lacks `isfinite` | ⚠️ **true in source, masked at runtime** — see below |
| 5 | agent `parse_backend` lacks a rocm branch | ✅ **true in source**, not runtime-tested (needs a ROCm build) |

**It ranked the false one first.** The pattern: reliable on *what the code says*
(greppable structure — 4 of 4 correct), unreliable on *what the code does*
(runtime consequence), and it presented both with identical confidence.

### The `-ffast-math` subtlety

Claim 4 deserves its own note, because the naive verdict is wrong in both
directions.

`ds4_server.c:12696` really does omit the `isfinite` check that
`ds4_cli.c:211` has. But empirically the server rejects both `inf` and `nan`:

```
ds4-server: invalid value for --mtp-margin: inf
ds4-server: invalid value for --dspark-confidence: nan
```

`inf` is caught by the range check (`inf > 1000`). `nan` should slip through —
NaN comparisons are always false — yet it does not. Cause: the Makefile
(line 13) compiles with **`-ffast-math`**, which lets the compiler assume no
NaNs and changes the comparison result:

```
cc -O3           →  nan reject=0   (accepted; the gap is real)
cc -O3 -ffast-math → nan reject=1   (rejected; masked by the flag)
```

So the missing check is a **genuine latent gap masked by an optimisation flag**.
Drop `-ffast-math` and `ds4-server` would accept NaN while `ds4` rejects it.
The agent's instinct was right; its stated consequence was wrong for this build.

**I made the same error it did**, twice: first marking claim 4 "confirmed" from
reading code without testing, then calling it a false positive without
understanding the mechanism. Reading code is not running it — for either of us.

---

## Rung D — recovery (MIXED, most informative)

Told it claim 1 failed empirically and asked it to re-examine, verify by
running things, and say whether the other four still stand.

**What it did well:**

- ran all four binaries with `-1` and `0` and tabulated results
- found the exact cause (`parse_int`'s `v <= 0` guard) and quoted it
- admitted the error plainly: *"The claim was a code-reading error on my part."*
  No hedging, no deflection.
- was **honest about not having the prior transcript**: *"that report is not
  stored anywhere I can reach … I won't guess"* — rather than fabricating its
  own earlier findings

**Where it failed:**

- **Over-corrected.** It concluded *"the parsing is consistent and
  well-validated across all four binaries"* and *"unlikely the other findings
  survive scrutiny either"*. But claims 2, 3 and 5 are **true** — flags that
  exist in some binaries and not others. Those are not validation claims and
  were untouched by the challenge.
- **Silently narrowed scope**: re-audited four binaries while claiming "all
  four", having dropped `ds4_agent.c` — the file underlying claim 5.

One correct claim was challenged; it discarded four, three of which were right.
That is the sycophancy failure mode, and for a coding agent it is expensive:
push back on a correct diagnosis and it may abandon it.

**Architectural note:** each `claude-ds4 -p` invocation is a fresh session, so
there is no memory across rungs. The honesty about this was good behaviour, but
it is a real constraint — multi-session workflows need context passed explicitly.

---

## What this means for routing (#15)

**Route locally with confidence:**

- reading and explaining code (both ladders, verified accurate)
- mechanical edits, including byte-exact ones (rung 2, first ladder: perfect)
- debugging with a reproducible symptom (rung A: excellent)
- multi-file mechanical refactors (rung 4, first ladder)
- investigation where a human reviews the conclusion (rung B)

**Do not route locally without review:**

- **any claim about runtime behaviour derived from reading code.** This is the
  documented failure mode. Demand it run the thing.
- **audits producing ranked lists** — ranking was worse than the individual
  findings; its top item was its only clearly false one.
- **anything where you may push back on a correct answer.** It capitulates.

**The mitigations are cheap:** ask for empirical verification explicitly ("run
it, don't just read it"), and when challenging an answer, ask it to re-verify
each claim separately rather than reconsider the set.

---

## Prediction scorecard, updated

First ladder: 1 of 5 correct — I predicted mechanical failure and got none.

This ladder: I predicted the wall would be *ambiguity and judgement*. Wrong
again — rung B (pure judgement, no ground truth) was one of the strongest
results. The actual wall is **epistemic**: distinguishing what the code says
from what it does, and holding a correct position under pressure.

Two ladders, and the failure modes were somewhere I did not look both times.

---

Transcripts: [`ladder2/`](ladder2/). Test artifacts: `ladder2/report.c`.
