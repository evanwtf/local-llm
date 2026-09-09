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
| `shim-strip-off.log` | `SHIM_NO_STRIP=1 uv run python ds4_qwen_tool_shim.py --port 18101` on 2026-09-09, captured on a free port and stopped at once | whole output, 2 lines |

Trimmed with `head`, never edited. If a line here looks wrong, the engine wrote
it that way and the parser is what needs to change.

## Adding one

Take it from a real run, record where it came from in the table above, and keep
it short — these exist to carry a marker, not to be a corpus. A log that needs
editing to make a test pass is not a fixture; it is a hand-written string with
extra steps.

## The OFF arm's shim line was invented for months

**Resolved 2026-09-09.** Kept because the shape of the mistake is worth more
than the fix.

`shim-strip-off.log` now carries a real capture. Before it, the equiv harness
printed a hand-typed `scaffolding strip: OFF`, and the shim has **never**
printed that. It prints:

    scaffolding strip: OFF (experiment arm) (#112 remedy 2)

The invented line survived because **two substring matchers agreed with each
other**. `strip_toggle_ab.sh:156` greps with `grep -q "$want"`, and
`tests/test_equiv.py` asserted `"scaffolding strip: OFF" in stdout` — and
`scaffolding strip: OFF` is a prefix of the real line, so both passed on a
string the shim does not emit. The fixture and the parser agreed, and neither
agreed with the shim. That is the same failure this directory's opening
section records about `ds4-server-route-tensor.log`, arrived at from the other
direction: there a fixture was wrong, here a fixture was missing and its
absence was papered over with the driver's own grep target.

The fix has two halves, and the second is the one that lasts:

1. capture the real line (`SHIM_NO_STRIP=1` on a free port, stopped at once —
   it prints the mode line before it serves anything, so no upstream and no
   GPU are needed);
2. assert the **whole** line rather than a prefix. `tests/test_equiv.py` now
   reads the expected text out of the fixture and checks `(experiment arm)` is
   in it, so a fixture that loses its real wording fails loudly instead of
   making the differential agree with itself.

**Never compose a fixture from a driver's grep target.** A grep target is what
we hope the program prints; a fixture is what it printed. When the two are the
same string by construction, the test proves only that somebody was
consistent.
