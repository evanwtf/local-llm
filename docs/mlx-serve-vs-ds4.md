# mlx-serve vs ds4

The scoreboard for the two Qwen3.8-Flash-Next stacks on the M5 Max. Add a row
to **Measurements** each time the pair is measured; do not rewrite a row.

## Standing, as of #577 (2026-09-20): mlx-serve takes 65% of ds4's wall

ds4 upstream main `8db1d1d1` against mlx-serve 26.9.4, 4+4 sweeps, 120 trials,
macOS 27.0, OpenCode 1.18.31:

- **Summed wall over 15 tasks × 4 sweeps: mlx-serve 3770 s, ds4 5822 s** —
  mlx-serve took 65% of ds4's time (35% less).
- Paired ratio ds4/mlx-serve **1.84, 95% CI 1.59–2.13**. mlx-serve is faster on
  14 of 15 tasks, ties 1, loses none. All four sweeps agree.
- Pass: mlx-serve 60/60, ds4 59/60. Indistinguishable (sign test p = 1.00).

This is a screen (n=60 per arm resolves ~17–26% paired wall). It fixes the
direction and rough size, not a precise margin. The macOS 27 settled rerun
(2026-09-29, [`macos-26-vs-27.md`](macos-26-vs-27.md)) points the same way:
mlx-serve 26.9.6 summed 865 s against ds4 `0aaea5a2` at 1223 s, one trial per
task.

**It is a stack comparison.** Engine, quantization and speculative decoding
move together: mlx-serve speculates by default (prompt lookup, PLD), ds4 does
not. Part of the win is speculation, but that is the engine's shipped default,
which is what this project measures. Do not credit it to the engine alone
(#138, #191). mlx-serve also holds ~100 GiB resident against ds4's ~68–80 GiB.

## The two stacks

| | mlx-serve | ds4 |
|---|---|---|
| weights | `Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit` (~100 GiB) | Q4 GGUF with PLE n-grams (68–80 GiB) |
| speculative decoding | **prompt lookup, on by default** (`draft_len=5`, `key_len=3`) | none unless an MTP sidecar is passed |
| tool-call shim | none; runs bare on :11234 | behind the tool-format shim on :8101 (server on :8000) |

Each row records its engine version and tree; read those, not this table.

## What is comparable, and what is not

**Only the stacks.** The same weights cannot run on both engines: the MLX path
has no GGUF reader, MLX has no PLE sidecar for the GGUF path, and the bundled
llama.cpp has no `qwen4exp` graph. So no sentence about these two may credit a
component. Report every head-to-head as a stack comparison.

The one-variable experiments that can attribute something, both done:

- **mlx-serve with and without `--no-pld`** (#224, from #262's rows): PLD does
  **not** cause the wall tail. Tail rate (`num_turns > 20`) was 13.3% with PLD
  and 9.3% without, 75 rows each, intervals overlapping.
- **ds4 with and without its MTP sidecar** (#39): the draft head engages, but
  ds4's scheduler bypasses it on ~77% of agent decode cycles. Agent wall time
  was within noise of the control (136.2 s against 120.9 s median).

## Known behavior: mlx-serve has a wall-time tail

mlx-serve is typically faster and occasionally much slower. In #191, ds4's
maximum across 120 rows was 20 turns and mlx-serve's was 334. Every excursion
**passed**, so this is a wall-time tail, not a reliability one.

The #225 census (`scripts/tail_events.py`, 2084 rows) put mlx-serve at 23/196 =
11.7% (95% CI 7.9–17.0) against 2.3% for all other backends. **mlx-serve does
not own the high rate**: `qwen36` ran 14.7% (5/34), and six other backends have
overlapping intervals. Do not write "mlx-serve is tail-prone"; write that it
sits in the high-tail cluster.

The tail moves between tasks. #191's 1377 s `parser-mbox-quoting` trial did
not reproduce in #225, and a census puts that task at 3.3%, mid-pack. **Do not
drop a task and quote the remainder.**

## Traps for the next comparison

- **The geometric mean hides the tail.** #191's registered statistic
  compressed an 8.08× gap to 2.96× on the one task that mattered. Report total
  wall beside it.
- **Pass rate cannot discriminate.** Both arms clear the task set at 59–60 of
  60 (#79 saturation). Do not make pass rate the primary endpoint.
- **`n_pairs` is 15, not 60.** Both endpoints pair by task.
- **The shim asymmetry is deliberate.** ds4 runs shimmed, mlx-serve bare. The
  shim corrects a tool-call pathology that is ds4's, not the model's; a probe of
  mlx-serve found the pathology absent.
- **`ds4-mtp-timing` reports no draft counters for the mlx arm**, which invites
  the conclusion that it did not speculate. It did: 439 draft lines in one sweep
  (#222).
- **mlx-serve rows record no `server_argv`** (#320), so the rows alone cannot
  show that every mlx trial ran one configuration. Keep the run record.

## Measurements

| date | issue | what | headline |
|---|---|---|---|
| 2026-09-08 | #191 | 4+4 sweeps, 120 rows, mlx-serve 26.9.1 vs ds4 | total wall tied (mlx 1.3% slower); paired ratio 0.68 (0.54–0.87); mlx wins 13/15 tasks; one 1377 s mlx tail |
| 2026-09-08 | #225 | 4+4 sweeps, 120 rows, mlx-serve `25de4d5` vs 26.9.1 | **null**: pass 59/60 both, ratio 1.00 (0.83–1.20); #191's tail did not reproduce |
| 2026-09-10 | #282 | 2+2 sweeps ×2 runs, 120 rows, mlx-serve 26.9.2 vs ds4 `ffd85d42` | mlx took 53% of ds4's summed time: **751 s against 1429 s**; wins 13/15; pass ds4 60/60, mlx 59/60 |
| 2026-09-10 | #282 | the same, ds4 at `6c1e8367` | mlx took 66%: **818 s against 1237 s**; wins 12/15; pass 60/60 both |
| 2026-09-20 | #577 | 4+4 sweeps, 120 rows, mlx-serve 26.9.4 vs ds4 main `8db1d1d1` | mlx took 65%: **3770 s against 5822 s**; ratio 1.84 (1.59–2.13); wins 14/15; pass mlx 60/60, ds4 59/60 |

## Evidence

- #191 run artifacts:
  `hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/benchmarks/mlx-serve/191-ab/`;
  report `scripts/stack_agent_report_191.py`.
- Later runs: `scripts/stack_agent_ab.py` drives them and
  `scripts/stack_agent_report.py` reads them out. Rows carry
  `env.harness_head` and `harness_dirty`.
