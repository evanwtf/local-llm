# The testing set

**What this project measures, and on what.** Four axes — hardware, client,
engine, model — plus the task set that runs across them. Anything not listed
here is not part of the matrix; anything listed as retired stays documented
because rows in `results.jsonl` still reference it.

Updated 2026-09-01.

---

## Hardware

| | |
|---|---|
| **Machine** | MacBook Pro, **Apple M5 Max, 128 GB** unified memory |
| **OS** | macOS 26.6.2 |
| **Metal ceiling** | `iogpu.wired_limit_mb = 114688` (112 GiB), set at boot by a LaunchDaemon |

**One machine is the point, not a limitation.** Every number in
`results.jsonl` shares a hardware baseline, which is what makes the
comparisons mean anything. The Metal ceiling is a **cap, not a reservation** —
with no model loaded, wired memory sits around 5 GiB — but it is *required*,
not an optimisation: stock gives ds4 a 75.5 GiB budget against an 89.87 GiB
GLM-5.3, which is a refusal.

**A second tier exists and has run trials.** `desktop`, reachable over ssh:

| | |
|---|---|
| **GPU** | RTX 3080 Ti, **12 GiB**, Ampere `sm_86`, driver 595.71.05, CUDA 13.2 |
| **CPU** | AMD Ryzen 9 7900X, 12 cores / 24 threads |
| **RAM** | **30 GiB** |
| **Disk** | 1.8 TB NVMe, 1.3 TB free |
| **OS** | Ubuntu 24.04 · **not always-on** |
| **Confinement** | **none** — `sandbox-exec` is macOS-only, so `workspace_escapes` is unenforced there |

The 30 GiB figure is the one that matters: with 12 GiB of VRAM it makes the
MoE CPU-offload path real rather than theoretical, which is the live experiment
in #20.

**No FP8 and no NVFP4** on Ampere, and no MLX at all — so most of the model
list above is Mac-only.

**Its trials do not go in `results.jsonl` beside the Mac's.** Mixing hardware
would quietly break every existing comparison. This is now enforced: the
harness refuses to append when the file already holds rows from other
hardware. It is enforced because it already happened — the first Linux run
appended 13 rows and nothing objected, surfacing only when a later `git pull`
refused to merge over them.

---

## Client

**OpenCode, and nothing else** — `opencode run --dir "$PWD"`.

The client axis is measured and closed. One server, one session, same task:
**Aider 11.1 s, OpenCode 39.5 s, Claude Code 189.6 s**, with prompt sizes of
737 / 11,721 / 85,413 tokens. The cause is prompt size, paid on every turn.
Aider is far faster and passes 22/34 inside a repository against OpenCode's
91/93, so "use the fastest client" would be wrong advice.

OpenCode is fixed as the answer by the project's premise: a local setup exists
to keep working when a vendor does not, so the agent has to be open too.

| also wired | run only when |
|---|---|
| Claude Code | the question is about Claude Code itself |
| Codex | the question is about Codex itself |
| Aider | a deliberate low-prompt reference point |
| hosted **Opus 5** | establishing a new task class's ceiling |

**`--dir` is not optional.** `opencode run` attaches to a persistent server
holding its own working directory and ignores the caller's `cwd`. Every
OpenCode row before **2026-08-31 21:47 EDT** measures that bug, not the client.

---

## Engines

Three, each for a different reason.

| engine | build pinned as | why it is in the set |
|---|---|---|
| **llama.cpp** | `llamacpp_head` + `build_info` (`b10751-3466812d1`) | The fast pick. Source build, so a commit is the only identifier |
| **ds4** (DwarfStar) | `ds4_head` + `server_argv` | The only engine that runs DeepSeek-V4-Flash — our one independent lineage |
| **Ollama** | version string + `digest_<backend>` | The 31 GB entry point, and the only path for `ornith15`, `gemma4`, `qwen36coding` |

Ollama is here on **friction, not speed**. Dropping it would delete the
recommendation a newcomer actually follows.

**Retired: LM Studio** (2026-09-01). Its runtime is llama.cpp underneath, so on
the same GGUF it can only add a layer — and it does: identical UD-Q3_K_XL
weights and client, **90 s median against 122 s**, correctness identical. Kept
in `tasks.toml` as `retired`, not deleted: 27 rows reference it and they are
unreadable without the sampler and context length that block records.

**Untested leads, tracked in #60:** Rapid-MLX, oMLX, mlx-serve, pMLX, MTPLX,
mlx-dspark. #60 is about engines we have *never run*; retiring LM Studio is the
opposite operation and does not narrow it.

**`b10729` is preserved** at `~/llamacpp-builds/b10729/bin`. It produced every
published llama.cpp number, and a `git pull` plus in-place rebuild would have
destroyed it.

---

## Models

**Six have valid current data.** These are the rows any published figure may
be drawn from — OpenCode, after the `--dir` cutover, not excluded.

| backend | model | engine | size | valid rows |
|---|---|---|---|---|
| `qwen38fnq3` | Qwen3.8-Flash-Next `UD-Q3_K_XL` | llama.cpp | 83.8 GiB | 30 |
| `ds4` | DeepSeek-V4-Flash 0731 | ds4 (OpenAI wire) | 90.9 GiB | 30 |
| `glm53ds4` | GLM-5.3-Flash | ds4 | — | 28 |
| `qwen36coding` | `qwen3.6:27b-coding-mxfp8` | Ollama | 31 GB | 24 |
| `ornith15` | `ornith-1.5:35b` | Ollama | 22 GB | 21 |
| `gemma4` | `gemma4:31b-mxfp8` | Ollama | 32 GB | 12 |
| `ds4anthropic` | DeepSeek-V4-Flash 0731 | ds4 (Anthropic wire) | 90.9 GiB | 18 |

`ds4` and `ds4anthropic` are the clean wire-format isolation: identical weights
and server, only the protocol differs.

**Configured but unmeasured under the current client.** These are wired in
`tasks.toml` and carry no valid OpenCode rows. They are candidates, not
results:

`gemma426` · `qwen36a3b` · `ornith` · `qwen` · `qwen36` · `qwen38flashnext` · `qwen38fnq2` ·
`qwen38fnq4m64` · `ornith15llamacpp` · `glm53` · `glm52ds4` · `glm53ds4shim` ·
`mtplx` · `opus5`

**`gemma426` is next up.** `gemma4:26b-mlx-bf16` is Gemma 4 26B A4B — the model
on Eigen Labs' MLX Fast leaderboard that Google amplified on 2026-09-01 with a
"2x faster on a Mac" claim. We already held the weights, so the baseline is
measurable here without waiting for the leaderboard's kernels to reach a
runtime we can use.

**`gemma4` has now been run**: 12/12 under OpenCode, the first non-Qwen and
non-DeepSeek backend to complete a cell (#16). It is the slowest stack measured
— a 383s excision median against llama.cpp's 90s — so it is a fallback rather
than a recommendation.

**The monoculture is real but no longer unanswered.** Of the seven measured,
four are Qwen derivatives. DeepSeek-V4-Flash, GLM-5.3 and now Gemma 4 are the
three other lineages.

---

## Tasks

Fifteen defined, in three classes. The exact prompt for each is published in
[`benchmarks/agent/PROMPTS.md`](benchmarks/agent/PROMPTS.md), generated from
the file the harness reads, with a test that fails if the two drift.

| class | count | what it measures |
|---|---|---|
| **Excision** (Python) | 8 | Find your way around unfamiliar code. One function body deleted; the repo's own suite is the only oracle |
| **Excision** (Swift) | 5 | The same, off the model's comfort ground — and a compile step Python cannot fail at |
| **Script** | 2 | Empty directory, produce a working CLI. Trivial logic, real boilerplate, almost no variance |

Script tasks vary 1.0–2.1× and are the fair way to compare stacks. Excision
tasks are noisier and closer to real work.

---

## What counts as valid

A figure may be quoted only from rows that are **OpenCode**, **after
2026-08-31 21:47 EDT**, and **not excluded**. Use `results.is_excluded()` —
never a hand-rolled `r.get("excluded")`, which misses `agent_error` rows and
has already produced two sets of published numbers that were wrong.

Every row stamps the harness commit, the engine build, the target repo commit
and the Metal ceiling. A row that cannot name the code and the engine that
produced it cannot be re-derived once either moves.

---

## Changing the set

Retire, do not delete. `retired = "<reason>"` in `tasks.toml` drops a backend
from the default matrix; naming it with `--backend` still runs it, so a
decision can be revisited without editing config back in. The config block is
the record of how its existing rows were made.
