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

> ### ⚠️ SUPERSEDED — these rows were measured in a 4,096-token window
>
> They record `context_tokens: 131072`. The server served **4096** — Ollama's
> default, never overridden. **All twelve are now marked excluded.** Re-run at
> a verified 32,768 the same day, the same backend scored **3/12**, so the
> 0/12 below measured our own truncation. The reading is retained as a record
> of what was concluded and why it was wrong; do not cite its numbers.
> Guard added in `230eeeb`. Corrected result: "gemma4 at 32k" below.


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


---

## 2026-09-02: `gemma4:12b-it` at a verified 32k — **3/12**, not 0/12

The same backend, same tasks, same day, with the context window fixed.

| task | trial 1 | trial 2 | trial 3 | verdict |
|---|---|---|---|---|
| `storage-blob-put` | FAIL 30.6s | FAIL 215.4s | FAIL 73.4s | 0/3 |
| `mbox-scan` | FAIL 284.9s | FAIL 193.7s | FAIL 289.7s | 0/3 |
| `script-reverse` | **PASS 21.2s** | **PASS 49.0s** | **PASS 26.3s** | **3/3** |
| `script-transform` | FAIL 32.8s | FAIL 27.1s | FAIL 25.9s | 0/3 |

`summarize.py`: `dtgemma412b 3/12 passed, median 40.9s, median turns 3`
against `dtornith159b 12/13 passed, median 75.9s, median turns 10`.

### The model was never the problem the old rows described

The superseded section concluded "this model is not a coding agent on this
hardware" and "completes none of 12 real tasks". Both are false. It completes
`script-reverse` **3 times out of 3**, reproducibly.

Three distinct failure mechanisms replace the single one recorded before:

* **`mbox-scan` — control unchanged.** 13 failed / 3 passed before and after,
  three times, after 194-290s of work. The file is never touched.
* **`script-transform` — no file.** `transform.py` is never created. This is
  the only place the old "code in a markdown fence" behaviour survives.
* **`storage-blob-put` — it tries.** Trial 1 and 2 broke the import outright
  (`1 error`, module unloadable). **Trial 3 reached `2 failed, 15 passed`** --
  two tests short of solving the hardest task in the set, from a control state
  of 14 failed / 3 passed.

That last row is the one to remember. At 4k this cell left the file untouched.
At 32k it gets within two tests of green.

### What this says about the task set (#79)

**The set discriminates between models at this tier, not within one.** #79
hoped a 9B would fail some tasks and pass others, giving the project its first
difficulty ordering. Ornith did not provide it -- 11/12, still saturated.
Gemma4 does, and cleanly: the same task passes or fails deterministically
across all three trials, never mixed.

The ordering that falls out, easiest first:

1. `script-reverse` -- no repository to navigate. Both models, 3/3.
2. `script-transform` -- a file must be created from nothing. Ornith 3/3,
   gemma4 0/3.
3. `mbox-scan`, `storage-blob-put` -- find and modify code in an existing
   tree. Ornith 5/7, gemma4 0/6.

**It also makes the case for #4 better than any previous run.** The oracle is
binary, so `2 failed, 15 passed` and `transform.py was never created` are both
"FAIL" -- identical in the table, opposite in meaning. A graded signal would
have separated a near-miss from a no-op without anyone reading a transcript.

### Confound: OpenCode updated mid-session

**Ornith ran on OpenCode 1.18.26; this run on 1.18.27.** The client
auto-updated between the two batches, unasked. Both rows record their own
version, so the comparison is not silently wrong -- but it is not a clean
isolation either, and the head-to-head above crosses a client version.

Neither model's result looks version-sensitive: gemma4's `script-reverse`
passes and `mbox-scan` leaves the control untouched on both `.26` and `.27`.
Still, ornith should be re-run on `.27` before the two are ranked against each
other. #55's rule applies -- date and version every claim.

### Provenance

Ollama 0.33.2, OpenCode **1.18.27**, harness `c8794f6`, target `gmail-archive`
@ `56e55cc`, model `gemma4-12b-it-32768` (Modelfile `num_ctx 32768`; 7.84 GiB
resident, fully on GPU -- gemma4's sliding-window attention makes the window
nearly free, 7.80 GiB at 8k against 7.84 GiB at 32k).

The `fib` smoke probe still fails at `stop_reason=max_tokens (spent the budget
thinking)`. It now has done so across two Ollama versions **and** an 8x larger
context, so it is neither a version nor a truncation artifact (#83).


---

## 2026-09-02: ornith re-run on OpenCode 1.18.27 — **9/12**, down from 11/12

Run to close the client-version confound recorded above. It did not close it.
It found one.

| task | trial 1 | trial 2 | trial 3 | 1.18.27 | 1.18.26 |
|---|---|---|---|---|---|
| `storage-blob-put` | PASS 165.7s | PASS 133.0s | **FAIL 151.9s** | 2/3 | 4/4 |
| `mbox-scan` | **FAIL 282.8s** | PASS 100.9s | PASS 534.4s | 2/3 | 2/3 |
| `script-reverse` | PASS 7.0s | **FAIL 8.3s** | PASS 9.2s | 2/3 | 3/3 |
| `script-transform` | PASS 10.6s | PASS 12.2s | PASS 12.0s | 3/3 | 3/3 |

### The measurable difference: turns on repository tasks doubled

Restricted to the two tasks that require finding and modifying code in a tree:

| client | median turns | values |
|---|---|---|
| 1.18.26 | **12.0** | 9, 10, 10, 12, 19, 24, 36 |
| 1.18.27 | **27.5** | 8, 18, 22, 33, 40, 45 |

OpenCode is the only variable that changed: same model digest
(`ornith-1.5-9b-32k`), same Ollama 0.33.2, same harness, same tasks, same
served 32,768 window, same machine, ninety minutes apart.

**9/12 against 11/12 is not a significant difference at n=12** and this section
does not claim the client caused the failures. The turn inflation is the
finding; the pass-rate drop is within noise.

### Three failures, three unrelated mechanisms

**`script-reverse` trial 2 — the model wrote to the wrong directory.** It
produced a correct script, ran it, and got the right answer:

    write → /tmp/script-reverse-dtornith159b-opencode-2/reverse.py
    bash  → python3 /tmp/script-reverse-.../reverse.py hello  →  olleh

The workspace is `/tmp/agent-bench/<trial>/`. It dropped the `agent-bench/`
segment, then **verified against its own wrong absolute path**, so its
self-check passed and it never noticed. The oracle was correct: nothing was
created in the workspace. The two passing trials wrote to
`/tmp/agent-bench/<trial>/reverse.py` and ran `python3 reverse.py hello`
relative to the cwd.

This is a self-consistent error the model cannot detect, and it is a cousin of
#54 -- the client solving the task and putting the answer somewhere else --
except here the harness passed `--dir` correctly and the model invented the
path anyway.

**`storage-blob-put` trial 3 and `mbox-scan` trial 1 — the `edit` tool bounced.**
Both are dominated by repeated:

    Could not find oldString in the file. It must match exactly,
    including whitespace, indentation, and line endings.

`storage-blob-put` trial 3 spent **40 turns** (the highest in either run) across
13 `edit` and 15 `bash` calls and finished with the control state untouched.
`mbox-scan` trial 1 made partial progress -- 11 failed / 5 passed against a
control of 13 / 3 -- over 33 turns and 10 `edit` calls.

The model also emitted raw tool-call markup as message content in
`storage-blob-put` trial 3, which OpenCode tried to treat as a path:

    NotFound: FileSystem.access (/home/evan/git/gmail-archive
    </parameter></function></tool_call><tool_call><function=bash>...

That is a Hermes/Qwen-style `<tool_call>` block leaking into the text channel
instead of being emitted as a structured call.

### What cannot be checked, and why

**The 1.18.26 transcripts no longer exist.** `--client-log` names files
`<task>-<backend>-<client>-<trial>.stdout.jsonl` with no run, commit or client
version in the name, so the 1.18.27 run overwrote all twelve of its
predecessor's transcripts. There is no way to tell whether the `oldString`
failures are new in 1.18.27 or were always there.

That is the same gap `500491a` closed for logs -- machine, commit and stack in
every filename -- one level down, in the artifact that actually holds the
evidence. Worth fixing before the next paired comparison.

### Provenance

Ollama 0.33.2, OpenCode **1.18.27**, harness `84a85f6`, target `gmail-archive`
@ `56e55cc`, `ornith-1.5-9b-32k` at a verified 32,768 (`llama-server -c 32768`
on the engine command line, 6.11 GiB resident, fully on GPU).

Preflight warned that `llama-server (pid 110844)` held 8.9 GiB and was not
listening. That was gemma4's server mid-unload from the previous batch; by the
first trial only ornith was resident. Not a contended run.


---

## 2026-09-03: `qwen3.5:9b` — **9/12**, and the chart's near-tie is not one

Run to test #113. 0xSero's `local.ai` chart puts Qwen3.5 9B Q4_K_M at ~50%
Intelligence against Gemma-4-12B's ~51% -- a one-point gap, with Qwen faster.
If an Intelligence Index predicted agent success, these two should land
together.

| task | trial 1 | trial 2 | trial 3 | qwen | gemma4 | ornith |
|---|---|---|---|---|---|---|
| `storage-blob-put` | FAIL 80.6s | **PASS 155.8s** | FAIL 130.0s | 1/3 | 0/3 | 6/7 |
| `mbox-scan` | **PASS 97.6s** | FAIL 277.1s | **PASS 84.8s** | 2/3 | 0/3 | 4/6 |
| `script-reverse` | PASS 26.0s | PASS 40.7s | PASS 19.3s | 3/3 | 3/3 | 5/6 |
| `script-transform` | PASS 92.2s | PASS 51.8s | PASS 39.0s | 3/3 | 0/3 | 6/6 |

`summarize.py`:

| backend | passed | median wall | median turns |
|---|---|---|---|
| `dtgemma412b` | **3/12** | 40.9s | 3 |
| `dtqwen359b` | **9/12** | 82.7s | 6 |
| `dtornith159b` | **21/25** | 75.9s | 9 |

(ornith's 25 pools its 1.18.26 and 1.18.27 runs; see the section above.)

### The finding

**A one-point gap on the Intelligence Index is a 3x gap in task completion.**
Gemma-4-12B and Qwen3.5-9B are neighbours on that chart. Here they are 3/12
and 9/12, and the difference is not marginal -- gemma4 never once solved a
task requiring it to modify code in a repository, and qwen solved seven.

Ranked by the chart, the order is gemma4 > qwen, with ornith absent. Ranked by
work completed on this card, it is ornith > qwen >> gemma4. The chart does not
merely mis-rank; the model it omits entirely is the one that wins, and its top
pick is last.

This is the clearest evidence the project has that **an intelligence aggregate
and a single-card task time do not predict whether an agent closes a loop on a
real repository**. Per #59 nothing from a leaderboard enters RECOMMENDATIONS
without our own measurement; this is why.

### Where qwen actually differs from ornith

Not in what it can do -- both clear all four task types -- but in consistency
and speed.

* **`script-reverse`: 26.0 / 40.7 / 19.3s** against ornith's metronomic
  8.5-8.9s. Three to five times slower on the simplest task in the set.
* **`storage-blob-put` is where it loses.** The three trials read
  `14 failed / 3 passed` (control, untouched), `17 passed` (solved), then
  `13 failed / 4 passed` (one test of progress). Untouched, solved, barely
  started -- on identical work.
* **It is fastest where it succeeds.** `mbox-scan` at 84.8s is the quickest
  anything has cleared that task, against ornith's 152.2s median.

### Another near-miss the oracle flattens (#4)

`mbox-scan` trial 2 finished at **`2 failed, 14 passed`** against a control of
`13 failed, 3 passed`. It took the file from 3 passing tests to 14 and missed
green by two. The table records that identically to gemma4 leaving the file
untouched.

That is the second such case in two days -- gemma4's `storage-blob-put` trial 3
reached 15/17 -- now across two different models. A binary oracle cannot tell a
near-miss from a no-op, and both keep appearing at this tier precisely because
these models are near the edge of capable.

### On #83

`qwen3.5:9b` passes the `fib` smoke probe in 21.2s. gemma4 fails it at
`stop_reason=max_tokens (spent the budget thinking)` on every run, both Ollama
versions and both context sizes. So that failure is model-specific, not a
property of the probe or of the 9B tier.

### Provenance

Ollama 0.33.2, OpenCode 1.18.27, harness `7d3455c`, target `gmail-archive`
@ `56e55cc`, model `qwen3.5-9b-32768` (Modelfile `num_ctx 32768`; 6.13 GiB
resident, `size_vram == size`, fully on GPU). Same verified window as
`dtgemma412b` and `dtornith159b`, so the three are directly comparable.

`qwen3.5:9b` was already on disk -- #79 listed it as a candidate on 2026-09-01
and it cost no download.


---

## 2026-09-03: the four remaining #114 candidates — and the oracle is now the bottleneck

Ran `mistral-nemo:12b`, `qwen3.5:9b-q8_0`, `gemma4:e4b-it-q4_K_M` and
`Bonsai-27B-Q1_0`. Seven backends now have data on this card.

| backend | passed | median wall | median turns | resident @ ctx |
|---|---|---|---|---|
| `dtornith159b` | **21/25** | 75.9s | 9 | 6.11 GiB @ 32k |
| `dtqwen359b` | 9/12 | 82.7s | 6 | 6.13 GiB @ 32k |
| `dtqwen359bq8` | 9/12 | 101.2s | 6 | 9.27 GiB @ 32k |
| `dtgemma4e4b` | 6/12 | **17.6s** | 3 | **3.03 GiB** @ 32k |
| `dtbonsai27b` | 5/12 | 61.2s | 4 | 5.66 GiB @ 32k |
| `dtgemma412b` | 3/12 | 40.9s | 3 | 7.84 GiB @ 32k |
| `dtmistralnemo` | **0/12** | 6.8s | 1 | 9.23 GiB @ **16k** |

Per task:

| task | ornith | qwen Q4 | qwen Q8 | E4B | bonsai | gemma 12B | nemo |
|---|---|---|---|---|---|---|---|
| `storage-blob-put` | 6/7 | 1/3 | 2/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| `mbox-scan` | 4/6 | 2/3 | 1/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| `script-reverse` | 5/6 | 3/3 | 3/3 | 3/3 | 2/3 | 3/3 | 0/3 |
| `script-transform` | 6/6 | 3/3 | 3/3 | 3/3 | 3/3 | 0/3 | 0/3 |

**Only ornith solves repository tasks reliably.** Every other backend is 0-2 of
3 on `storage-blob-put` and `mbox-scan`. That is the tier's real ceiling.

### The headline: eleven near-misses, and the oracle cannot see any of them

A binary oracle records "did not touch the file" and "one test short of green"
identically. Across the seven backends, **eleven failed trials fixed most of
the suite**, eight of them within three tests:

| backend | task | result | control |
|---|---|---|---|
| `dtqwen359bq8` | `mbox-scan` t2 | **1 failed / 15 passed** | 13/3 |
| `dtgemma412b` | `storage-blob-put` t3 | 2 failed / 15 passed | 14/3 |
| `dtqwen359bq8` | `storage-blob-put` t2 | 2 failed / 15 passed | 14/3 |
| `dtgemma4e4b` | `storage-blob-put` t1 | 2 failed / 15 passed | 14/3 |
| `dtqwen359b` | `mbox-scan` t2 | 2 failed / 14 passed | 13/3 |
| `dtqwen359bq8` | `mbox-scan` t3 | 2 failed / 14 passed | 13/3 |
| `dtbonsai27b` | `storage-blob-put` t2 | 3 failed / 14 passed | 14/3 |
| `dtgemma4e4b` | `mbox-scan` t2 | 3 failed / 13 passed | 13/3 |

**#4 is no longer a hypothesis about the oracle. It is the dominant
measurement artifact at this tier.** Six of seven backends produced at least
one near-miss; `dtmistralnemo` is the only one that never came close, and it
is the only one that never called a tool.

### Q8 vs Q4: identical score, different model

`qwen3.5:9b` at Q8_0 and Q4_K_M both score **9/12**. The metric says they are
the same model. The evidence says otherwise:

* **Q4's failures include not engaging.** One `storage-blob-put` trial left the
  control untouched (14/3); another reached only 13/4.
* **Q8's three failures are all near-misses** -- 15/17, 15/16, 14/16. It always
  engages and nearly finishes.
* **Q8 costs about 2x wall time** (median 101.2s against 82.7s; smoke probes
  ran 12-44s against 7-21s), for 3.14 GiB more resident memory.

Two quantisations of the same weights, indistinguishable on the metric,
plainly different in the evidence. This is the cleanest #4 case available.

### `gemma4:e4b` beats `gemma4:12b`, at 39% of the memory

**6/12 against 3/12**, 3.03 GiB resident against 7.84 GiB, and a median wall of
17.6s against 40.9s. The entire difference is `script-transform`, which the 12B
never once completed and E4B passed 3/3 in 13.7-19.7s.

E4B is also the most consistent backend measured here on the tasks it can do:
`script-reverse` at 10.2 / 10.3 / 10.4s, tighter than ornith.

Its failure mode on repository tasks is bimodal rather than graded -- trials
either come within two tests or never touch the file, with nothing in between.

**Footprint note.** E4B is 9.6 GB on disk but **3.03 GiB resident**. It is a
MatFormer-style nested model, so the download size and the loaded size are
different questions. #114's ~4 GB estimate was right about memory and wrong
about download.

### `Bonsai-27B` at Q1_0: the sub-1-bit class works, and is not competitive

**5/12.** A 27B at roughly one bit per weight, 3.54 GiB on disk, **5.66 GiB
resident at 32k**, fully on GPU. #114's "a 27B in 12 GB" claim holds on memory.

More interesting: **Q1_0 is coherent.** It passes two of three smoke probes and
completes `script-transform` 3/3 and `script-reverse` 2/3. Its
`storage-blob-put` trial 2 reached 3 failed / 14 passed -- eleven tests fixed
from the control.

Three caveats for anyone reading the claim:

1. **`general.architecture` is `qwen35`.** Bonsai is Qwen lineage and does
   **not** satisfy #16's non-Qwen requirement.
2. **The 128K context claim does not survive arithmetic here.** 24 heads /
   4 KV, key+value 256, 64 blocks is 256 KiB/token, so 128K needs ~32 GiB of
   KV at f16. It fits 32k in 5.66 GiB; it does not fit 128K on 12 GiB without
   KV quantisation.
3. **It found a new failure mode.** `mbox-scan` trial 1 wrote code that made
   the test run consume 8.4 GiB, and #82's memory ceiling killed the oracle.
   Peak RSS for that trial was 27.0 GiB against 30.5 GiB of system memory.
   Without that guard the trial would have taken the machine and the batch.
   First time the #82 cap has fired here, and it paid for itself.

### `mistral-nemo:12b`: 0/12, zero tool calls

It never calls a tool. It restates the task in prose and stops:

> "Based on your instructions, I understand that you want me to implement the
> `BlobStore.put` method in the `storage.py` file..."

Every trial ended in 2.7-16.9s, median 1 turn. `Mistral-Nemo-Instruct-2407`
predates the tool-calling training these tasks require; `"tool_call": true` in
the client config is an assertion, not a capability.

**This is the answer for #16.** The non-Qwen candidate already on disk cannot
drive an agent loop at all. Combined with bonsai being Qwen lineage after all,
this tier currently has **no working non-Qwen backend**.

It also cannot reach 32k: 12.26 GiB at 32768 and it spills (10.15 GiB
resident); 24576 spills too. It ran at **16,384**, the largest that fits at
9.23 GiB. Its rows are not comparable to the others on context-sensitive work
-- though at 1 median turn, context was not what stopped it.

### On #83

Three models now fail a smoke probe at `stop_reason=max_tokens (spent the
budget thinking)`, and the affected probe differs by model:

* `gemma4:12b-it` fails `fib` -- every run, both Ollama versions, both context
  sizes
* `Bonsai-27B` fails `mergesorted`
* `gemma4:e4b`, both qwens, ornith and mistral-nemo pass all three

So it is model-specific, not a property of the probe, the tier, or the gemma4
family -- E4B passes the probe its 12B sibling always fails.

**A wording bug in the gate.** It logs `did not finish ['fib'] in 300s` when
the probe returned in 47.9s by exhausting its token budget. That misdescribes
every instance of this failure as a timeout.

### Provenance

Ollama 0.33.2, OpenCode 1.18.27, harness `10d8573`, target `gmail-archive`
@ `56e55cc`. All backends at a verified 32,768 window except `dtmistralnemo`
at 16,384 (the card's limit for it). Each backend ran as its own `run.py`
invocation with the previous model stopped between arms (#112).

Bonsai resolved to
[`prism-ml/Bonsai-27B-gguf`](https://huggingface.co/prism-ml/Bonsai-27B-gguf)
-> `Bonsai-27B-Q1_0.gguf`, 3.54 GiB, recorded in `~/models/bonsai-27b/SOURCE.txt`
so #111 does not repeat.
