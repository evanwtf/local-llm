# #191 landing note

Three patches, applied in this order, each with a bare `git apply`:

1. `191-server-cmdline.patch`   — scripts/stack_agent_ab.sh, tests/test_stack_agent_ab_engines.py
2. `191-head-selector.patch`    — scripts/stack_agent_report_191.py, tests/test_stack_agent_report_191.py
3. `191-sweep-windows.patch`    — scripts/stack_agent_report.py, scripts/stack_agent_report_191.py, tests/test_stack_agent_report_191.py

## The order is load-bearing, not preferred

Patch 3 builds on patch 2. On a clean tree it refuses to apply:

    $ git apply 191-sweep-windows.patch
    error: patch failed: scripts/stack_agent_report_191.py:207
    error: scripts/stack_agent_report_191.py: patch does not apply

That refusal is a feature: a wrong order fails loudly rather than half-applying.
Do not skip patch 2 because the run only needed the record fix. Do not try
patch 3 first. The three commands must run in this order.

## Each patch is one commit

Three commits total. Nothing in the tree until the run is done.
