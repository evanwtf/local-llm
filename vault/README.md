# vault — retired shell drivers. Do not run anything in here.

These 18 drivers, plus one library they share, drove every measurement this
project published before #235. They are kept because the runs they produced are
still cited, and a result whose driver has been deleted cannot be re-read.

**How each was retired, stated exactly, because it is not uniform.** Fourteen
of the eighteen were cleared by a **differential**: the real `.sh` and its port
were driven end to end against recording fakes and handed their children the
same command line. The other four were not, and saying otherwise would claim
evidence we do not have:

| file | how it was cleared |
|---|---|
| `route_agent_ab.sh` | **No differential.** Dead by #264 — it passes `--skip-tensor-gate`, which `run.py` no longer accepts, so it cannot produce an agreeing run. |
| `stack_agent_ab.sh` | **Partial differential.** Its `server_argv` and `sweep` function text is executed and compared; its top-level sequence is not. The gap is deliberate — #235's stopping rule. |
| `lib/ds4_server.sh` | **No differential of its own.** A library is never invoked alone, so it can produce none; it clears when all its sourcers clear. |
| `lib/mlx_serve.sh` | Same. |
| `lib/transcript_move.sh` | Same — its only sourcer is `stack_agent_ab.sh`, which is in here. |

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

2. **Two of them carried a defect that went undetected for the life of the
   script.** `lib/ds4_server.sh` and `lib/mlx_serve.sh` chained an EXIT trap in
   a way that handed `*_stop_on_exit` the *previous* handler's exit status, so
   a refusal that ended in `exit 1` came out as `0`. Five of the seven callers
   take that path, and `mtp_treatment_gate.sh` is a gate whose entire job is
   refusing.

   **These archived copies carry the fix, not the defect** — it was repaired
   before they were vaulted. The point is not that they are broken; it is that
   the bug lived here undetected across seven callers, which is what an
   unmaintained script does, and nothing here is maintained any more.

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
- nothing outside `vault/` **builds a path into it**, apart from the
  differentials that read these files as **text** to compare against. Note
  what this does and does not cover: it greps Python for a literal `vault/...`
  path. It does not and cannot prove nothing *runs* one — `bash
  vault/targets_ab.sh` ignores the missing executable bit — so the rule above
  is a rule, not a mechanism;
- every driver here still has the Python replacement its retirement was granted
  against, and every library here has no sourcer left outside this directory;
- **nothing live still tells a reader to run one of these paths.** That guard
  exists because the first draft of this move shipped two: `AGENTS.md` told
  every agent to run `scripts/coherence_check.sh` before a measurement batch,
  and `preflight.py`'s refusal text named `scripts/stack_agent_ab.sh` as the
  thing to do instead.

The last one matters most. These were retired on the promise that a replacement
exists. If a replacement is ever deleted, this stops being an archive and starts
being the only copy — and then somebody will run it.
