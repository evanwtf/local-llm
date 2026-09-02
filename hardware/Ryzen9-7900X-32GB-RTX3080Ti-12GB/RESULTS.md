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
