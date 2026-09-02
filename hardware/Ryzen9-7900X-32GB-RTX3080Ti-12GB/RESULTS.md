# Results — Ryzen9-7900X-32GB-RTX3080Ti-12GB

Reached over ssh as `desktop`. **Not always on.** Directory name derived by
`uv run python scripts/hardware_id.py`, not typed.

**These rows are never pooled with the Mac's.** One file, one hardware
baseline (#20). `results.foreign_hardware()` enforces it, and the run that
produced this file was refused until it was pointed at
`--results hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/results.jsonl`.

---

## Machine

| | |
|---|---|
| CPU | AMD Ryzen 9 7900X, 12 cores / 24 threads, boost 5737 MHz |
| Memory | **32 GB installed** (2 × 16 GB DDR5-4800, 2 of 4 slots), 30.5 GiB usable |
| GPU | NVIDIA GeForce RTX 3080 Ti, **12,288 MiB**, compute capability **8.6** (Ampere `sm_86`) |
| Disk | 1.8 TB NVMe |
| Arch | x86_64 |

## Operating system and drivers

| | |
|---|---|
| Distribution | Ubuntu 24.04.4 LTS |
| Kernel | **7.0.0-30-generic** |
| NVIDIA driver | **595.71.05** |
| CUDA runtime | 13.2 (via the driver) |
| CUDA toolkit | **not installed** — no `nvcc` |

## Software under test

| | |
|---|---|
| Engine | **Ollama 0.33.2** |
| Client | **OpenCode 1.18.26** |
| Harness | `1fc598e` |
| Python | 3.12.3 · uv 0.12.9 |
| Target repo | `evanwtf/gmail-archive` @ `56e55cc` |

**Ollama is deliberately 0.33.2, not the 0.33.3 prerelease.** 0.33.3 makes GGUF
sampler keys outrank Ollama's built-in defaults (#84), and matching the Mac's
engine version is worth more than being newest.

**Confinement is `none`.** `sandbox-exec` is macOS-only, so `workspace_escapes`
is unenforced here (#81). Every row records it. `source_repo_intact` was `True`
for all 12 trials and no escape was detected, but the guarantee is weaker than
the Mac's and the rows say so.

---

## 2026-09-02: `gemma4:12b-it-q4_K_M` under OpenCode — **0/12**

> ### ⚠️ These rows were measured in a 4,096-token window
>
> They record `context_tokens: 131072`. The server served **4096** — Ollama's
> default, never overridden. Nothing checked, so the field recorded what the
> backend asked for rather than what it got. **The 0/12 and the reading below
> are not safe to cite**, and the same bug turned an ornith trial from a
> 1566.9s failure into a 93.5s pass once corrected. Re-run before quoting.
> Guard added in `230eeeb`; see the ornith section below.


Sampler from the model's own Modelfile: `temperature 1, top_k 64, top_p 0.95`.
Model digest `4eb23ef187e2`.

| task | trial 1 | trial 2 | trial 3 | result |
|---|---|---|---|---|
| `script-reverse` | 14.9s | 14.3s | 22.4s | **0/3** — `reverse.py` never created |
| `script-transform` | 34.9s | 24.9s | 31.6s | **0/3** — `transform.py` never created |
| `storage-blob-put` | 33.4s | 30.1s | 26.4s | **0/3** — 14 failed, 3 passed |
| `mbox-scan` | 45.9s | 22.6s | 22.1s | **0/3** — 13 failed, 3 passed |

### The excision numbers are the control state, unchanged

The failing test counts are **identical to the untouched control**:

```
control (no agent):  storage-blob-put  14 failed, 3 passed
after the agent ran: storage-blob-put  14 failed, 3 passed
control (no agent):  mbox-scan         13 failed, 3 passed
after the agent ran: mbox-scan         13 failed, 3 passed
```

The agent left the repository exactly as excised. Not a wrong implementation —
**no implementation**.

### It does call tools now, and that is the change

A failing `storage-blob-put` transcript: **7 steps, 6 `tool_use` events**, one
text block. Turn counts across the 12 trials ranged 1–17. So the model reads
files, navigates the repository, and produces no working change.

That is a **different failure** from the discarded 2026-09-01 preliminary run,
where the transcripts showed **zero tool calls** — the model emitted correct
code in a markdown fence and told the reader to save it. Between the two runs
Ollama went 0.32.15 → 0.33.2 and the client config was corrected. Tool-calling
now happens; task completion still does not.

On the two script tasks the older failure persists exactly: no file is created.

### Reading

**This model is not a coding agent on this hardware.** It passes 2 of 3 smoke
prompts — which read the answer out of the reply — and completes none of 12
real tasks, where the answer has to be a file on disk. The gap between those
two is the whole finding, and no leaderboard measures it.

The smoke gate's `fib` failed at `stop_reason=max_tokens (spent the budget
thinking)` on both runs and both Ollama versions, so it is not a version
artifact (#83).

**What this does not say.** It does not say a 12 GiB card cannot host a coding
agent. `gemma4:12b-it` is a dense 12B, and the untested candidate for this tier
is the MoE path — `ornith-1.5:35b`, 22 GB with 3B active, streamed with
`--n-cpu-moe` against 32 GB of system RAM. That model already scores 21/21 on
the Mac, so it is the only candidate where the model is proven and only the
hardware is in question (#20, #79).

---

## 2026-09-02: `ornith-1.5:9b` under OpenCode — **11/12**

The first model to pass anything on this machine, and the first result here
measured with a **verified** context window.

| task | trial 1 | trial 2 | trial 3 | median |
|---|---|---|---|---|
| `storage-blob-put` | PASS 75.9s | PASS 198.9s | PASS 115.0s | **115.0s** |
| `mbox-scan` | PASS 152.2s | PASS 101.2s | **FAIL 451.4s** | 152.2s |
| `script-reverse` | PASS 8.9s | PASS 8.5s | PASS 8.8s | **8.8s** |
| `script-transform` | PASS 45.0s | PASS 11.9s | PASS 20.9s | **20.9s** |

`summarize.py` reports `12/13 passed, median 75.9s, median turns 10` against
gemma4's `0/12, median 25.6s, median turns 4`. Thirteen rows for twelve trials:
a `storage-blob-put` trial 1 at 93.5s survives from an earlier run that was
killed partway through, at the same commit and the same served context. It is a
real trial, not a duplicate measurement, so it is kept rather than excluded.

### The context window was the whole story

The first attempt at this cell ran in Ollama's default 4,096-token window:
`storage-blob-put` thrashed at 97% GPU for **1566.9s** and failed. The same
task, model and client at **32,768** passed in **93.5s** — 16.8x faster, and a
pass instead of a failure. That row is kept, marked excluded, with its reason.

`context_tokens = 131072` was copied from the Mac's backends and was never
reachable here: 4 KV heads x (256+256) x 32 layers is 128 KiB/token, so 131072
needs **16 GiB of KV cache** against a 12 GiB card. The backend now points at
`ornith-1.5-9b-32k`, built with an explicit `num_ctx`, verified at 6.11 GiB
resident with `size_vram == size` — fully on the GPU, no offload.

**This tier runs at 32k where the Mac runs at 131072.** That is a hardware
limit, not a defect, and it means the two are not directly comparable on
context-sensitive tasks.

### The one failure is the interesting row

`mbox-scan` trial 3: **19 turns, 27,889 output tokens, 451.4s** — three times
the wall time of its own passing runs. pytest reported `1 error in 0.11s`, not
`16 failed`: the module never imported. The quality gates went backwards
(ruff 1→3, mypy 18→22). It did not touch the tests and did not escape the
workspace.

That is a different failure from gemma4's. Gemma4 produced correct code in a
markdown fence and never called a tool. Ornith engaged with the repository,
worked hard, and shipped something that would not load.

### Reading

**A 9B dense model is a working coding agent on 12 GiB** — the thing the
gemma4 rows appeared to rule out, and could not, because they were measured
through a 4k window.

**The task set does not discriminate cleanly at 9B.** #79 hoped this tier would
reveal a difficulty ordering. Eleven of twelve passed; the single failure is
too thin to rank on. That strengthens #4 rather than resolving it.

**Wall time is noisy here.** `storage-blob-put` spans 75.9-198.9s, a 2.6x range
within one cell; `script-transform` spans 11.9-45.0s, 3.8x. Only
`script-reverse` is stable at 8.5-8.9s. #23 budgets +/-28% for a 3-trial
median, so no wall-time claim from this run survives its own variance. More
trials, not fewer.

### Provenance

Ollama 0.33.2, OpenCode 1.18.26, harness `230eeeb`, target `gmail-archive`
@ `56e55cc`. Sampling recorded as `{'num_ctx': '32768', 'sampling_source':
'modelfile'}` — where the gemma4 run recorded `engine defaults (unrecorded)`.
Setting the parameter explicitly is what made the server identity recordable
at all (#78).
