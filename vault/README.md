# vault — retired shell drivers. Do not run anything in here.

These 18 scripts drove every measurement this project published before #235.
Each has a Python replacement, and each was retired only once a **differential**
showed the two hand their children the same command line. They are kept because
the runs they produced are still cited, and a result whose driver has been
deleted cannot be re-read.

**Nothing in this directory may be executed.** Not to reproduce an old run, not
to check what a flag did, not "just to see". They are text.

## Why not just delete them

Because the rows outlive the driver. `results.jsonl`, the manifests under
`benchmarks/`, and the issues that cite them all refer to arms these files
defined. When somebody asks in six months what `--kv-disk-space-mb 32768`
actually did on 2026-09-03, the answer is in `disk_kv_mechanism_test.sh`, and
`git log` on a deleted path is a worse way to find it than a directory.

Git history would technically hold them either way. A directory is what makes
them *readable* — nobody runs `git show` on a path they do not know existed.

## Why they must never run

Three reasons, in the order they will bite you:

1. **They are not maintained.** Their Python replacements are. Every fix since
   the port has landed on one side only, so these will diverge further with
   every commit. A number produced here in 2026-10 is not comparable with one
   produced by the port in the same hour.

2. **Two of them carry a defect we found and fixed only in the port.**
   `lib/ds4_server.sh` and `lib/mlx_serve.sh` chained an EXIT trap in a way
   that handed `*_stop_on_exit` the *previous* handler's exit status, so a
   refusal that ended in `exit 1` came out as `0`. Five of the seven callers
   here take that path, and `mtp_treatment_gate.sh` is a gate whose entire job
   is refusing. The fix is in these files; the point is that it lived here
   undetected across seven callers, which is what an unmaintained script does.

3. **They take the machine.** Most of these acquire the run lock, start a
   ~100 GiB model server and hold it for hours. Running one by accident does not
   fail — it succeeds, and voids whatever real measurement was in flight. That
   has happened: on 2026-09-06 five dry runs of `targets_ab.sh` each reached an
   unguarded `pkill -f qwen_tool_shim` and killed the shim belonging to a live
   batch, which ended after three runs and could not satisfy its own
   pre-registration.

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

`scripts/shell_debt.py` is the live accounting and knows about this directory:
vaulted lines are reported separately from the shell that is still in service,
so moving a file here cannot make the number look better than it is.

## The rule is enforced, not just written

`tests/test_vault.py` asserts three things, because a README is advice and a
test is a rule:

- no file in here carries an executable bit;
- nothing outside `vault/` sources, runs, or builds a path into it, apart from
  the differentials that read these files as **text** to compare against;
- every file here still has the Python replacement its retirement was granted
  against.

The last one matters most. These were retired on the promise that a replacement
exists. If a replacement is ever deleted, this stops being an archive and starts
being the only copy — and then somebody will run it.
