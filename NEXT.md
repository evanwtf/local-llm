# Where to pick up

Written 2026-08-27 21:55, immediately before a laptop reboot. Work the issues in
the order below. Each issue is self-contained; this file only sets priority and
records the machine state a reboot destroys.

## Order

| # | issue | why this position |
|---|---|---|
| 1 | **#30** Raise `iogpu.wired_limit_mb` | **Needs the operator + sudo.** Blocks #32 and unlocks a rung on #31. One command. |
| 2 | **#31** Re-run Qwen3.8-Flash-Next at `UD-Q3_K_XL` | Does NOT need #30 -- 83.8 GiB fits the current default with 23 GiB spare. The 2-bit quant was never necessary, and this backend is already 15/15 with Codex. |
| 3 | **#32** Retry GLM with Unsloth `UD-Q2_K_XL` + PR #27752 | Blocked on #30. A matched pair needing none of #25's seven patches, on the PR most likely to merge. Only candidate that reduces the Qwen monoculture (#16). |
| 4 | **#26** ds4 wall time swings 3x between trials | Cheap, and it gates how every other number is read. |
| 5 | **#24** Correct two published verdicts | Do after the re-runs -- they change what the corrected text should say. Now also needs the "107 GiB budget" language fixed (see #30). |
| 6 | **#23** No combination clears 90% with confidence | Methodology. Should shape the rewrite in #24 and how future batches are sized. |
| 7 | **#28** llama.cpp vs Ollama on identical weights | Targets the #14 re-prefill, the largest single measured cost here. |
| 8 | **#27** Retire the ds4 fork | Blocked on upstream merging antirez/ds4#885 and #886. Housekeeping. |

**#22 (finals) is DONE** -- both finalists ran clean, 30/30. `ds4anthropic x codex`
reached 36/36 lifetime and is the first combination here to clear 90% with 95%
confidence. `ornith15 x codex` is 40/42 and ~1.6x faster.

**#25 (GLM via PR #27773) is a closed negative result** -- it loads and runs and
emits gibberish. Superseded by #32.

Then the older backlog: #13 (re-baseline Ollama 0.33.1), #17 (GLM background),
#16 (monoculture), #4 (harder tasks -- increasingly the bottleneck).

## The open question all of this serves

*What is the most useful model + harness for local coding if hosted providers are
unavailable?*

Best current answer, stated with its uncertainty: **`ds4anthropic x codex`**,
21/21 and 190.7s, on a self-contained C engine with no Python runtime and the
only non-Qwen lineage in the field. The honest caveat is that 21/21 only
establishes ">85%" (#23), and `ornith15 x codex` is 1.8x faster at a pass rate
this data cannot distinguish from it. #22 is designed to break that tie.

One finding is already firm: **Codex beats Claude Code on every local backend
measured.** No Claude Code pairing exceeds 94%.

## Correction carried into everything above

The **107.0 GiB "Metal budget"** quoted throughout `RECOMMENDATIONS.md`,
`RESULTS.md` and `benchmarks/llamacpp/llamacpp-up` is a macOS **default**
(`iogpu.wired_limit_mb = 0`), not a hardware wall. Several "too big for this
machine" verdicts rest on it. See #30.

It does not revive `qwen38flashnext` -- that peaked at 126.51 GiB, still above a
raised 112 GiB ceiling.

## Settled tonight

The `upstream/main` merge into ds4 (`399acbb`) is good: **25/25 PASS**
post-merge across both clients. The apparent slowdown that showed up first was a
warm-up artifact, not a regression -- see #26.

## Machine state a reboot destroys

- **`ds4-server` and Ollama are both stopped.** Restart ds4:
  ```sh
  cd ~/git/ds4 && ./ds4-server -m gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf \
      --warm-weights --ctx 100000 --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192
  ```
  Binary is `399acbb` (the merge); `./ds4-server --version` confirms it. The
  first trial after any restart is slow -- that is #26, not a regression.
- **The session scratchpad is gone**, including `finals.sh`. Its commands are
  reproduced in full in #22.

## Machine state a reboot preserves

- `~/git/ds4/gguf/GLM-5.3-Flash-Q2.gguf` -- 96.5 GB, downloaded, unused
- `~/git/llama.cpp-glm53` -- worktree at `9370c82db` (PR #27773), **not built**
- `~/git/llama.cpp` -- the qwen4exp build (`035e22731`). Do not `git pull` this
  away; `qwen38fnq2`'s provenance stamp depends on it.
- `~/.codex/*.config.toml` -- profiles, not in git. All need
  `wire_api = "responses"`; Codex 0.148.0 removed `"chat"`.

## Two traps worth not rediscovering

**Do not poll `pgrep -f 'benchmarks/agent/run.py'` from a shell that waits on it.**
The waiter's own command line contains that string, so it matches itself and the
loop never exits. This idled the machine for 4 minutes tonight. Sequential steps
in one script need no polling at all.

**Do not run anything else while benchmarking.** A 96 GB download overlapped one
timing batch tonight and produced an hour of chasing a regression that did not
exist.
