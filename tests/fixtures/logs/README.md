# Real engine output, kept so parsers are tested against what ds4 prints

Every file here is an excerpt of a log this machine actually produced. None of
it is written by hand, and that is the whole point.

`benchmarks/agent/test_ds4_route.py` asserted for months that the withheld
Metal route prints

    Metal 4 tensor API available but not enabled (set DS4_METAL_ENABLE_TENSOR=1)

ds4 has never printed that line. It prints

    ds4: Metal 4 tensor API available but not enabled (numerics); set DS4_METAL_ENABLE_TENSOR=1 to override

The invented line appears in **0** of the logs under `~/bench-logs`; the real
one appears in 12. The test passed because the matcher it exercised was a
substring of the head of both, so nothing ever disagreed. A parser tested only
against strings its author typed is a test of the typing.

Four of the ten markers our parsers look for appeared in **zero** committed
logs before this directory existed: the withhold line, both halves of the
`ds4_test` equivalence summary, and the shim's strip-mode line. They are here
now, and `test_log_fixtures.py` fails if a marker constant ever loses its real
example.

## Provenance

| file | source | lines |
|---|---|---|
| `ds4-server-route-tensor.log` | `~/bench-logs/149-route-ab/server-t-sweep1.log` | head 40 |
| `ds4-server-route-withheld.log` | `~/bench-logs/149-route-ab/server-r-sweep1.log` | head 40 |
| `ds4-server-graph-mtp.log` | `~/bench-logs/greedy-mtp-ab-20260909-054041/ds4server-r1-mtp.log` | head 30 |
| `ds4-server-graph-plain.log` | `~/bench-logs/greedy-mtp-ab-20260909-054041/ds4server-r1-plain.log` | head 30 |
| `ds4-test-metal-tensor-equivalence.log` | `~/bench-logs/149-route-ab/gate-validate.log` | whole file, 106 lines |
| `shim-strip-on.log` | `~/bench-logs/greedy-mtp-ab-20260909-054041/shim-8102.log` | head 6 |

Trimmed with `head`, never edited. If a line here looks wrong, the engine wrote
it that way and the parser is what needs to change.

## Adding one

Take it from a real run, record where it came from in the table above, and keep
it short — these exist to carry a marker, not to be a corpus. A log that needs
editing to make a test pass is not a fixture; it is a hand-written string with
extra steps.
