# Why the benchmark is shaped this way

Design rationale and the findings that produced it. Moved out of the root
README on 2026-09-06 to keep that file to a usable length; the content is
unchanged except where noted.

## What this is for

A working fallback for when hosted inference is unavailable or unaffordable.
Concretely: something that can be handed a real repository, told to fix a real
failure, and left to run an implement-test-verify loop to a green test suite.

### The target stack is open end to end

**OpenCode + an open model + an open engine, on hardware we own.** All three
have to be things that survive a vendor deciding otherwise:

| layer | what it must be | current candidate |
|---|---|---|
| **agent** | open source, installable from source | **OpenCode** |
| **model** | open weights, on local disk | DeepSeek V4 Flash, Qwen3.8-Flash-Next, GLM-5.3-Flash |
| **engine** | open source, runs offline | llama.cpp, ds4/DwarfStar, Ollama |
| **hardware** | owned, not rented | M5 Max 128 GB |

**An open model on an open engine driven by a proprietary client is not a
fallback — it fails with the vendor.** That is why the agent layer is now held
to the same standard as the other three, and why **OpenCode is the primary
harness this project measures.** Claude Code and Codex remain in the suite as
*reference points*: they establish what a task's ceiling looks like, and a gap
between them and OpenCode is a defect to chase rather than a result to publish.

This is a change of priority, made 2026-08-30. Earlier results ranked clients
neutrally and the recommendation followed whichever scored best. It now follows
the stack that still works when a vendor stops answering — and "OpenCode runs
this suite reliably" is a project goal, not an observation to record.

**Status: it does not yet.** OpenCode's measured results here are under
investigation and none of them should be cited (#54, #55). The short version:
`opencode run` is headless, `external_directory` defaults to `ask`, and with
nobody to ask, agents were observed reading — and in one case destructively
editing — repositories outside the trial checkout. Every OpenCode number
predates that discovery and has to be re-measured under confinement.

## Two target repositories, two languages

The benchmark excises a function from a real repository and asks the agent to
restore it. There are two:

| repo | language | size | tests | oracle |
|---|---|---|---|---|
| `~/git/gmail-archive` | Python | 1,833 lines | 71 | `uv run pytest -q`, ~0.85 s |
| `~/git/monitor` | Swift | 11,265 lines | 215 | `swift test`, 0.705 s |

**The second exists because the first ran out of things to find.** gmail-archive
has 52 functions, a median of 13 lines, and exactly *one* function carrying the
surface that produced the only code-quality defect in 18 trials. A larger
codebase in a language these models see less often is the test of whether that
was the task set or the repository (#4, #42).

Both are pinned on a `local-llm-benchmark` branch. That is not ceremony: on
gmail-archive, `origin/main` had moved **73 commits** ahead of the pinned base
while the working checkout sat held back, and a routine `git pull` would have
silently changed what every trial measures.

**Results from the two are not pooled.** Different repository, language and
oracle — a new series.

**What the second repository actually taught, which was not the question asked.**
Swift did **not** make the tasks harder to pass — 44/45 on the first set, 8/8 on
a harder second set (#44, #45). The repository was not the limit on correctness.
What it exposed instead is a measurement Python cannot produce here:

- **`swift test` has a build step**, so an agent can fail by emitting code that
  does not compile. A Python syntax error is a pytest collection error; there is
  no separate build to fail. One trial in 53 has failed this way.
- **How much more a pair writes on unfamiliar ground varies 2.3x**, from 1.19x
  to 2.73x moving Python → Swift. Wall time tracks output tokens at r=0.98, so
  this is a practical number, not a curiosity.
- **That gap widens with difficulty** (#45): between the terse and verbose pairs
  it went 5.42x → 8.26x on tokens when the tasks got harder. Measuring inflation
  on easy tasks *under*-estimates the spread on hard work.

**Caveat carried on every one of those numbers:** the Swift tasks are not
difficulty-matched to the Python ones, so the ordering is sound and the absolute
ratios are not.

