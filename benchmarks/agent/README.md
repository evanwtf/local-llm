# Agent benchmark — local models as Claude Code backends

> ## ⚠️ OpenCode results before 2026-08-31 21:47 EDT are INVALID
>
> Any OpenCode trial recorded before `2026-08-31T21:47:18-04:00` measures a
> harness bug -- the client was never told which directory to work in, so it
> solved each task and wrote the answer somewhere else. **Do not quote, pool,
> or compare against those numbers.** Cause, cutover and replacements:
> [docs/archive/results-opencode-pre-dir.md](../../docs/archive/results-opencode-pre-dir.md). Other clients are unaffected.

Measures what actually matters for a coding agent: **does it finish the job,
and how long does it take?**

Every other benchmark here measures the engine. This one measures the whole
loop — model, tool use, file editing, and the agent's willingness to stop.

## How it works

Each trial:

1. Exports a real repository at a pinned commit into a **standalone directory**
   with `git archive` — no `.git`, no link to the source repo.
2. **Hollows out one function**, keeping the signature and docstring, then makes
   that the new repo's *only* commit — so the original body exists nowhere in
   the checkout, history included.
3. Verifies the repo's tests now **fail**. A task whose tests still pass proves
   nothing, and this check is not optional.
4. Runs one **agent client** (`claude`, `codex` or `opencode`) against one
   backend, in that directory.
5. Runs the repository's own test suite.

**The test suite is the oracle.** Pass or fail — no rubric, no partial credit,
no judging model marking its own homework.

The directory is destroyed afterwards. It used to be a `git worktree`; that
shared an object store with the source repo and kept a path back to it, and on
2026-08-17 an agent followed that path and modified the operator's checkout.
A worktree isolates files, not the agent. Every row now records
`source_repo_intact`, and the runner refuses to start if the source repo is
dirty or off its pinned commit. See METHODOLOGY.md.

## Running it

```sh
uv run python benchmarks/agent/preflight.py       # FIRST. What is already up?
uv run benchmarks/agent/run.py --dry-run          # verify tasks, run no agent
uv run benchmarks/agent/run.py --trials 3         # the full matrix
uv run benchmarks/agent/run.py --backend qwen --task mbox-scan
```

The backends you select must be up first — `ds4-up` for ds4, Ollama for qwen.

**Start with the preflight, every time.** A model server left running from an
earlier session holds its weights whether or not anyone is using it, and these
models are sized to nearly fill unified memory. If the new one still fits
alongside the old one, nothing fails — the batch just spends hours measuring a
contended machine, and the numbers look plausible. See the
[preflight section in the top-level README](../../README.md#preflight-always-check-what-is-already-running).
`run.py` runs the same check itself and warns, but by then the server is
already started.

Two more artifacts are written outside this repo, because both carry
repository content: `--client-log` (default `~/bench-logs`) keeps the client's
event stream, and `--solutions` (default `~/bench-solutions`) keeps each
trial's diff. The solution's SHA-256 goes into the row itself, so the artifact
can be cleaned up without losing the ability to tell two runs apart.

## Two axes: backend and client

`--client` selects the agent harness. They interact — **no client is fastest on
every backend** — so a result belongs to a *pair*, not to either alone:

```sh
# Interleaved per task, so server drift hits both clients equally
uv run benchmarks/agent/run.py --backend ds4 --client claude --client codex
```

Only `passed`, `wall_seconds` and `touched_tests` compare cleanly across
clients. Token and turn counts come from each client's own accounting: Codex
reports one "turn" per exec rather than per round trip, so it records
`tool_items` instead and leaves `num_turns` empty.

Results append to `results.jsonl`; nothing is overwritten, so runs accumulate
and you can compare across days.

## Raw data is committed

`results.jsonl` and the run logs are **tracked in git, on purpose**. Each row
costs minutes of wall time and a full matrix costs hours, so the data cannot be
cheaply regenerated — and analyses nobody has thought of yet can only be run
against data that still exists.

Every row carries its own environment capture, so old rows stay interpretable
after the stack moves on:

```json
"env": {"claude": "2.1.233", "ollama": "0.32.14-rc0",
        "digest_qwen": "5642e97495e1", "ds4_head": "fdcf3aa",
        "machine": "Apple M5 Max", "macos": "26.5.2",
        "target_commit": "56e55cc"}
```

Model **digests** are recorded, not just tags — a tag can be re-pushed upstream,
a digest cannot. `ds4_head` and `ds4_server_mtime` pin the engine build, since
the binary can predate the checkout.

Rows are never deleted. A run whose conditions were wrong is marked
`"excluded": true` with a reason and skipped by `summarize.py`; `--dry-run` rows
are kept too and skipped the same way. Deleting them would falsify the record.

## Tasks

From [`gmail-archive`](https://github.com/evanwtf/gmail-archive) — a real
project, 4,599 lines of Python, 166 tests that run in under 5 seconds. Fast
tests matter: the oracle runs twice per trial.

| task | removes | tests broken by the excision |
|---|---|---|
| `mbox-strip-envelope` | a pure byte transform | 3 |
| `mbox-scan` | mbox offset scanning | 13 |
| `storage-blob-put` | atomic write, fsync, sha256 | 14 |
| `parser-mbox-quoting` | `unquote_mbox`, must round-trip | 34 |
| `parser-date` | RFC 2822 date parsing with warnings | 49 |

Roughly ordered by blast radius, which is a decent proxy for difficulty.

`query.search` was tried and **rejected**: `tests/test_query.py` skips without
`GMAIL_ARCHIVE_TEST_DATABASE_URL`, so removing the function was invisible to
the oracle. The control check caught it on the first dry run. To use the SQL
surface, bring up the compose stack and export that variable first.

## Reading the results

```json
{"task": "mbox-strip-envelope", "backend": "qwen", "passed": true,
 "wall_seconds": 124.1, "num_turns": 13, "output_tokens": 3837,
 "touched_tests": false}
```

- `passed` — the oracle.
- `wall_seconds` — end to end, including the agent's own tool calls.
- `num_turns` — how many round trips it needed. Fewer is not automatically
  better, but a large number with a failure usually means thrashing.
- `touched_tests` — the cheat detector. A pass with this set to `true` is not a
  pass.
- `control_fails_as_expected` — must be `true` or the row is meaningless.

## Known limitations

**ds4-server does not report input token counts** — `input_tokens` comes back
as 0. Wall time and turns are still comparable; token efficiency is not. Do not
compare `input_tokens` across backends.

**`total_cost_usd` from Claude Code is fiction here.** It prices local
inference at API rates. Ignore it.

**Context windows differ on purpose** — ds4 at 100k, Qwen at 262144. Each model
gets its real window. Capping a model below its capability is not a fair
comparison, and exceeding it makes auto-compact fire after the server has
already truncated.

**One trial is not a result.** These models are sampled, not deterministic. Use
`--trials 3` or more before believing any gap, and treat a single-trial
difference of a few seconds as noise.

**Memory pressure is real on a 128 GiB machine.** ds4 resident is ~91 GiB and
Qwen is ~18 GB. Running both servers at once means one of them is paged out and
its first request pays for it. Bench one at a time, or accept the noise and say
so.

## Results

238 trials across 8 backends and 3 clients. Full report:
[**RESULTS.md**](../../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/RESULTS-agent.md). Picks for this machine:
[**RECOMMENDATIONS.md**](../../RECOMMENDATIONS.md).

Headline: correctness barely separates the *backends* (seven of eight at 100%),
but it separates the *clients* sharply on the same model — Claude Code 14/15,
Codex 15/15, OpenCode 6/15 on ds4. And no client wins everywhere: Codex is 12%
faster than Claude Code on ds4 and 63% slower on Ollama.
