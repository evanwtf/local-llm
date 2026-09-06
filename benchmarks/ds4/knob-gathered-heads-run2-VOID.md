# Run 2 is VOID — I ran a test file on one arm

`knob-gathered-heads-run2/` must not be pooled. At about 16:12:5x I ran
`uv run pytest -q tests/test_decode_ab_report.py` while run 2's rep 3 arm A
(`on`) was in flight — that arm started 16:12:45.

| | |
|---|---|
| 16:12:45 | run 2, rep 3, arm `on` starts |
| ~16:12:5x | `uv run pytest -q tests/test_decode_ab_report.py` (50 tests, 0.14 s plus uv startup) |
| ~16:13:4x | run 2, rep 3, arm `off` starts |

**The load is far smaller than the one that voided the `f309990` batch** — one
or two seconds of single-threaded Python against a ~60 s GPU-bound arm, rather
than a two-minute full suite. It is very unlikely to have moved the result.

It is voided anyway, and the reason is the point. This morning another agent's
pytest run landed on one arm of a paired comparison and that batch was voided.
The rule cannot be "asymmetric load voids a run, unless the person who caused it
judges it small enough afterwards" — that judgement is exactly what a confound
that flatters you looks like from the inside. A standard enforced on a peer and
not on the person enforcing it is not a standard.

Replaced by `knob-gathered-heads-run5/`. Runs 1, 3, 4 and 5 are the batch.

The direction, for the record: the load sat on the `on` arm, which would slow
the arm predicted to be *faster*, biasing **against** the hypothesis rather than
toward it. That makes it the less dangerous kind of confound, and is still not a
reason to keep it.
