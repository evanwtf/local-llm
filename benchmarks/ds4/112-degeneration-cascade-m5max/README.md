# The #112 tool-call degeneration, with transcripts this time — an M5 Max run

**Issue:** [#112](https://github.com/evanwtf/local-llm/issues/112). The first
`qwen38fnds4shim` run whose transcripts survive, so the cascade hypothesis is
testable at last.

**Why this run exists.** #112's open question is a **context-poisoning
cascade**: once a tool error enters the conversation, does the model's next tool
call get likelier to be malformed? The 2026-09-11 read-out found this
**untestable on the held ledger** — all 41 `qwen38fnds4shim` `solution_empty`
rows had no surviving transcript. This run produces the missing evidence.

**Setup.** `scripts/degeneration_cascade_run.py`, one arm, the cell #112 was
found on: `ds4-metal` `ba01f5d`, the Q4 fast-pack, **MTP off** (graph line
`MTP=off verifier=off`), the tool-format shim on :8101 with **no** temperature
pinning and its strip remedy active (`26cd07c`, the current default). OpenCode
1.18.30, harness `a44b2db`, `harness_dirty=False`. 15 tasks × 3 trials = 45.
Run 2026-09-12 15:58–17:58 EDT, M5 Max, 128 GiB, Metal. `--client-log`
captured every transcript; `scripts/degeneration_cascade.py` read them.

## Result — the error-triggered cascade is not supported here

`scripts/degeneration_cascade.py` over the 45 transcripts (evidence:
`cascade-summary.json`):

| quantity | value |
|---|---|
| trials | 45 (40 passed, 4 `solution_empty`, 1 wrong-code) |
| tool errors across all conversations | **5** |
| turns that complained about the format and landed no call | **3**, in 3 trials |
| of those, recovered a clean tool call afterward | 1 |
| **P(malformed \| 0 prior tool errors)** | **3 / 347 (1%)** |
| **P(malformed \| 1 prior tool error)** | **0 / 35 (0%)** |

**All three malformed turns happened with zero tool errors before them.** Of
the 35 tool-attempt steps that *followed* a tool error, none was malformed. So
in this run the degeneration does not ride on a prior tool error — it is a
low-rate (~1% of tool-attempt steps) event that appears without one.

The three malformed turns are not one failure mode:
- `storage-put-and-sweep` t1 — narrates the format complaint, then stops. A death (`solution_empty`).
- `parser-mbox-quoting` t3 — a malformed turn at step 0, then recovers clean `bash`/`read` calls, but the trial still ends empty.
- `script-reverse` t3 — trailing format-narration *after* the solution was already written. The trial **passed**. A malformed turn is not the same as a failure.

## Against the original run, and the caveats

The #94 run that opened #112 scored 36/45 with **9** `solution_empty`, clustered
**2 / 2 / 5** across trials 1/2/3 — the shape that suggested a cascade. This run:
**4** `solution_empty`, spread **2 / 1 / 1**, not clustered in trial 3. One of
the four (`swift-downsample-buckets` t1) is the #55 task-setup artifact, not
degeneration. The shim's strip remedy is active here, which is the current
production config and the likeliest reason the classic stacked-`<tool_call>`
signature is rarer than pre-remedy.

- **Small numbers.** 3 malformed turns and 5 tool errors cannot settle the
  hypothesis; this run fails to support it rather than refuting it. The value is
  that the question is now measurable and the instrument
  (`scripts/degeneration_cascade.py`, tested) exists to accumulate more.
- **n = 1 per cell** (3 trials, 1 each). Not a 3-trial median per #23.
- The shim-level rejection (`invalid Qwen tool call`) appears as model-quoted
  text, not as a `tool_use` error; it occurred once and that trial recovered
  and passed.

Evidence: `cascade-summary.json` (the aggregate; the raw transcripts are prompt
captures and are not committed, per `CONVENTIONS.md`). Rows are in the M5 Max
`results.jsonl` under batch `112-cascade`.
