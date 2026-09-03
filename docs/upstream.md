# Upstream issues we track

Engines we depend on, and the upstream work that blocks or affects us.

Moved out of `NEXT.md`, which `AGENTS.md` says carries only the ordered table,
the machine state that is not in git, and the traps. This is none of those.

> **This page is a snapshot and goes stale fast.** The content below was last
> swept **2026-08-29/30**. Since then the ds4 landscape moved considerably --
> `ivanfioravanti/ds4-metal` became a fourth fork, PRs #952/#953/#954 landed or
> opened, and #621 was superseded. **Verify before relying on anything here**,
> and prefer `uv run python scripts/upstream_sweep.py --hours 168` and the
> `source-sweep` skill, which read the current state rather than a transcript of
> a past one.

---

**Swept 2026-08-29 22:30.** antirez/ds4 is in a burst of GLM-5.3 work -- eleven
issues and eight PRs touched it in three days. Check this table before
re-investigating anything GLM- or ds4-related.

### Merge status, verified 2026-08-30 — do not assume main

**GLM-5.3 is NOT merged to ds4 main, and the practical Mac recipe is the preview
branch.** Verified locally, not inferred:

```
upstream/main GLM-5.3 commits:  1  (8db89fe "download: add GLM 5.3 Flash models")
upstream/glm-5.3-flash:         13 commits ahead of main, 0 behind
branch tip 2026-08-29 17:55 +0200   main tip 2026-08-28 23:25 +0200
```

Main has the **download script only**. Everything that runs the model lives on
the branch, which is a clean fast-forwardable superset of main (0 behind), so it
is a sound base rather than a divergent experiment.

**The recipe, unchanged:**

```sh
git clone https://github.com/antirez/ds4 && cd ds4
git checkout glm-5.3-flash
./download_model.sh glm53-q2        # ~90 GB, fits a 128 GB Mac
make
./ds4 -m gguf/GLM-5.3-Flash-Q2.gguf --ctx 32768
```

Q4 on one Mac needs `--ssd-streaming`. Q4 across two 128 GB Macs needs the RDMA
tensor-parallel path (~37 t/s generate, ~500 t/s prefill) and is not our
configuration.

**Note `--ctx 32768` in antirez's own recipe.** That is a third datapoint against
[ds4#890](https://github.com/antirez/ds4/issues/890)'s ">4096 tokens fails":
ds4#892 ran a 4500-token prompt at ctx 8192, and the maintainer's published
command allocates 32k. **Do not treat 4096 as a settled boundary.**

**What is promised but NOT shipped on main:** vision, vector steering (including
an anti-refusal vector), ROCm, better Metal / DGX Spark. **Do not plan around any
of it** -- plan around Q2 on the branch.

**Scope: vision is out of scope for this project.** We measure the coding-agent
use case only. Most of the branch's recent movement is vision work, so **branch
activity is a poor proxy for progress on anything we care about** -- read the
commits, not the commit count. The parts of the promised merge that would matter
here are the Metal improvements and anything touching the tool-call parser
(ds4#569) or KV session reuse (ds4#816); nothing else on that list changes a
coding-agent result.

### The one that changes our plan

**[ds4#892](https://github.com/antirez/ds4/pull/892) -- GLM-5.3 Flash brought up
on an M5 Max 128 GB, which is this machine.** Branch `glm53-mtp-width`, author
`audreyt`. Q2 GGUF, ctx 8192, greedy `--temp 0`:

| mode | prefill | decode |
|---|---|---|
| serial | 76-80 t/s (474 t/s @ 4500-tok prompt) | 33.0 t/s |
| `--mtp` (width 2, upstream) | same | **40.5 t/s** |

MTP acceptance **89.6%** over 135 cycles. `make test-glm53-kda` PASS. Greedy
goldens byte-identical across serial, `--mtp`, and widths 3/4/6.

**This retires "[#39](https://github.com/evanwtf/local-llm/issues/39) is blocked in practice."** The claim there was that `--mtp`
is GLM-gated and GLM does not run, so no flag reaches a working model. Someone
has now run exactly that combination on our hardware and published the numbers.
It also reports a **4500-token prompt succeeding at ctx 8192**, which is above
the 4096 boundary in [ds4#890](https://github.com/antirez/ds4/issues/890) -- so
either #890 is narrower than we recorded or the branch already fixes it. **That
question is cheap to answer and is no longer open here** (measured on the
branch: a ~30 KB prompt prefills at 460 t/s).

Two further findings from #892 worth not re-deriving:

- **Decode is dispatch-bound, not kernel-bound.** A 2-token forward costs 1.23x a
  1-token forward (37.4 ms vs 30.3 ms). Speculative *width* is the lever, not
  kernel speed -- which matches our own Qwen3.8 result that n_tok=2 is near-flat.
- **Wider is worse, with evidence.** Depth-2 acceptance falls to ~45% from 89.6%,
  and each reject costs a KDA restore plus prefix replay: W=3 -> 30.6 t/s,
  W=4 -> 20.8, W=6 -> 16. All below width 2. **Do not spend time on width > 2.**

It also states that **DFlash2 draft support for GLM-5.3 does not exist** -- the
draft GGUFs exist (qwen3-arch, same tokenizer) but the machinery lives in an
`ornith15` branch bound to the Qwen graph. That is directly relevant to [#19](https://github.com/evanwtf/local-llm/issues/19).

### Still blocking us, unchanged

| upstream | what it blocks | our issue |
|---|---|---|
| **[ds4#569](https://github.com/antirez/ds4/issues/569)** | **Codex against any GLM on ds4.** Tool-call parser stringifies every argument value; `"false"` where a boolean is declared. Open since 2026-07-17, hits GLM-5.2 too. | [#41](https://github.com/evanwtf/local-llm/issues/41) |
| **[ds4#816](https://github.com/antirez/ds4/issues/816)** | **Claude Code at long context.** Stateless clients never extend the live KV session — 787/787 misses, `reason=token-mismatch`. Structural, so KV budget does not fix it. | [#38](https://github.com/evanwtf/local-llm/issues/38), [#14](https://github.com/evanwtf/local-llm/issues/14) |
| **[ds4#885](https://github.com/antirez/ds4/pull/885)**, **[#886](https://github.com/antirez/ds4/pull/886)** | Retiring our fork. Both still open. | [#27](https://github.com/evanwtf/local-llm/issues/27) |

### Tracking, not blocking

| upstream | why we care |
|---|---|
| **[ds4#890](https://github.com/antirez/ds4/issues/890)** | **Reconciled 2026-08-30: does not reproduce here.** A ~30 KB prompt prefills at **460 t/s**, on a build that logs crossing the 4096 cap onto the compact indexed path. It is a **memory-budget failure, not a prefill defect**. Our 107.52 GiB stock measurement is now cited upstream as a second machine; the 128 GiB half of the guard is still open. |
| **[ds4#893](https://github.com/antirez/ds4/pull/893)** | **CLOSED, superseded by `b0c31af`.** My earlier note here -- "keeps the fixed 110 GiB ceiling, raising the sysctl buys nothing" -- is **wrong now**: the sysctl is read and *overrides* the heuristic. At 112 GiB it yields exactly 110 GiB, so the conclusion held by coincidence, not for the stated reason. **q4 resident is still unreachable** (177 GiB). |
| **[ds4#891](https://github.com/antirez/ds4/issues/891)** | GLM-5.2 Metal + `--ssd-streaming` fails above 8192 tokens. We measured GLM-5.2 streaming at 30.8 GiB ([#35](https://github.com/evanwtf/local-llm/issues/35)) and called it possible-but-impractical; this caps it further. |
| **#894, #897, #899, #904, #906** | A cluster on GLM thinking/tool replay and KV alignment: prefill ending in `</think>` misfiled, compaction failing when think-mode overshoots. **If GLM-5.3 becomes runnable here, these are the defects to expect**, and they hit exactly the agent loop we benchmark. |
| **[ds4#901](https://github.com/antirez/ds4/issues/901)** | SIGSEGV running GLM-5.3 distributed. Not our configuration (single host), noted so it is not mistaken for our bug. |
| **llama.cpp [#27752](https://github.com/ggml-org/llama.cpp/pull/27752), [#27773](https://github.com/ggml-org/llama.cpp/pull/27773)** | Both **still open** as of 2026-08-29. Our two GLM worktrees track them; neither has merged, so neither is a stable base. |

**Check upstream before writing up a finding.** Every defect we have found
independently was already reported. That is reassuring about the measurements
and would have saved hours of diagnosis.
