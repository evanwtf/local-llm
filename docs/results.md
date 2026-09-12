# The measured results, and how much to trust them

**Split out of `RECOMMENDATIONS.md` on 2026-09-08 ([#232](https://github.com/evanwtf/local-llm/issues/232)).**
The tables between the `BEGIN GENERATED` / `END GENERATED` markers below are
written by `benchmarks/agent/splice_tables.py` from `results.jsonl` — **do not
edit them by hand**, run the script. Everything around them is prose that
moved here unchanged.

Start at [`RECOMMENDATIONS.md`](../RECOMMENDATIONS.md) for what to run. This
file is what the recommendation rests on: what the benchmark measures, every
backend's numbers, what the figures cannot say, and how to reproduce them.

---

## What is being measured

Every number below comes from a real coding agent doing a real task, timed end
to end. There are two kinds of task, and they measure different things.

**Excision tasks.** The agent gets a checkout of a real Python repository
([`gmail-archive`](https://github.com/evanwtf/gmail-archive), pinned at one
commit) in which **one function body has been deleted** and replaced with
`raise NotImplementedError`. The repository's own test suite is the only oracle.
No test is shown to the agent as a target, and editing tests is forbidden and
checked afterwards. This measures whether a stack can find its way around code
it has never seen.

| task | what the agent is asked to do |
|---|---|
| [`mbox-strip-envelope`](../benchmarks/agent/PROMPTS.md#mbox-strip-envelope) | implement `strip_envelope` in an mbox parser |
| [`parser-mbox-quoting`](../benchmarks/agent/PROMPTS.md#parser-mbox-quoting) | implement `unquote_mbox`, which must round-trip with `requote_mbox` |
| [`storage-blob-put`](../benchmarks/agent/PROMPTS.md#storage-blob-put) | implement `BlobStore.put` |
| [`parser-date`](../benchmarks/agent/PROMPTS.md#parser-date) | implement `_date`, an email date parser |
| [`mbox-scan`](../benchmarks/agent/PROMPTS.md#mbox-scan) | implement `scan`, which walks an mbox file |

**Script tasks.** The agent starts in an **empty directory** and must produce a
working command-line program — the right filename, reading `argv`, printing to
stdout. Trivial logic, real boilerplate, and no repository to navigate.

| task | what the agent is asked to do |
|---|---|
| [`script-reverse`](../benchmarks/agent/PROMPTS.md#script-reverse) | write `reverse.py`: take a string, print it reversed |
| [`script-transform`](../benchmarks/agent/PROMPTS.md#script-transform) | write `transform.py`: `--input` plus `--reverse`, `--sort` and `--sha256`, applied in a fixed order whatever order the flags arrive in |

**The exact prompt for every task is published** in
[`benchmarks/agent/PROMPTS.md`](../benchmarks/agent/PROMPTS.md), generated from the
file the harness actually reads, with a test that fails if the two drift. If a
number here looks surprising, read the prompt that produced it.

**Why both kinds.** The script tasks have almost no variance (1.0–2.1x between
the best and worst run of the same task) because there is no codebase to get
lost in, which makes them the fair way to compare stacks. The excision tasks are
noisier but closer to real work. A stack that does well on one and badly on the
other is telling you something.

## Measured results

<!-- BEGIN GENERATED -->

### Cortex-X925-128GB-GB10

*Generated from `hardware/Cortex-X925-128GB-GB10/results.jsonl` — 381 rows, sha256 5c2bc6b3f825.*

#### Every stack measured under OpenCode

**The three timing columns count only trials that passed.** A trial that dies early is quick, so counting failures would reward a stack for failing fast and lift it up a table sorted by median. Read the `passed` column first.

| stack | passed | median | worst | spread |
|---|---|---|---|---|
| qwen38fnq3nothinkdgx | 60/60 | 60s | 497s | 14.1x |
| qwen38fnq3dgx | 30/30 | 127s | 315s | 6.6x |
| qwen36nvfp4nothinkdgx | 60/60 | 136s | 268s | 5.2x |
| qwen36codinggguf | 29/30 | 162s | 221s | 3.2x |
| qwen36nvfp4dgx | 30/30 | 179s | 545s | 6.5x |
| qwen36nvfp4v1dgx | 29/30 | 190s | 832s | 12.3x |
| nemotroncascade2 | 2/10 | 197s | 197s | 1.0x |
| ds4dgx | 30/30 | 234s | 364s | 3.6x |
| qwen36nvfp4specdgx | 9/13 | 236s | 984s | 21.9x |
| qwen36bf16dgx | 30/30 | 432s | 1335s | 6.7x |
| nemotron3nano | 8/10 | 580s | 1212s | 8.5x |
| nemotron33 | 1/10 | — | — | — |

Excision tasks only; `script-*` excluded because they are a different class. **Spread is worst / best on the same task**, and it is the column most people forget to ask for.

#### How fast each stack actually serves tokens

| stack | seconds per 1k output tokens |
|---|---|
| nemotron33 | 15s |
| nemotroncascade2 | 17s |
| nemotron3nano | 19s |
| qwen38fnq3dgx | 55s |
| qwen38fnq3nothinkdgx | 68s |
| qwen36codinggguf | 70s |
| ds4dgx | 88s |
| qwen36nvfp4nothinkdgx | 105s |
| qwen36nvfp4dgx | 147s |
| qwen36nvfp4specdgx | 147s |
| qwen36nvfp4v1dgx | 160s |
| qwen36bf16dgx | 409s |

### MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A

*Generated from `hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/results.jsonl` — 2565 rows, sha256 f01b2d48ac3a.*

#### Every stack measured under OpenCode

**The three timing columns count only trials that passed.** A trial that dies early is quick, so counting failures would reward a stack for failing fast and lift it up a table sorted by median. Read the `passed` column first.

| stack | passed | median | worst | spread |
|---|---|---|---|---|
| ornith15 | 21/21 | 44s | 93s | 5.9x |
| qwen38fnmlxserve | 238/241 | 52s | 1377s | 64.7x |
| qwen38fnmlxserve-git | 74/75 | 52s | 402s | 18.5x |
| qwen38fnds4kimat | 361/361 | 91s | 775s | 27.0x |
| Qwen3.8-Flash-Next Q3 - llama.cpp | 75/75 | 106s | 356s | 8.3x |
| DeepSeek-V4-Flash - ds4 (Anthropic wire) | 18/18 | 110s | 221s | 4.3x |
| qwen38fnq3reap | 21/21 | 110s | 261s | 6.8x |
| DeepSeek-V4-Flash - ds4 | 30/30 | 115s | 230s | 4.3x |
| Qwen3.8-Flash-Next Q3 - LM Studio | 21/21 | 122s | 261s | 4.2x |
| gemma426 | 11/11 | 150s | 160s | 1.7x |
| qwen36 | 11/12 | 159s | 352s | 3.6x |
| qwen38fnds4shim | 228/262 | 160s | 792s | 20.5x |
| qwen38fnds4greedy | 37/40 | 163s | 806s | 14.8x |
| Qwen3.6-27B-coding - Ollama | 24/24 | 167s | 700s | 12.6x |
| qwen38fnds4mtp7shim | 72/127 | 177s | 638s | 11.4x |
| qwen38fnds4mtp7greedy | 48/60 | 239s | 1003s | 13.8x |
| qwen | 12/12 | 247s | 406s | 4.0x |
| GLM-5.3-Flash - ds4 | 22/24 | 369s | 1227s | 18.0x |
| gemma4 | 12/12 | 383s | 1316s | 4.8x |

**Rows here were not all taken under one client.** qwen38fnq3reap under 1.18.26; qwen38fnds4kimat, qwen38fnds4mtp7shim, qwen38fnds4shim under 1.18.27; qwen38fnds4greedy, qwen38fnds4kimat, qwen38fnds4mtp7greedy, qwen38fnds4mtp7shim, qwen38fnds4shim, qwen38fnmlxserve, qwen38fnmlxserve-git, Qwen3.8-Flash-Next Q3 - llama.cpp under 1.18.29; qwen38fnds4kimat under 1.18.30; the rest under 1.18.25. A comparison across that split also compares the client ([#137](https://github.com/evanwtf/local-llm/issues/137)). No (backend, task) cell here holds both versions, so the client's own effect is unmeasured on this machine — there is nothing to correct for, only a boundary to name. Measured under more than one: qwen38fnds4kimat (1.18.27, 1.18.29, 1.18.30); qwen38fnds4mtp7shim (1.18.27, 1.18.29); qwen38fnds4shim (1.18.27, 1.18.29); Qwen3.8-Flash-Next Q3 - llama.cpp (1.18.25, 1.18.29).

**The `qwen38fnmlxserve`, `qwen38fnmlxserve-git` rows are PLD-on.** mlx-serve turns on Prompt Lookup Decoding by default, and every mlx-serve row here was taken with it on; the pack ships no MTP head or drafter, so PLD is the draft source ([#262](https://github.com/evanwtf/local-llm/issues/262)). That is the engine's own default — "what you get when you install it," which is what this project measures — but the speculation was never recorded, so these rows are not a no-speculation baseline against the ds4 arms whose MTP state we set explicitly.

Excision tasks only; `script-*` excluded because they are a different class. **Spread is worst / best on the same task**, and it is the column most people forget to ask for.

#### Same weights, two engines

| task | what it asks for | llama.cpp | LM Studio |
|---|---|---|---|
| [`mbox-scan`](../benchmarks/agent/PROMPTS.md#mbox-scan) | implement `scan`, which walks an mbox file | 106s | 140s |
| [`mbox-strip-envelope`](../benchmarks/agent/PROMPTS.md#mbox-strip-envelope) | implement `strip_envelope` in an mbox parser | 53s | 94s |
| [`parser-date`](../benchmarks/agent/PROMPTS.md#parser-date) | implement `_date`, an email date parser | 188s | 238s |
| [`parser-mbox-quoting`](../benchmarks/agent/PROMPTS.md#parser-mbox-quoting) | implement `unquote_mbox`, round-tripping with `requote_mbox` | 86s | 93s |
| [`script-reverse`](../benchmarks/agent/PROMPTS.md#script-reverse) | write `reverse.py` from nothing: read argv, print reversed | 41s | 57s |
| [`script-transform`](../benchmarks/agent/PROMPTS.md#script-transform) | write `transform.py`: `--input` plus three composable flags | 49s | 70s |
| [`storage-blob-put`](../benchmarks/agent/PROMPTS.md#storage-blob-put) | implement `BlobStore.put` | 95s | 124s |

#### How fast each stack actually serves tokens

| stack | seconds per 1k output tokens |
|---|---|
| ornith15 | 21s |
| gemma426 | 21s |
| qwen | 31s |
| qwen38fnds4kimat | 33s |
| qwen38fnq3reap | 38s |
| qwen38fnmlxserve-git | 39s |
| qwen38fnmlxserve | 41s |
| Qwen3.8-Flash-Next Q3 - llama.cpp | 42s |
| qwen36 | 50s |
| DeepSeek-V4-Flash - ds4 (Anthropic wire) | 54s |
| GLM-5.3-Flash - ds4 | 55s |
| Qwen3.6-27B-coding - Ollama | 69s |
| DeepSeek-V4-Flash - ds4 | 71s |
| qwen38fnds4shim | 78s |
| qwen38fnds4greedy | 82s |
| gemma4 | 84s |
| qwen38fnds4mtp7shim | 84s |
| qwen38fnds4mtp7greedy | 106s |
| Qwen3.8-Flash-Next Q3 - LM Studio | 115s |

**Rows here were not all taken under one client.** qwen38fnq3reap under 1.18.26; qwen38fnds4kimat, qwen38fnds4mtp7shim, qwen38fnds4shim under 1.18.27; qwen38fnds4greedy, qwen38fnds4kimat, qwen38fnds4mtp7greedy, qwen38fnds4mtp7shim, qwen38fnds4shim, qwen38fnmlxserve, qwen38fnmlxserve-git, Qwen3.8-Flash-Next Q3 - llama.cpp under 1.18.29; qwen38fnds4kimat under 1.18.30; the rest under 1.18.25. A comparison across that split also compares the client ([#137](https://github.com/evanwtf/local-llm/issues/137)). No (backend, task) cell here holds both versions, so the client's own effect is unmeasured on this machine — there is nothing to correct for, only a boundary to name. Measured under more than one: qwen38fnds4kimat (1.18.27, 1.18.29, 1.18.30); qwen38fnds4mtp7shim (1.18.27, 1.18.29); qwen38fnds4shim (1.18.27, 1.18.29); Qwen3.8-Flash-Next Q3 - llama.cpp (1.18.25, 1.18.29).

### Ryzen9-7900X-32GB-RTX3080Ti-12GB

*Generated from `hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/results.jsonl` — 224 rows, sha256 604807a2bef1.*

#### Every stack measured under OpenCode

**The three timing columns count only trials that passed.** A trial that dies early is quick, so counting failures would reward a stack for failing fast and lift it up a table sorted by median. Read the `passed` column first.

| stack | passed | median | worst | spread |
|---|---|---|---|---|
| dtternarybonsai27b | 15/20 | 87s | 163s | 5.2x |
| dtqwen359b | 9/12 | 98s | 156s | 1.8x |
| dtbonsai27bllamacpp | 26/38 | 117s | 335s | 6.0x |
| dtqwen359bq8 | 9/12 | 119s | 119s | 1.4x |
| dtornith159b | 21/25 | 124s | 534s | 7.0x |
| dtbonsai27b | 19/38 | 185s | 238s | 1.8x |
| dtsparkx254b | 13/24 | 207s | 207s | 1.0x |
| dtgemma412b | 3/12 | — | — | — |
| dtmistralnemo | 0/12 | — | — | — |
| dtgemma4e4b | 6/12 | — | — | — |

**Rows here were not all taken under one client.** dtbonsai27b, dtbonsai27bllamacpp, dtsparkx254b, dtternarybonsai27b under 1.18.27; the rest under unrecorded. A comparison across that split also compares the client ([#137](https://github.com/evanwtf/local-llm/issues/137)). No (backend, task) cell here holds both versions, so the client's own effect is unmeasured on this machine — there is nothing to correct for, only a boundary to name. Measured under more than one: dtbonsai27b (1.18.27, unrecorded).

Excision tasks only; `script-*` excluded because they are a different class. **Spread is worst / best on the same task**, and it is the column most people forget to ask for.

#### How fast each stack actually serves tokens

| stack | seconds per 1k output tokens |
|---|---|
| dtgemma4e4b | 11s |
| dtornith159b | 13s |
| dtsparkx254b | 14s |
| dtgemma412b | 22s |
| dtbonsai27bllamacpp | 24s |
| dtternarybonsai27b | 33s |
| dtmistralnemo | 36s |
| dtbonsai27b | 42s |
| dtqwen359b | 62s |
| dtqwen359bq8 | 89s |

**Rows here were not all taken under one client.** dtbonsai27b, dtbonsai27bllamacpp, dtsparkx254b, dtternarybonsai27b under 1.18.27; the rest under unrecorded. A comparison across that split also compares the client ([#137](https://github.com/evanwtf/local-llm/issues/137)). No (backend, task) cell here holds both versions, so the client's own effect is unmeasured on this machine — there is nothing to correct for, only a boundary to name. Measured under more than one: dtbonsai27b (1.18.27, unrecorded).

<!-- END GENERATED -->

**One batch above is unbalanced, and the table does not say so.** The rows
tagged `greedy-mtp-ab-py` were taken while the Python port of the driver was
being validated, and the run was stopped before the control arm finished:
**15 trials on `qwen38fnds4mtp7greedy` against 10 valid on
`qwen38fnds4greedy`** (13 taken, 3 excluded -- they were written against a
server the driver had already stopped,
[#268](https://github.com/evanwtf/local-llm/issues/268)). Every surviving row
is a real trial and belongs in the per-backend totals, which is why they are
here. But those two backends are the two arms of an A/B, they sit next to each
other in a table sorted by median, and **their gap in this table is not that
A/B's result** -- the arms have different n and the treatment arm alone
carries a full sweep. Read the effect from `greedy-mtp-ab-paired`, which ran
30 against 30.


**Three conditions apply to the `qwen38fnds4*` rows, and a reproduction that
misses them will not get these numbers.** They are set out at the end of this
file rather than here because they are long, but they are not footnotes — each
one changes what you would have to build to see the same result:

* [The shim's scaffolding strip is load-bearing](#the-shims-scaffolding-strip-is-load-bearing-not-tidying)
  — worth **23 points of pass rate** where it has been measured. Proxy the shim
  without it and trials end with no tool call and no code, which reads as the
  model failing rather than the plumbing.
* [Three rows cannot be reproduced from upstream sources](#three-rows-here-cannot-be-reproduced-from-upstream-sources)
  — they need a PLE sidecar that exists only on ivanfioravanti's forks.
  `antirez/ds4` main will not load these weights at all.
* [The build behind `qwen38fnds4shim` has been withdrawn](#the-ds4-shim-rows-were-measured-on-a-build-its-author-has-withdrawn)
  — the Q4_0 file it measured is no longer offered on Hugging Face.

**Reading the spread column.** It is the worst run divided by the best run *on
the same task*. Anything near 4x is ordinary — these models sample at
temperature and sometimes write four times as much code to solve the same
problem. The two at 12x and 18x are different in kind: on one task, GLM-5.3 took
**99 seconds once and 1,227 seconds another time**. It got the right answer both
times.

**That variance is the agent, not the machine.** We checked. Across 113 trials,
wall time correlates with output tokens at **0.97** and with turns taken at
0.77, while seconds-per-turn — the part the hardware controls — varies only
1.18x. A slow run is one where the agent wrote more and took more turns, not one
where the computer was busy.

**Which is why GLM-5.3-Flash is not in the top three.** It serves tokens faster
than almost anything here (47s per 1k) and it passes 16/18. But you cannot plan
around it: a task that usually takes 90 seconds will occasionally take twenty
minutes.

---

## How much should you trust this?

**The pass rates are strong; the speed rankings are weaker than they look.**

- Three trials pins a task's median to about **±28%**, so two stacks need to
  differ by roughly 56% before the difference is real. Qwen3.8-Flash-Next
  (90s) and DeepSeek-V4-Flash (115s) are **not** reliably distinguishable.
  Qwen3.6-27B-coding (167s) is.
- A perfect run of 21/21 supports "above 85%" at 95% confidence, not "100%".
  Nothing here has run the ~35 consecutive trials a >90% claim needs.
- Every stack was measured on **one machine**, on **one repository**, on six
  tasks. Your code is not our code.

**What we are confident about**: all three stacks work, none of them is a trap,
and **the client you drive them with matters more than the model you pick** —
see below.

## The client matters more than the model

Three clients, same server, same model, same session, same task, interleaved so
none of them got a warmer server. `script-transform` on Qwen3.8-Flash-Next Q3:

| client | median | slowest run | prompt sent | turns |
|---|---|---|---|---|
| **Aider** | **11.1s** | 11.9s | **737 tokens** | 1 |
| **OpenCode** | 39.5s | 55.3s | 11,721 tokens | 5 |
| **Claude Code** | 189.6s | 339.4s | **85,413 tokens** | 3 |

Aider used **6%** of Claude Code's time. All nine runs produced correct output.

**The cause is how much prompt the client sends.** This task starts in an empty
directory and writes one file — there is no repository to read. Claude Code
still sends 85,000 tokens of its own scaffolding, and the server prefills that
on every turn. Output volume does not explain the gap: Claude Code wrote 1,524
tokens against Aider's 395, under four times as many, for seventeen times the
clock.

This is why the gap grows with model size. Prefill cost scales with the model,
so an oversized prompt is nearly free on a 31 GB model and expensive on a 90 GB
one.

### So should you use Aider?

**For a self-contained script, yes — it is dramatically cheaper.** For changing
code inside an existing repository, no:

| client | one-file script tasks | tasks inside a repository |
|---|---|---|
| OpenCode | 15/15 | **91/93** |
| Aider | 15/15 | **22/34** |

Aider's speed comes partly from doing less — one turn, no exploration. That is
exactly right for "write me this script" and not enough for "find where this
behavior lives and change it". **The recommendation stays OpenCode**, with
Aider worth reaching for on small self-contained jobs.

**Claude Code is the reference point, not a recommendation here.** It is
proprietary, so it cannot be part of a fallback that survives a vendor, and on
these measurements it is both the slowest and the least consistent (152.5s,
339.4s, 189.6s on the same task).

## Reproducing this

```sh
git clone https://github.com/evanwtf/local-llm && cd local-llm
uv sync
uv run python benchmarks/agent/preflight.py        # checks servers, versions, config
uv run python benchmarks/agent/run.py --client opencode --backend qwen38fnq3 --trials 3
```

The tables above are generated from `benchmarks/agent/results.jsonl` by
`gen_tables.py` and spliced in by `splice_tables.py` — they are never typed by
hand, and a test fails if this file drifts from the data.

### The ds4 shim rows were measured on a build its author has withdrawn

`qwen38fnds4shim`'s 135 trials ran against
`Qwen3.8-Flash-Next-Q40RoutedExperts-…gguf` — **Q4_0 routed experts**. On
2026-09-04 that file was replaced on Hugging Face by a Q4_K imatrix build, its
author describing the Q4_0 one as *"faster, less accurate"*. The file we
measured is no longer offered.

Measured against it, four runs
([#138](https://github.com/evanwtf/local-llm/issues/138)): the replacement is
**+9.5% decode and −24.5% prefill**, and both builds answer 6/6 on a
six-question `ds4-eval` gate. That gate ranks nothing — it says neither is
broken — so **the accuracy claim that motivated the change is still
unmeasured here.**

Two cautions on those figures. Neither engine loads the other's weights, so
the quant and the engine move together and neither can be credited alone. And
prefill is prompt-dependent ([#140](https://github.com/evanwtf/local-llm/issues/140));
that −24.5% is one prompt at 1298 KiB and must not be pooled with a figure
taken on another.

Nothing here changes the recommendation — the llama.cpp stack is still the one
to install — but a reader reproducing our ds4 numbers should know they are
pinned to a file the upstream author has moved on from.

### Three rows here cannot be reproduced from upstream sources

The `qwen38fnds4shim`, `qwen38fnds4kimat` and `qwen38fnds4mtp7shim` rows all
need **PLE sidecar support**, and that exists only on ivanfioravanti's forks:
[`ivanfioravanti/ds4-metal`](https://github.com/ivanfioravanti/ds4-metal) and
[`ivanfioravanti/ds4`](https://github.com/ivanfioravanti/ds4) branch
`qwen3.8-flash-next`. `antirez/ds4` main has no `ple_path` anywhere, so a
build from upstream **will not load these weights at all** — it fails with
`required tensor is missing: per_layer_token_embd.weight`.

Two consequences worth stating plainly rather than discovering:

* Cloning upstream `ds4` and following this file will not reproduce those
  rows. Clone the fork named above.
* Those rows depend on one person's branches staying available. That is a real
  durability risk for a recommendation, tracked as
  [#141](https://github.com/evanwtf/local-llm/issues/141), and it is a reason
  to prefer the llama.cpp stack when either would do.

The `--ple` flag is also undocumented — it is absent from `ds4-bench --help`
and present in the parser. Passing no sidecar produces the same missing-tensor
error as an upstream build, which reads exactly like the model being
unsupported. It is not.

### The shim's scaffolding strip is load-bearing, not tidying

**All three** `qwen38fnds4*` rows — `qwen38fnds4shim`, `qwen38fnds4kimat` and
`qwen38fnds4mtp7shim` — run behind `ds4_qwen_tool_shim.py`, which removes the
bare `<tool_call>` tags from the content it hands back after it has recovered a
tool call. That looked like hygiene when it shipped. It is worth **23 points of
pass rate**, measured 2026-09-06 as an A/B over 8 runs of 15 tasks with the arm
alternating A B B A and the server restarted before each run:

| shim | passed | trials with no solution |
|---|---|---|
| strip on (shipped) | **53/60, 88%** | 7 |
| strip off (`SHIM_NO_STRIP=1`) | **39/60, 65%** | 21 |

Every strip-on run scored higher than every strip-off run — 15, 12, 14, 12
against 9, 11, 10, 9 — so the gap does not rest on pooling
([#112](https://github.com/evanwtf/local-llm/issues/112)).

**The 23 points were measured on `qwen38fnds4shim` only** — all 120 A/B rows
carry that backend. The other two run behind the same shim and so carry the
same dependency, but neither has been measured with the strip off, and the size
of the effect there is unknown. `qwen38fnds4kimat` is the point that matters:
it is the strongest of the three (90/90, 97s) and therefore the one a reader is
most likely to reproduce.

**A reproduction that proxies this shim without the strip will not get these
numbers**, and the failure will look like the model rather than the plumbing:
the trial ends with no tool call and no code, not with wrong code. What the
experiment does not establish is *why* — whether the echoed tags poison the
model's context or break the client's own handling of the message.
