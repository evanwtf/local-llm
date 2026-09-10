# Fans auto vs fans max, one build — #276 (parent #116)

Run of 2026-09-09T19:46:50-0400 to 20:55:55-0400 on the M5 Max / 128 GB.
`ds4-bench` at `6289c516` (ds4-pr1014-base), DeepSeek-V4-Flash IQ2XXS
w2Q2K-AProjQ8-SExpQ8-OutQ8 chat-v2 imatrix-0731.

**The arm is the machine state, not the code.** One engine tree, one GGUF, one
sweep definition. The only thing that changes between phases is whether the
fans are under macOS thermal control or pinned to maximum.

## The answer

Max fans hold the die **8.48 °C cooler** under load and buy **0.6% decode**.
The two numbers are reconciled by SoC power, which moves **0.78%**: the
machine was only just thermally limited, so relieving it returns almost
nothing.

| | auto | max | Δ |
|---|---:|---:|---:|
| fan rpm (measured, both fans) | 2,233 | 5,565 | — |
| die peak °C | 97.95 | 89.47 | −8.48 |
| die mean °C | 88.07 | 82.00 | −6.07 |
| SoC power mean W | 94.45 | 95.19 | +0.78% |
| SoC power max W | 123.34 | 126.93 | +2.9% |
| ambient °C | 20.66 | 20.66 | 0.00 |

Paired, adjacent in time (max/auto), median of per-frontier ratios:

| pair | decode | prefill |
|---|---:|---:|
| 01-auto → 02-max | 1.0032 | 1.0440 |
| 03-auto → 04-max | 1.0056 | 1.0175 |
| 05-auto → 06-max | 1.0090 | 1.0089 |
| **median** | **1.0056** | **1.0175** |
| spread | 0.6 pp | 3.5 pp |

**Decode is the trustworthy number.** Three pairs, all positive, 0.6 pp
spread, and consistent with the 0.78% power measurement.

**Prefill should be discounted.** The pairs trend 4.4% → 1.8% → 0.9%, which is
a first-phase artefact rather than an effect: `01-auto` was the first phase of
the run, had the worst within-phase drift (−6.4%) and the slowest absolute
prefill (578.00 tok/s at 16384, against 602.66 and 607.04 for the later auto
phases). Pairs 2–3 give ~1.35%.

Within-phase drift improves modestly: decode −3.2% → −2.6%, prefill −5.1% →
−4.0% (mean across the three phases of each condition).

## The protocol

Recorded verbatim in the manifest, and asserted by
`tests/test_fan_ab_protocol.py`:

```
1. max fans until the die plateaus (idle GPU)
2. set fans to the arm's mode
3. begin test
4. end test
5. max fans until the die plateaus
6. begin test
7. end test
8. max fans until the die plateaus   <- once, after the last cycle
9. fans auto
```

Three segments, arms `auto` then `max`, so phases run A,B,A,B,A,B and a linear
ambient drift cannot align with condition. It did not need to: ambient came
out identical to two decimals across both arms.

**The cooldown gate is a slope, not a difference of means.** Settled means the
least-squares slope over a trailing 180 s is flatter than 0.30 C/min — the p90
of a settled die's own slope, measured in `scripts/calibrate_settle.py`. A
difference of two 30-second medians against 0.3 C is 36 C/hour restated as a
rate, and passes any slower fall indefinitely.

**The bulk cooldown always runs on max**, whatever arm follows, because the
cooldown exists to make phases start alike rather than to reach a particular
temperature. It also biases conservatively: the auto arm gets a cooler start
than ordinary auto operation would give it.

## Files

| file | what |
|---|---|
| `rows.csv` | **start here.** 144 rows = 6 phases × 3 reps × 8 frontiers, 49 columns. Every row self-contained: throughput, thermals, power, fan rpm, ambient, provenance. |
| `sensors-1hz.csv` | monitord at 1 Hz across the whole 69-minute window, cooldowns included. The per-rep aggregates in `rows.csv` cannot reproduce the cooldown curves; this can. |
| `fan-ab-manifest.json` | the run's own record: protocol, per-phase cooldown outcome, slope, waited seconds, start die temperature. |
| `NN-cond-repN.csv` | raw `ds4-bench` output, unmodified. |
| `NN-cond-repN.log` | that rep's engine stdout. |
| `run.log` | the driver's log, including every cooldown decision. |

Regenerate `rows.csv` with `uv run python scripts/fan_ab_collate.py
benchmarks/ds4/fan-ab-276`, and the summary with `scripts/fan_ab_report.py`.

## What is exact and what is attributed

`ds4-bench` writes no per-row timestamp. The manifest records exact ISO 8601
start and end for each **rep**, and a rep covers 8 frontiers in ~55 s. So
every `*_rep_*` column is exact for the rep and attributed to each of its
rows. No per-frontier timestamp was invented to join against.

Ambient is interpolated linearly between the two samples bracketing each rep
boundary, with the bracketing stamps and their gap kept in the row.

## Caveats

- **`start_die` spread was 5.91 °C** (25.02–30.93). Phase 1 is the outlier at
  30.93 because it followed a long idle rather than a load, so its slope hit
  the bound early at a higher temperature. After phase 1 the arms are not
  systematically different (auto 26.02 mean, max 26.45).
- **`gpu.utilization` swings 0.54–0.99 within a single condition**, so the
  auto/max difference in that column is not interpretable and nothing here
  rests on it.
- Three pairs is the minimum this repo accepts for a claim. It is the
  minimum.

## Cooldown times, a secondary result

Every cooldown reached plateau; none hit the 900 s ceiling. An earlier attempt
cooling on **auto** did not: from 74.11 °C it reached only 34.13 °C in 420 s
and was still falling at 0.809 C/min. On max, 74.43 °C reached plateau at
26.21 °C in 707 s.

Forced cooling also reaches a much lower floor — die 26.21 °C against ambient
21.0, a ΔT of 5.2 °C, versus roughly 10–11 °C on auto. That is about **half**
the idle gradient removed, not the 23% an earlier short-dwell measurement
suggested; that measurement had not finished cooling.

**If max fans are worth running, it is for this, not for the 0.6%.** They
remove roughly 45 minutes of cooldown from a six-phase A/B.
