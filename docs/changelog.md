# Changelog

What shipped, and why. Newest first, one section per release.

An entry belongs here the day the work lands — a test, a convention in
`AGENTS.md`, a line in `RESULTS.md`, or an entry here is where a finding
becomes durable. Anything still only in [`NEXT.md`](../NEXT.md) has not landed
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
picks in `RECOMMENDATIONS.md`, and the current queue in `NEXT.md`.

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
