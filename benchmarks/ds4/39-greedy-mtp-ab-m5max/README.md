# ds4 embedded MTP vs greedy control — agent suite on an M5 Max

**Issue:** [#39](https://github.com/evanwtf/local-llm/issues/39) (and [#151](https://github.com/evanwtf/local-llm/issues/151), [#210](https://github.com/evanwtf/local-llm/issues/210)). The first ds4 MTP agent rows that carry a *verified* treatment.

**What this is.** `scripts/greedy_mtp_ab.py`, 2 rounds, 1 trial, arm order
alternated (#130). Paired arms, both pinned to temperature 0 through a
`SHIM_TEMPERATURE=0` shim on :8102 (ds4 reaches its MTP path only at temp ≤ 0,
`ds4.c:80120 at ds4-metal ba01f5d`):

- `qwen38fnds4mtp7greedy` — MTP on, `--mtp-draft 7`
- `qwen38fnds4greedy` — MTP off, the control for the greedy regime

`ds4-metal` `ba01f5d`, `Q4KExperts`+PLE+`qwen3.8-flash-next-q4-mtp` sidecar,
OpenCode client. M5 Max, 128 GiB, Metal. Run 2026-09-12 12:55–15:21 EDT.

## Result — no #23-grade difference; MTP ≈ control, slightly slower at the median

`scripts/report.py`, resolution rule applied:

| arm | passed | median wall | worst | spread |
|---|---|---|---|---|
| `qwen38fnds4mtp7greedy` (MTP on) | 20/26 | **136.2s** | 627.9s | 87.2× |
| `qwen38fnds4greedy` (control) | 23/26 | **120.9s** | 297.5s | 41.9× |

The medians are **13% apart** — below the 56% two 3-trial medians must differ by
to be real (#23), and below #39's own 26% "worth a series break" bar. Per task
the direction is mixed: of the five tasks that clear 56%, MTP is faster on two
(mbox-scan, parser-mbox-quoting-nodoc) and slower on three (parser-date,
script-transform, swift-chartaxis-spacing); the other ten are within noise.

## Why — the treatment is real but seldom applied

The MTP arm's rows carry the treatment, so this is not #210's "accepted:0"
problem. Across 45 MTP rows:

- **drafting_share** (fraction of decode cycles the scheduler let MTP draft):
  min 0.03, **median 0.23**, max 1.00 — MTP is bypassed on roughly three
  quarters of agent decode cycles. 9 of 45 rows drafted in under 10% of cycles.
- **accept_rate when drafting**: min 0.14, **median 0.58**, max 0.86 — when MTP
  does draft, acceptance is decent.

So ds4's adaptive scheduler (documented in the engagement probe,
`benchmarks/ds4/39-mtp-engagement-probe-m5max/`) bypasses MTP whenever its
per-cycle cost exceeds plain decode, which on agent traffic is most of the
time. The decode-rate win MTP offers in a tight generation loop does not
survive contact with an agent workload — re-prefill and context handling
dominate, exactly the project's standing finding that decode rate does not
predict agent wall time.

## Caveats

- n = 2 rounds, 1 trial. The "no material difference" conclusion is robust to
  this (a 13% gap rarely becomes >56% with one more trial), but this is not a
  3-trial cell.
- The MTP arm retried failed trials (3 rows/task vs the control's 2), several of
  them short #266 prefill-failure runs; `report.py`'s resolution rule collapses
  these to 26 cells/arm.
- `report.py` warns the rows do not record `server_argv`; arm identity here
  rests on the driver's server control and the per-row `draft` counters, not on
  a stamped argv.
- Control `swift-downsample-buckets` is UNTOUCHED (#55) — every trial failed with
  the same oracle output, a task-setup artifact, not an MTP effect.

Remaining #39 arms not run here: `--mtp-exact-sampling` (step 3) and `--mtp` on
the DeepSeek V4 Flash primary (step 4).

Evidence: `report-*.log` (the resolved comparison). Rows are in the M5 Max
`results.jsonl` under batch `greedy-mtp-ab`.
