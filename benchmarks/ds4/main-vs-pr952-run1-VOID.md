# Run 1 is VOID — a full test suite ran across three of its six arms

`main-vs-pr952-run1/` must not be pooled. Another session ran the full pytest
suite (1319 tests, ~2 minutes of saturated CPU) while this paired comparison
was on the machine.

| | |
|---|---|
| 16:38:19 | run starts, `main` rep 1 |
| 16:40:12 | `pr952` rep 2 starts |
| ~16:40:30 | another session's full suite starts (inferred from its commit times) |
| 16:41:14 | `main` rep 2 starts |
| 16:42:15 | `main` rep 3 starts |
| ~16:43:00 | suite ends; its commits land 16:43:10 and 16:43:11 |
| 16:43:16 | `pr952` rep 3 starts |

The load covered part of one `pr952` arm and all of two `main` arms. **It is
asymmetric and it falls mainly on `main`**, which would slow the baseline and
make the branch look faster — the direction the comparison is being asked
about. That is the flattering kind of confound again.

This is the third void today from the same cause: another session's CPU work
landing inside a paired comparison. The first two were a pytest suite on
`f309990` run 2 and my own test file on `knob-gathered-heads` run 2. Three
instances is a mechanism problem, not an attention problem — the run lock at
`~/.local-llm-bench/run-lock.json` already knows the machine is claimed, and
nothing consults it before running tests. The guard that closes this is in
`benchmarks/agent/conftest.py`.

Replaced by `main-vs-pr952-run2/`.
