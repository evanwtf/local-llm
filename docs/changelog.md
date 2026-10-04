# Changelog

What shipped, and why. Newest first, one section per release.

An entry belongs here the day the work lands — a test, a convention in
`AGENTS.md`, a line in `RESULTS.md`, or an entry here is where a finding
becomes durable. Anything still only in an issue comment has not landed
anywhere.

**Entries carry a `## vX.Y.Z` heading and end with a `---`.** That is not a
style preference. `scripts/release_notes.py` reads the section matching a tag
and hands it to `gh release create`, so a heading that disagrees with its tag
publishes the wrong notes, and a section that never ends publishes the rest of
the file. Both have happened; both are now tested.

**Everything before v1.0.0 is in [`history.md`](history.md)** — about 1590
lines of dated entries written before the repo was versioned. Several are the
only written account of why a guard exists. Read it for history; nothing new
goes there.

**Read this for history, not for current state.** Numbers here were true when
written. Current results live in `hardware/<machine>/RESULTS-agent.md`, current
picks in `RECOMMENDATIONS.md`, and the current queue from
`scripts/make_next.py --platform {macos,nvidia}` (#463).

---

## v1.2.0 — 2026-10-03

The coding-agent test on the two-Spark cluster is done for now (operator,
2026-10-03). This release closes it: the pick is restamped on three runs, the
last candidates are measured, and the heartbeat reads the room the Sparks
breathe.

**The cluster pick is the MiaAI TensorFold recipe v1.5.** Same weights (TR3
4bpw) and engine (TensorFold v0.6.0 plus the recipe's patches) as v1.1.0's
pick, now at recipe v1.5 @`1576746` over 3 runs: 124 of 126 trials passed
(95% CI 94.4–99.6%), in 4,246.2 s, 4,071.8 s and 4,013.6 s over the 14 tasks,
59–62% of vLLM's 6,811.6 s on the same weights. Hidden tests 853/1,170
(72.9%). v1.3's single run (3,885.1 s) is 4.5% faster, inside the noise, and
the currency gate will not run it again. Recipe v1.4 is also measured, over 2
runs: 82/84 in 4,069.4 s (#892, #918, #920, #922, #923, #925, #927, #929,
#932).

**GLM-5.3-Flash on NVIDIA's NVFP4 weights, via kindlingai's vLLM launcher**:
3 runs, 120/126, 4,872.1 s (125% of the pick). The screen cuts it (#896,
#910, #911, #913, #916, #917).

**TensorFold v0.6.5 is not run.** The recipe's 70 patches apply 70/70 to
v0.6.0 and 42/70 to v0.6.5; we wait for the recipe to rebase rather than port
them (#892).

**The heartbeat reads the inlet air.** The weather station's indoor sensor sits
at the front of the Sparks. Each update gives its temperature and each GPU's
rise over it, looking back 2 h because Home Assistant records the sensor only
on change (#928, #933).

**Sources.** The upstream sweep watches TensorFold and the four recipes built
on it (#931). Three DeepSeek-V4.1 two-Spark routes are filed as leads (#919,
#924, #930), parked with #897, #912 and #811 for a later round.

**Client image.** uv 0.12.23 and CPython 3.14.8, after the currency gate
refused the older image twice (#907, #908, #926).

**Known gaps.** Unchanged from v1.1.0: cluster rows record the client host's
vLLM version (#904), and TensorFold builds record as `unknown` (#320).

---

## v1.1.0 — 2026-10-01

The first release since v1.0.1, after 534 merged pull requests. It marks the
two-Spark cluster's first full set of picks.

**The cluster has a pick.**
`hardware/Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md` is rewritten from the
whole ledger: 1,405 rows from 2026-09-22 to 2026-10-01, 18 stack
configurations. The pick is GLM-5.3-Flash EXL3 TR3 on TensorFold v0.6.0,
through MiaAI-Lab's recipe. It passed 83 of 84 replay and hard-set trials, and
took 57% of vLLM's time on the same weights: 3,885.1 s against 6,811.6 s over
14 tasks. It is provisional at two runs of the three the method asks for. The
page states the test environment, and every recipe commit, image, weight
revision and client version behind the numbers. It also lists the tools that
were behind on the day.

**The cluster.** Two DGX Sparks over 200 Gb/s RoCE. Its rows go to their own
ledger, behind a verified identity (#647). An all-reduce gate checks the fabric
before a batch (#646). A systemd timer posts the heartbeat, not a session
(#691).

**Remote clients.** The agent runs on a separate machine and calls the head
node's API (#562). The client is a pinned image with a 12 GiB memory cap, and
every row records the cap (#611, #637). Linux trials run in a bwrap sandbox,
with a private `/tmp` and the answers hidden (#476, #780).

**Harder tasks.** The replay set rebuilds real commits from their tests (#714).
The hard set adds held-out tests and multi-commit spans (#726). The standard
set no longer separates the main stacks on pass rate.

**Screening.** `scripts/screen_stacks.py` ranks stacks by their sum of median
trial times (#762, #813). `run.py` stops a run early when its failures reach
30% of the leader's passes, or its time reaches 200% of the leader's (#812). A
replay plus hard launch now counts as one run, so the early stop works for it
(#900, #902).

**Reporting.** `report.py --results` reads any ledger, so a cluster readout is
one command (#903). Every row records whether a failed trial edited source
(#770). Re-graded held-out verdicts apply in reports (#801).

**Known gaps.** Cluster rows record the client host's vLLM version, not the
serving image's (#904). TensorFold builds record as `unknown` (#320).

---

## v1.0.1 — 2026-09-07

Three defects in the release machinery v1.0.0 shipped, all found by using it.

**Release notes no longer swallow the changelog.** `scripts/release_notes.py`
ended a section at the next `##` heading. There was no next `##` heading — the
~1590 lines below v1.0.0 predate versioning — so the notes for a six-paragraph
release came out at 102,607 bytes. A section now ends at a `---` as well, and a
test asserts against the real file that the notes stay under 8 KB. Note what
the gate did while this was wrong: it passed. Non-empty output and exit 0 are
indistinguishable from success, which is the failure mode a gate is supposed to
be immune to.

**The release workflow could not start.** `${{ runner.temp }}` in a
workflow-level `env:` refers to a context that does not exist there; only a
step has it. This is not a readable error — GitHub creates the run, fails it
with *"This run likely failed because of a workflow file issue"*, and writes no
logs, so `gh run view --log-failed` answers `log not found`. `UV_CACHE_DIR` now
comes from a step that appends to `$GITHUB_ENV`, the way `test.yml` has always
set it, and a test walks every workflow's YAML to keep `runner.` under a step.

**The changelog was two files in one.** Everything before v1.0.0 moved to
[`history.md`](history.md), byte-identical. `changelog.md` holds releases and
nothing else. Four tests hold the split: a pre-1.0 entry here fails, a version
heading there fails, an entry dated after the split there fails, and a section
that does not end before the next one fails. Each was checked against a
synthetic violation, so none of them is green by vacuity.

v1.0.0's release notes were published by hand, because the workflow that should
have written them could not start. This is the first release it cuts itself.

---

## v1.0.0 — 2026-09-07

The first tagged release. It marks the point where the three recommended
stacks are locked, reproducible, and installable with one command.

**The recommendations are the product.** `RECOMMENDATIONS.md` names three
stacks, and this tag is the state they were measured in. Evan locked them for
a week on 2026-09-07.

**Slot 2 changed.** "You want it fast" is now Qwen3.8-Flash-Next `Q4_K
imatrix` on ds4 (ivanfioravanti's fork), in place of the llama.cpp mainline
build. Three paired sweeps, 90 rows, arm order alternated per pair: the paired
wall ratio is 0.84 (95% CI 0.76–0.92) and the pass rate does not move — 0
tasks down, 0 up, 15 tied, sign test p=1.000. So: 16% faster, and it costs a
fork and 98 GiB against 84 GiB. The mainline build stays documented as the
fallback, because a fork is a durability risk and the Q4_0 pack that preceded
this one was withdrawn from Hugging Face.

**One command runs a stack.** `scripts/local-agent.sh <stack> [opencode|claude]`
pulls the weights, starts the engine, waits for it to answer, and starts the
agent. It asks before a download, because two of the three stacks are over 80
GiB. It reuses an engine that is already listening rather than starting a
second one on a machine that has room for neither.

**A benchmark refuses a busy machine.** `benchmarks/agent/preflight.py` gates
a run on an empty GPU, and `--allow-contended` is the only way past it. Every
measurement before this one was taken on trust that nothing else was resident.

**Releases are gated.** `scripts/check_release_version.py` refuses a tag that
disagrees with any declared version. `scripts/release_notes.py` refuses a
version with no section in this file. Both run on the tag push, before
anything is published. Notes are generated from here and never written into a
tag message — backticks in `git tag -m` are expanded by the shell, which
deletes text silently.

**Entries from here on carry a `## vX.Y.Z` heading.** Everything written
before this release moved to [`history.md`](history.md) unchanged.

---
