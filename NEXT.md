# Where to pick up

Written 2026-08-27 21:55, immediately before a laptop reboot. Work the issues in
the order below. Each issue is self-contained; this file only sets priority and
records the machine state a reboot destroys.

## Order

| # | issue | why this position |
|---|---|---|
| 1 | **#22** Finals: 3 rounds of `ds4anthropic x codex` and `ornith15 x codex` | Interrupted mid-run. Commands are in the issue, and it is the only queued work that can *settle* a reliability claim rather than add to it. |
| 2 | **#26** ds4 wall time swings 3x between trials | Cheap to test and it gates how every other number is read. Until it is understood, wall-time rankings across the project are suspect. |
| 3 | **#25** GLM-5.3-Flash: build llama.cpp PR #27773 | Weights already on disk (89.9 GiB). The only new *capability* queued, and the only non-Qwen candidate besides ds4 (#16). |
| 4 | **#24** Correct two published verdicts | Do after 1 and 2 -- both change what the corrected text should say. |
| 5 | **#23** No combination clears 90% with confidence | Methodology. Should shape the rewrite in #24 and how future batches are sized. |
| 6 | **#27** Retire the ds4 fork | Blocked on upstream merging antirez/ds4#885 and #886. Housekeeping. |

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
