# Real engine output, kept so parsers are tested against what ds4 prints

Every file here is an excerpt of a log the M5 Max produced. None of it is
written by hand.

**Why.** `benchmarks/agent/test_ds4_route.py` once asserted that the withheld
Metal route prints

    Metal 4 tensor API available but not enabled (set DS4_METAL_ENABLE_TENSOR=1)

ds4 has never printed that line. It prints

    ds4: Metal 4 tensor API available but not enabled (numerics); set DS4_METAL_ENABLE_TENSOR=1 to override

The test passed because its matcher was a substring of the head of both. A
parser tested only against strings its author typed is a test of the typing.
Four of the ten markers the parsers look for had no committed real example
before this directory existed. `tests/test_log_fixtures.py` fails if a marker
constant loses its real example.

## Provenance

| file | source | lines |
|---|---|---|
| `ds4-server-route-tensor.log` | `~/bench-logs/149-route-ab/server-t-sweep1.log` | head 40 |
| `ds4-server-route-withheld.log` | `~/bench-logs/149-route-ab/server-r-sweep1.log` | head 40 |
| `ds4-server-graph-mtp.log` | `~/bench-logs/greedy-mtp-ab-20260909-054041/ds4server-r1-mtp.log` | head 30 |
| `ds4-server-graph-plain.log` | `~/bench-logs/greedy-mtp-ab-20260909-054041/ds4server-r1-plain.log` | head 30 |
| `ds4-test-metal-tensor-equivalence.log` | `~/bench-logs/149-route-ab/gate-validate.log` | whole file, 106 lines |
| `shim-strip-on.log` | `~/bench-logs/greedy-mtp-ab-20260909-054041/shim-8102.log` | head 6 |
| `shim-strip-off.log` | `SHIM_NO_STRIP=1 uv run python ds4_qwen_tool_shim.py --port 18101` on 2026-09-09, captured on a free port and stopped at once | whole output, 2 lines |

Trimmed with `head`, never edited. If a line here looks wrong, the engine wrote
it that way, and the parser is what needs to change.

## Adding one

Take it from a real run, record its source in the table above, and keep it
short: a fixture carries a marker, it is not a corpus. A log that needs an edit
to make a test pass is not a fixture.

**Never compose a fixture from a driver's grep target.** A grep target is what
we hope the program prints; a fixture is what it printed. The shim's OFF line
was once typed as `scaffolding strip: OFF`, but the shim prints
`scaffolding strip: OFF (experiment arm) (#112 remedy 2)`. The driver's
`grep -q` and `tests/test_equiv.py` both matched the prefix, so both passed on
a string the shim never emits. `tests/test_equiv.py` now reads the expected
line out of the fixture and checks the whole text.
