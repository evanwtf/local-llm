# Where to pick up

Updated 2026-08-28, after #24 and the reader fixes it turned up. Work the issues in the
order below. Each issue is self-contained; this file only sets priority and
records machine state that is not in git.

## Order

| # | issue | why this position |
|---|---|---|
| 1 | **#26** Wall time swings 3x between trials | Now replicated on a **third** backend: GLM ran `storage-blob-put` at 375.7 / 641.6 / 315.7 across three rounds. Every speed ranking in this project is noise until this is understood. Cheap to test. |
| 2 | **#4** Harder tasks | **This is now the structural bottleneck.** Four Codex backends sit at 15/15 or better; the suite can no longer discriminate on correctness, and speed is the only remaining axis -- which #26 says is unreliable. Without harder tasks, more benchmarking produces no new information. |
| 3 | **#33** Offload the n-gram PLE table to SSD | Concrete and lossless: ~29 GiB saving, which makes `UD-Q4_K_XL` comfortably resident. AtomicChat's `-M64` GGUFs need no MLX and no patches. |
| 4 | **#34** Evaluate SSD offload as a strategy | Do **step 1 first** regardless of #33: nothing here records the machine's NVMe read bandwidth or random-read latency, and every "SSD offload is fine" claim rests on it. |
| 5 | **#23** Sizing batches for confidence | Retitled in spirit: **two** combinations now clear 90% (`ds4 x claude` 46/46, `ds4anthropic x codex` 36/36), so the issue's premise is spent. What remains useful is the sizing rule -- ~35 consecutive passes to clear 90% -- and deciding how many trials a new backend gets. |
| 6 | **#28** llama.cpp vs Ollama on identical weights | Targets #14 re-prefill, the largest single measured cost here. |
| 7 | **#35** Model evaluation queue | Standing index. Devstral (#3) is the highest-value entry -- it would add a fifth lineage. |
| 8 | **#27** Retire the ds4 fork | Blocked on upstream merging antirez/ds4#885 and #886. Housekeeping. |

## Done since the last update

- **#24** published verdicts corrected. Auditing them turned up **two live
  reader bugs**, both of which had already reached published tables:
  a timeout writes no `passed` key, so `if "passed" in row` dropped three
  `qwen38fnq2 x claude` failures and printed 13/13 for a 13/16 backend; and
  `summarize.py` still hand-rolled `r.get("excluded")`, missing the fourteen
  rows marked `confound`/`contaminated` -- every published OpenCode number was
  computed over them. `results.py` gained `verdict()` and `trials()`;
  `summarize.py` now reads through it. **Do not test `row["passed"]` directly.**
- **#30** sysctl applied and verified: Metal ceiling **107.52 -> 112.00 GiB**.
  **NOT persisted** -- see machine state below.
- **#31** Qwen3.8-Flash-Next at `UD-Q3_K_XL`: **15/15, 28.4% faster than 2-bit**,
  faster on all five tasks. The bigger quant is the quicker one.
- **#32** GLM-5.3-Flash: **15/15**, zero patches, zero warnings. The matched pair
  (Unsloth `UD-Q2_K_XL` + PR #27752) was the whole trick.
- **#22** finals: both finalists 15/15.
- **#25** closed as a negative result -- GLM on PR #27773 loads, runs, and emits
  gibberish.
- **#16** materially addressed: GLM is the first non-Qwen, non-DeepSeek backend
  that works. Five lineages now represented (#35).

## The open question all of this serves

*What is the most useful model + engine + harness for local coding if hosted
providers are unavailable?*

**Answer: `ds4`, with either Claude Code or Codex.** Not "Codex only" -- that
claim stood here until 2026-08-28 and the clean data refutes it.

| combination | pass | 95% CI | suite |
|---|---|---|---|
| `ds4 x claude` | 46/46 | **92-100%** | 858.2s |
| `ds4anthropic x codex` | 36/36 | **90-100%** | 975.3s |
| `ds4anthropic x claude` | 29/30 | 83-99% | 1120.6s |
| `ornith15 x codex` | 40/42 | 84-99% | **597.0s** |
| `qwen38fnq3 x codex` | 15/15 | 80-100% | 895.8s |
| `glm53 x codex` | 15/15 | 80-100% | 1362.1s |
| `qwen38fnq2 x claude` | 13/16 | 57-93% | 5235.7s |

Pooling the two ds4 wire protocols -- same weights, same server -- gives
**Claude Code 75/76 (982s) and Codex 36/36 (975s)**. Two clients, seven seconds
apart, intervals almost entirely overlapping. Nothing here separates them.

**Two combinations now clear 90% at 95% confidence, and they are the same model
under different clients.** `ornith15 x codex` is 1.6x faster and still cannot be
distinguished from either. The three at 15/15 need ~35 consecutive passes to
clear 90%; they are promising, not proven.

Note what this table cannot tell you: **which writes better code.** Nearly
everything passes. That is #4, and it is why this table has stopped being
informative.

## Machine state

**Not persisted, and a reboot reverts it:**

```sh
sudo sysctl iogpu.wired_limit_mb=114688     # currently applied, verify: 112.00 GiB
```

Verify with the Metal probe in #30, not with `sysctl` -- the sysctl reads `0`
whether or not a limit is in force. **`glm53` will not load without this**
(100.6 GiB resident against a 107.52 GiB default).

**Servers:** a GLM `llama-server` may still be on :8030 with its shim on :11501.
`ds4-server` and Ollama are stopped. Restart ds4 with:

```sh
cd ~/git/ds4 && ./ds4-server -m gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf \
    --warm-weights --ctx 100000 --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192
```

**Three llama.cpp worktrees, do not confuse them:**

| path | commit | purpose |
|---|---|---|
| `~/git/llama.cpp` | `035e22731` (PR #27742) | qwen4exp. **Every `qwen38fnq2`/`q3` row depends on it. Do not `git pull` this away.** |
| `~/git/llama.cpp-glm52pr` | `8a8d0bcc4` (PR #27752) | serves `glm53`. Clean, unpatched. |
| `~/git/llama.cpp-glm53` | `9370c82db` (PR #27773) | the failed attempt, **166 lines of uncommitted patches**. Two are independently upstream-worthy (#25). Do not build GLM here. |

**Weights on disk:** `~/models/Qwen3.8-Flash-Next-GGUF` (157 GB, Q2 + Q3),
`~/models/GLM-5.3-Flash-GGUF` (101 GB, Unsloth Q2),
`~/git/ds4/gguf/GLM-5.3-Flash-Q2.gguf` (90 GB, antirez -- **unusable**, no engine
loads it; keep per the archive convention or delete deliberately).

**Codex profiles** in `~/.codex/*.config.toml` are not in git. All need
`wire_api = "responses"`; 0.148.0 removed `"chat"`.

## Traps worth not rediscovering

**Write results through `results.py`.** Never hand-roll an exclusion filter --
five different keys have meant "untrustworthy row", and an analysis that checked
one silently counted fifteen bad rows as good data (#29).

**`/health` answers before the model is loaded.** GLM answered at 4 s and did not
finish loading until 33 s. A request in that window returns no `choices` and
looks exactly like a broken model.

**Coherence-check at temperature 0 before every benchmark.** A model can load,
serve, and report plausible token counts while emitting noise -- that is #25, and
it cost hours.

**A timeout is a failure, not an absence.** It writes `error` and no `passed`
key. Read verdicts with `results.verdict()` and denominators with
`results.trials()`; `if "passed" in row` silently shrinks the denominator and
turned a 13/16 backend into a published 13/13.

**Do not poll `pgrep -f 'benchmarks/agent/run.py'` from a shell that waits on
it.** The waiter's own command line matches, so the loop never exits.

**Do not run anything else during a timing batch.** A 96 GB download overlapped
one and produced an hour of chasing a regression that did not exist.
