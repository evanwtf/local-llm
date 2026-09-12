# Migration audit: AGENTS.md restructure

This records where every distinct rule and fact from the old root `AGENTS.md`
went when it was reduced from an entry point plus everything else (17,826 words)
to a slim entry point (1,794 words) plus task-triggered topic docs. It exists so
a reviewer can confirm nothing was lost, only moved.

## Context

- **Source revision:** `AGENTS.md` and `CONVENTIONS.md` as of `a3d506a` (the
  branch base). `AGENTS.md` was byte-identical at `485f17e`, where it was first
  read in full; the two commits between them (`dd03d40`, `a3d506a`) touched only
  `TESTING-SET.md`, `benchmarks/agent/tasks.toml`, `config/opencode.json`, and
  `scripts/README.md`.
- **Delivery mode:** `pr`. CI runs on `pull_request`
  (`.github/workflows/test.yml`), which disqualifies `direct`; established repo,
  remote `git@github.com:evanwtf/local-llm.git`, default branch `main`.
- **Approach:** reconcile (`AGENTS.md`, `README.md`, `CONVENTIONS.md` were all
  substantial) with a small bootstrap (the four baseline policies and the
  secret-handling section were absent and were added).
- **Implementation maturity:** established.
- **Project structure:** simple repository. `sandbox/` is gitignored (the
  harness's ephemeral clones of target repos, #146), so the only tracked
  `AGENTS.md` is the root one; no component guidance applies.

## Destinations created or changed

| file | status | holds |
|---|---|---|
| `AGENTS.md` | rewritten, slimmed | entry point: cross-cutting rules, the loop, secrets, baseline policies, reading map |
| `docs/measurement-discipline.md` | new | reading, comparing, and reporting a number |
| `docs/harness-operations.md` | new | servers, engines, target repos, the ledger |
| `docs/automation-hazards.md` | new | shell, subprocess, exit-code, and waiter traps |
| `docs/agent-workflow.md` | new | machine naming, issues, dates, the loop, peers, releases |
| `CONVENTIONS.md` | extended | data/safety; received two moved rules and two consolidations |
| `README.md` | one correction | stale test count fixed |
| `.gitignore` | narrow addition | `.env` family, keeping `.env.example` trackable |
| `CLAUDE.md` | new | relative symlink → `AGENTS.md` |

The topic docs are **task-triggered specialized reading**, each with an explicit
trigger in its header and in the `AGENTS.md` reading map. They are outside the
universal 4,000-word budget; the universally required reading is `AGENTS.md`
alone (1,794 words). `CLAUDE.md` is a symlink alias and is not counted again.

## Consolidations that changed structure (every distinct fact kept)

1. **"Keep the historical record honest"** existed in both `AGENTS.md` and
   `CONVENTIONS.md`. Consolidated into one section in `CONVENTIONS.md` with all
   three distinct faces preserved: never delete a result row (`excluded: true` +
   reason, `summarize.py` skips it); do not rewrite logs/traces/transcripts on a
   path change; correct prose openly rather than silently, keeping a superseded
   finding visible. Anchor `#keep-the-historical-record-honest` preserved;
   inbound links from `agent-workflow.md` and `measurement-discipline.md` point
   to it.

2. **The exit-status/`| tail` trap** appeared twice in `AGENTS.md` (its own
   section, and again inside "Finishing a batch"). Consolidated into one section
   in `automation-hazards.md`, keeping both distinct facts — "twice in one
   session" and "put a red commit on main on 2026-09-01". "Finishing a batch"
   (now in `agent-workflow.md`) keeps a one-line actionable form and links to it.

3. **The union-merge prohibition** appeared in `CONVENTIONS.md` ("One main…")
   and as a full section in `AGENTS.md` ("Never resolve a data file by taking
   the union"). Consolidated into one subsection of `CONVENTIONS.md`'s "One main"
   section, keeping every distinct detail: `exclude_rows.py` annotates in place
   and `load()` does not de-duplicate; `.gitattributes` leaves `results.jsonl`
   to 3-way merge; the 90 restored rows; `archive_pre_dir_rows.py`;
   resolve-as-theirs-plus-genuinely-new; the `HAS_LOCAL_RESULTS` CI-skip and
   #218; re-run archivers after any ledger merge.

## Reconciled tension (documented, not silently resolved)

**Machine branches.** `CONVENTIONS.md` said "a machine is a field in the data,
not a branch… no long-running per-machine branch"; `AGENTS.md` said "a branch
named after a machine is permanent infrastructure" (the Ryzen branch must never
be deleted). Both cite #292. These are not in conflict, and the reconciliation
is now stated in `CONVENTIONS.md` under "One main, machines are directories": the
default is that every machine that **can** rebase onto `main` commits there and
uses only short-lived topic branches; the **exception** is a machine with **no
coordination channel** (the Ryzen box), for which a permanent branch named after
it is the only write interface — never deleted, rebased, or force-pushed, and
"346 behind" is its normal steady state. "No long-running per-machine branch"
forbids holding a machine's commits off `main` when it could merge them; it does
not license deleting the branch of a machine that has no other way to write. All
distinct facts from both copies are preserved (the 96 rows across seven
backends, the feature-branch merge-test that is wrong for a machine branch, the
still-open question of copying rows into main).

## Correction to hand-written content

- **`README.md`**, quickstart comment `# 1075 tests` → `# the full suite
  (2,600+ tests)`. The old figure was demonstrably stale: a static count found
  **2,663** `def test_` functions across **175** test files (26 of them use
  `@pytest.mark.parametrize`, so the collected total is higher still). Counted
  statically with `git ls-files` + `grep`; **the suite was not executed** (a
  documentation task does not run test suites, and this is the benchmark
  machine). The replacement is a scale figure that can only grow, not a brittle
  exact count.
- Two other `README.md` figures — "(43 scripts)" and "the 19 repos we watch" —
  were **preserved unchanged**. Crude static proxies disagreed (106 tracked
  `scripts/*.py|*.sh` including `lib/` helpers the index may exclude; 15 rough
  `upstream_sweep.py` matches), but neither is clean evidence of the correct
  value, so per "when a claim cannot be verified, preserve it" they were left
  as-is.

## Bootstrap additions (absent before; added from approved intent)

- **Four baseline policies** — added verbatim to `AGENTS.md` "Baseline policies"
  as required intentional policy (minimal changes; preserve architecture; no new
  dependencies without justification; update tests and docs on behavior change).
- **"Handling secrets and credentials"** — added to `AGENTS.md`. Documents the
  repo's actual workflow: no stored secrets; non-secret local tokens in
  `tasks.toml`; `ANTHROPIC_API_KEY` read from the environment and popped for
  local runs by `run.py` (asserted in `test_run.py`); prompt captures
  (`fail-*.json`, `--trace`) as the real exposure vector, already gitignored;
  1Password via `op` as the operator's established store; the never-display /
  ignore-rules-don't-scrub-history / report-path-and-rotate requirements.
- **`.gitignore`** — added `.env` and `.env.*` with `!.env.example`, scoped so
  no source file or public certificate is hidden (verified with
  `git check-ignore --no-index`).
- **`CLAUDE.md`** — created as a relative symlink to `AGENTS.md` (mode 120000).
- **Reading map** — new navigational table in `AGENTS.md`.

## Section-by-section disposition of the old AGENTS.md

Every section is **moved verbatim** to the destination unless noted; where the
entry point keeps a one-line actionable form, that is marked "+ terse rule in
AGENTS.md". Destinations: MD = `docs/measurement-discipline.md`, HO =
`docs/harness-operations.md`, AH = `docs/automation-hazards.md`, WF =
`docs/agent-workflow.md`, CV = `CONVENTIONS.md`, A = retained in `AGENTS.md`.

| # | original section | dest | notes |
|---|---|---|---|
| — | Header + OpenCode-pre-dir INVALID warning | A | retained as the entry-point banner |
| 1 | Before you say a model is not on disk, run the census | HO | + `model_inventory.py` in AGENTS.md commands |
| 2 | Confirm your machine is one we manage (2026-09-11) | WF | + terse machine/naming rule in AGENTS.md |
| 3 | New code is Python, not shell (2026-09-09) | AH | whole section incl. failure list, port table, exception |
| 4 | Cite engine source as `file:line at <sha>` (2026-09-06) | MD | + terse rule in AGENTS.md |
| 5 | An issue is a public work log (2026-09-06) | WF | + essentials in AGENTS.md loop |
| 6 | Every time and date is ISO 8601 (2026-09-06) | WF | incl. "log lines too" subsection; + terse rule in AGENTS.md |
| 7 | A download is not verified until on disk (2026-09-06) | HO | |
| 8 | Absolute URLs in issue and PR comments (2026-09-06) | WF | + terse rule in AGENTS.md |
| 9 | Model weights stay out of Time Machine (2026-09-06) | CV | placed next to the weights-archive rule |
| 10 | Check the field before concluding not possible | WF | |
| 11 | One definition of "done" (2026-09-04, three times) | MD | |
| 12 | Name both directions of a ratio (2026-09-04, twice) | MD | |
| 13 | Assert what a script does, not what it mentions (2026-09-04) | AH | |
| 14 | Check the exit status, not the tail (2026-09-01, twice) | AH | **consolidated** with the duplicate in #24 |
| 15 | The testing set is written down (2026-09-01) | HO | |
| 16 | Three engines, not four: LM Studio retired (2026-09-01) | HO | |
| 17 | Measure OpenCode only (2026-09-01) | HO | + terse rule in AGENTS.md |
| 18 | OpenCode is the primary harness (2026-08-30) | HO | premise summarized in AGENTS.md "What this project is answering" |
| 19 | How to call OpenCode (2026-09-01) | HO | + `--dir` rule in AGENTS.md |
| 20 | What this project is answering | A | compacted as the orientation section |
| 21 | Do not overstate what is measured | MD | + terse rule in AGENTS.md |
| 22 | Never write "N times faster" | MD | full table; + terse rule in AGENTS.md |
| 23 | The working loop | A + WF | compact loop in AGENTS.md, full narrative in WF |
| 24 | Post a status update every 5 minutes | WF | + terse rule in AGENTS.md |
| 25 | antirez is the sherpa | HO | |
| 26 | Another Mac's result is a lead | MD | |
| 27 | Finishing a batch regenerates the derived docs | WF | embedded exit-code trap consolidated into #14 (AH) |
| 28 | Stamp every line with the code (provenance) | MD | + terse rule in AGENTS.md |
| 29 | Never put backticks in a `-m` message | AH | + terse rule in AGENTS.md |
| 30 | Always measure the latest infrastructure | HO | + terse rule in AGENTS.md |
| 31 | Pin the sampler, and vary one thing at a time | MD | |
| 32 | Restart the model server between arms (#112) | HO | + terse rule in AGENTS.md |
| 33 | A failing arm looks fast, so pair the tasks | MD | |
| 34 | A wall-time difference is a token-count hypothesis | MD | |
| 35 | Know what a trial count can support | MD | + terse rule in AGENTS.md |
| 36 | One trial is not a result | MD | + terse rule in AGENTS.md |
| 37 | Keep the historical record honest | CV | **consolidated** with the CONVENTIONS.md copy |
| 38 | American English spellings only (2026-09-05) | WF | + terse rule in AGENTS.md |
| 39 | A figure needs its input named (2026-09-04) | MD | |
| 40 | Record the client version; do not pin it (2026-09-04) | HO | |
| 41 | Check that both arms can run (2026-09-04) | MD | |
| 42 | A generated-token count equal to the cap is a truncation (2026-09-04) | MD | |
| 43 | A remedy that cannot be switched off cannot be measured (2026-09-04) | MD | |
| 44 | Name the confounds | MD | |
| 45 | Observe the wire call, not the status code | MD | |
| 46 | Wait for a completion, never for /health | HO | + terse rule in AGENTS.md |
| 47 | Always start from a known-good reference repo | HO | + terse rule in AGENTS.md |
| 48 | Verify the oracle before trusting a run | HO | |
| 49 | Adding a language means a parser | HO | |
| 50 | Write results through `results.py` | HO | referenced from CV "historical record" |
| 51 | Transcripts are on by default | HO | |
| 52 | Report results with the script (2026-09-01) | MD | |
| 53 | Never `pkill` `run.py` (2026-09-03) | HO | |
| 54 | ds4's Qwen MTP runs only at temperature 0 (2026-09-08) | MD | |
| 55 | MTP is not a speed-only flag (2026-09-03) | MD | |
| 56 | A backend fast, quantised, thermally fine — and unusable | MD | |
| 57 | Sustained load drifts ~10% — bracket with A-B-A | MD | |
| 58 | Coherence-check at temperature 0 | MD | + `coherence_check.py` in AGENTS.md commands |
| 59 | Nothing may feed `results.verdict()` except the oracle | MD | |
| 60 | Three traps that cost work on 2026-09-07 | AH + WF | **split**: backticks → AH (consolidated w/ #29); repo-check-before-queued-run and no-tests-on-this-Mac → WF |
| 61 | Write findings for the operator (2026-09-07) | WF | + essentials in AGENTS.md loop |
| 62 | Never publish a ratio without the absolutes | MD | + terse rule in AGENTS.md |
| 63 | A pipe hides the exit code | AH | |
| 64 | A `tail -f` monitor never ends (+ 3 subsections) | AH | + terse rule in AGENTS.md |
| 65 | Check a peer every 20 minutes (2026-09-07) | WF | + terse rule in AGENTS.md |
| 66 | A branch named after a machine is permanent (2026-09-07) | CV | **reconciled** with "One main, machines are directories" |
| 67 | Never resolve a data file by taking the union (2026-09-07) | CV | **consolidated** with the union lines in "One main" |
| 68 | Cutting a release (2026-09-07) | WF | whole procedure incl. gates and the changelog/history split |

## CONVENTIONS.md sections

| original section | disposition |
|---|---|
| Model weights are an archive, not a working set | retained in place |
| Never commit a prompt capture | retained in place; now also cross-referenced from AGENTS.md secrets section |
| Held-out text must be the tail | retained in place |
| A log is filed by what it records | retained in place |
| Keep the historical record honest | **consolidated** — received AGENTS.md #37's facets (result rows, prose corrections) |
| Engine roots are configurable, results are local | retained in place |
| One main, machines are directories | **expanded** — received #66 (machine-branch exception) and #67 (union-merge), with the existing union lines merged into the latter |

## Preservation check

Completed by comparing each original section against its destination —
including its examples, exceptions, dates, measurements, citations, tests, and
the incident narrative — not by matching headings. Every distinct item has a
real home reachable through a working link. Two rules were consolidated and one
tension reconciled with all distinct facts kept (above); one hand-written figure
was corrected with its evidence recorded and the old value noted. No rule was
weakened, no exception dropped, and no operator decision changed.
