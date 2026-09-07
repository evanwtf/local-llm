# Batch 0906-1716 is VOID — incomplete, 3 runs of 4

**Do not read this batch out. Do not pool its rows with any other batch.**

Ran 2026-09-06T17:16:36-0400 to 2026-09-06T19:46:42-0400. Produced **45 rows,
not 60**: `legacy` once, `sandbox` twice. The pre-registration in
`scripts/targets_ab.sh` requires **two sweeps per arm**, so this batch cannot
answer the question it was run to answer.

## Why it stopped

Run 4's smoke gate could not reach the shim:

```
smoke: qwen38fnds4shim reverse  correct=False 0.0s (URLError: [Errno 61] Connection refused)
smoke.SmokeFailure: qwen38fnds4shim did not answer
[19:46:42] run 4 exited non-zero; keeping what it wrote
[19:46:42] run 4 done, 0 transcripts
```

The shim was killed by `targets_ab.sh`'s own teardown. `pkill -f qwen_tool_shim`
sat outside the `DRY_RUN` guard, so a dry run started no shim and killed
whichever one was already running. The `UNTIL` cutoff fix (#175) was being
verified with five dry runs, and one landed in the fifteen seconds between run
3 finishing and run 4's gate.

The machine lock did not prevent it because a dry run skips the lock — it sits
inside the same `DRY_RUN -eq 0` guard as preflight. The mode was invisible to
the running batch and destructive to it at once.

Fixed in #180, with a test that runs a decoy process and fails if the guard
regresses.

## What the rows are still good for

Nothing that gets quoted. Runs 1-3 completed normally and carry
`harness_dirty: false` throughout, so they are not corrupt — they are
**incomplete**, which is a different thing and just as unusable. The arms are
unbalanced by construction (one legacy sweep against two sandbox), and reading
a partial batch is how a pre-registration turns into a post-hoc story.

Nobody on this project has looked at the per-arm comparison for this batch, and
nobody should before the re-run: a peek now biases how the clean result is read.

## The re-run

Needs #180 merged first, or the same thing can happen again. See #146.
