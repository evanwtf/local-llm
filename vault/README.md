# vault — retired shell drivers. Do not run anything in here.

These 15 drivers and the 3 libraries in `lib/` drove every measurement this
project published before the Python port (#235). They are kept because the runs
they produced are still cited, and a result whose driver is deleted cannot be
re-read.

**How each was retired.** Thirteen of the 18 files were cleared by a
**differential**: the real `.sh` and its port ran end to end against recording
fakes and handed their children the same command line. The other five were
not:

| file | how it was cleared |
|---|---|
| `route_agent_ab.sh` | **No differential.** Dead by #264: it passes `--skip-tensor-gate`, which `run.py` no longer accepts, so it cannot produce an agreeing run. |
| `stack_agent_ab.sh` | **Partial differential.** Its `server_argv` and `sweep` function text is executed and compared; its top-level sequence is not (#235's stopping rule). |
| `lib/ds4_server.sh` | **No differential of its own.** A library never runs alone; it clears when all its sourcers clear. |
| `lib/mlx_serve.sh` | Same. |
| `lib/transcript_move.sh` | Same. Its only sourcer is `stack_agent_ab.sh`, which is in here. |

**Nothing in this directory may be executed.** Not to reproduce an old run, not
to check what a flag did, not "just to see". They are text.

## Why keep them

The rows outlive the driver. `results.jsonl`, the manifests under
`benchmarks/`, and the issues all refer to arms these files defined. To learn
what `--kv-disk-space-mb 32768` did on 2026-09-03, read
`disk_kv_mechanism_test.sh`. Git history holds a deleted file too, but nobody
runs `git show` on a path they do not know existed.

## Why they must never run

1. **They are not maintained.** Every fix since the port has landed in the
   Python replacement only, so a number produced here is not comparable with
   one from the port.
2. **Unmaintained scripts hide defects.** `lib/ds4_server.sh` and
   `lib/mlx_serve.sh` once chained an EXIT trap so that a refusal ending in
   `exit 1` came out as `0`, across seven callers. These copies carry the fix,
   but nothing here gets the next one.
3. **They take the machine.** Most acquire the run lock, start a ~100 GiB model
   server and hold it for hours. An accidental run does not fail: it succeeds
   and voids the real measurement in flight. Five dry runs of `targets_ab.sh`
   once killed the shim of a live batch through an unguarded
   `pkill -f qwen_tool_shim`.

## What to run instead

| retired | run this |
|---|---|
| `ab_status.sh` | `scripts/ab_status.py` |
| `coherence_check.sh` | `scripts/coherence_check.py` |
| `decode_ab.sh` | `scripts/decode_ab.py` |
| `decode_ab_engine.sh` | `scripts/decode_ab_engine.py` |
| `decode_ab_repeat.sh` | `scripts/decode_ab_repeat.py` |
| `decode_ab_stack.sh` | `scripts/decode_ab_stack.py` |
| `disk_kv_mechanism_test.sh` | `scripts/disk_kv_mechanism.py` |
| `greedy_mtp_ab.sh` | `scripts/greedy_mtp_ab.py` |
| `metal_knob_ab.sh` | `scripts/metal_knob_ab.py` |
| `mtp_treatment_gate.sh` | `scripts/mtp_treatment_gate.py` |
| `restart_between_trials.sh` | `scripts/restart_between_trials.py A` |
| `restart_between_trials_armB.sh` | `scripts/restart_between_trials.py B` |
| `route_agent_ab.sh` | `scripts/route_agent_ab.py` |
| `stack_agent_ab.sh` | `scripts/stack_agent_ab.py` |
| `strip_toggle_ab.sh` | `scripts/strip_toggle_ab.py` |
| `targets_ab.sh` | `scripts/targets_ab.py` |
| `lib/ds4_server.sh` | `scripts/lib/ds4_server.py` |
| `lib/mlx_serve.sh` | `scripts/lib/mlx_serve.py` |

`scripts/shell_debt.py` reports vaulted lines apart from the shell still in
service, so a move into this directory cannot make the number look better.

## The rule is enforced, not only written

`tests/test_vault.py` asserts four things:

- no file in here carries an executable bit;
- nothing outside `vault/` **builds a path into it**, except the differentials
  that read these files as text. The test greps Python for a literal `vault/...`
  path; it cannot prove nothing runs one (`bash vault/targets_ab.sh` ignores the
  missing executable bit), so the rule above is still a rule, not a mechanism;
- every driver here still has its Python replacement, and no library here has
  a sourcer left outside this directory;
- **no live document tells a reader to run one of these paths.** The first
  draft of this move shipped two such pointers.

The replacements matter most. If one is deleted, this directory stops being an
archive and holds the only copy, and then somebody will run it.
