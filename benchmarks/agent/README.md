# Agent benchmark — local models as Claude Code backends

Measures what actually matters for a coding agent: **does it finish the job,
and how long does it take?**

Every other benchmark here measures the engine. This one measures the whole
loop — model, tool use, file editing, and the agent's willingness to stop.

## How it works

Each trial:

1. Creates a **git worktree** of a real repository at a pinned commit.
2. **Hollows out one function**, keeping the signature and docstring, and
   commits that — so the agent cannot `git checkout` its way to the answer.
3. Verifies the repo's tests now **fail**. A task whose tests still pass proves
   nothing, and this check is not optional.
4. Runs `claude -p` against one backend, in that worktree.
5. Runs the repository's own test suite.

**The test suite is the oracle.** Pass or fail — no rubric, no partial credit,
no judging model marking its own homework.

The worktree is destroyed afterwards, so trials cannot contaminate each other.

## Running it

```sh
uv run benchmarks/agent/run.py --dry-run          # verify tasks, run no agent
uv run benchmarks/agent/run.py --trials 3         # the full matrix
uv run benchmarks/agent/run.py --backend qwen --task mbox-scan
```

Both backends must be up first — `ds4-up` for ds4, Ollama for qwen.

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

## First measurements

`mbox-strip-envelope`, one trial each, both passed:

| backend | wall | turns | output tokens |
|---|---|---|---|
| qwen3.8:27b-mlx | 124.1 s | 13 | 3,837 |
| ds4 mixed q2/q4 | 130.1 s | 9 | 1,428 |

Too close to call on one trial of the easiest task, which is the correct
conclusion to draw from it. ds4 was also paged out to ~27 GiB resident when
this ran, so its number is pessimistic.
