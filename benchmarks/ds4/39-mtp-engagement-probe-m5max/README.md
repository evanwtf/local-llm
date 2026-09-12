# ds4 embedded MTP — engagement probe on an M5 Max

**Issue:** [#39](https://github.com/evanwtf/local-llm/issues/39). Precondition for the full measurement, using the [#210](https://github.com/evanwtf/local-llm/issues/210) treatment gate. Related: [#266](https://github.com/evanwtf/local-llm/issues/266), [#151](https://github.com/evanwtf/local-llm/issues/151).

**What this is.** `scripts/mtp_treatment_gate.py probe` — no trials, no rows. It
loads the MTP stack and measures whether the draft head *engages* at two prompt
sizes before any timed suite runs. It does not measure agent wall time.

**Setup.** `~/git/ds4-metal` at `ba01f5d`, backend `qwen38fnds4mtp7shim`.
Model `Qwen3.8-Flash-Next-Q4KExperts` (symlink → `Q40RoutedExperts`, arch
`qwen4-exp`) + PLE-Q4_1 sidecar + `qwen3.8-flash-next-q4-mtp.gguf` (arch
`qwen4-exp-mtp`), `--mtp-draft 7 --mtp-timing`, ctx 100000. M5 Max, 128 GiB,
Metal. Machine FREE. Run 2026-09-12 12:46 EDT. Probe pad 0 and 11000, arms
plain/tools, repeats 2, max-tokens 200.

## Findings

**1. The draft head engages.** The sidecar loads `state=ready draft=7`; the
graph line shows `MTP=Q4_K/Q8_0/BF16 verifier=block/max16`. When MTP runs,
acceptance is healthy — across the engaged cycles, accepted-per-cycle spans
0..7 and the single most common value is **7 of 7** (58 cycles). This is not a
silently-failed draft head.

**2. ds4 adaptively bypasses MTP when it is not winning.** Across the probe's
808 `Qwen MTP timing` cycles:

| cycle kind | count | share |
|---|---:|---:|
| scheduler-bypass (`drafted=0`, plain decode) | 591 | 73% |
| MTP-engaged (`drafted>0`) | 215 | 27% |

Three logged `scheduler switching to target decode` events each show the
engine measuring the MTP cycle **slower** than plain decode and switching off:
`actual=110.5ms baseline=65.6ms`, `97.6/64.9`, `97.1/64.4`. So "MTP on" is not
a fixed treatment — the engine self-regulates per run, and most cycles here ran
plain.

**3. This reframes two issues.**
- **#210**: a row reporting `accepted: 0` can be the scheduler *correctly*
  bypassing a losing speculation, not a broken draft head. The treatment gate
  still matters, but zero-acceptance is not by itself evidence of breakage.
- **#39**: an MTP-on vs MTP-off agent measurement will partly measure the
  scheduler's decisions. The full run must record the engaged/bypass split per
  row, not just a flag.

**Not measured here:** agent wall time or pass rate. The bimodal probe
wall-times (pad=11000: 21s vs 5.6s at identical acceptance counts) are prefill
cache miss vs hit, not an MTP effect — which is exactly why the timed #39 suite
is a separate step.

Evidence: `engagement-pad0.json`, `engagement-pad11000.json`,
`mtp-scheduler-evidence.log` (sidecar/graph, the three scheduler switches, and
the accepted-length histograms).
