# Operating the DGX Spark

The `m5max-runbook.md` analog for a DGX Spark: how to drive one without
wasting its time or losing it to an OOM. It applies to each node of the
two-Spark cluster. Node A also carries the single-Spark history
([`hardware/Cortex-X925-128GB-GB10/README.md`](../hardware/Cortex-X925-128GB-GB10/README.md)).
For the fabric and two-node launches, read
[`dgx-cluster-setup.md`](dgx-cluster-setup.md) and
[`dgx-cluster-howto.md`](dgx-cluster-howto.md). The agent session routine on
the head is in [`hardware/agent-opener-prompt.md`](../hardware/agent-opener-prompt.md), §10.

## Is the machine working? Power, not utilization

**`nvidia-smi --query-gpu=utilization.gpu` is wrong in both directions here.**
It reports occupancy: the fraction of a sample window in which any kernel was
resident. It reads **96% while a model only loads**, and **0% mid-trial**
while the agent runs a tool or the test oracle. Both errors happened on
2026-09-12.

| state | GPU power | temp |
|---|---|---|
| idle | flat **9–11 W** (2026-09-12); 4.2 W on 2026-10-28 | 37–47 °C |
| real work | **35–83 W** | 53–63 °C |

The gap has no overlap, so **power decides**. Temperature lags tens of
seconds, so it is reported and never used to decide.

**Use p90 over 15 minutes, threshold 25 W.** Over five minutes a running
trial reads p50 10 W and p90 19 W, the same as idle, because most samples fall
between decodes. Fifteen minutes is long enough that a live run cannot hide
and short enough to notice a machine that stopped.

```sh
uv run python scripts/gpu_utilization.py state     # BUSY / WORKING / LOADING / IDLE / STALLED
```

Only **IDLE** and **STALLED** are defects. `LOADING` and `WORKING` are the
machine between phases.

**A checkpoint load is disk-bound.** GPU power stays at the idle floor for the
whole shard read (10 W while the disk read 499 MB/s, 2026-09-12), so low power
alone cannot separate loading from idle. The separating fact is that **a
server process exists and does not answer yet**; `machine_state` uses it.

## Prometheus, via gcx

Prometheus runs on a LAN host and scrapes both nodes. The datasource UID is
**`uMatQbvMk`**. [`dcgmi.md`](dcgmi.md) covers dcgmi itself.

**The token is not in a non-interactive shell's environment.** It is in
`~/.config/gcx/token`, and `~/.bashrc` exports it as `GRAFANA_TOKEN` only for
interactive shells. Every `gcx` call otherwise fails `Unauthorized`:

```sh
export GRAFANA_TOKEN="$(cat ~/.config/gcx/token)"
```

**`gcx config check` is not an auth check.** It reports `Connectivity: online`
with no credential at all. `gcx datasources list` is the real test.

Series worth knowing. The head's `instance` label is the one
`scripts/gpu_utilization.py` queries (`PROM_POWER`). The label holds the
node's host name, which the public repo does not hold (#1153). The scripts
read it from `LOCAL_LLM_DGX_HEAD` / `LOCAL_LLM_DGX_WORKER`, or from
`~/.config/local-llm/hosts.toml`, through `scripts/lib/dgx_hosts.py`. With
neither, the clock-cap scripts refuse before any query, and
`gpu_utilization.py` logs a warning and uses its own samples:

```toml
# ~/.config/local-llm/hosts.toml (untracked, one per machine)
[dgx]
head = "<head host name>"
worker = "<worker host name>"
```

| series | port | use |
|---|---|---|
| `DCGM_FI_DEV_POWER_USAGE` | 9400 | the idle/busy signal above |
| `node_disk_read_bytes_total` | 9100 | measures a model load directly |
| `vllm:time_to_first_token_seconds` | the server's port | prefill, which dominates agent wall time (#14) |
| `vllm:num_requests_running` | the server's port | queue depth |
| `vllm:prefix_cache_hits_total` | the server's port | cache effectiveness (#330) |
| `llamacpp:*` | 8020 | the same, with `--metrics` |

Engine counters are per server and reset to zero on restart. Use
`rate()`/`increase()`, not raw deltas.

### Build llama.cpp from the evanwtf fork

Build llama.cpp on a Spark from
[`evanwtf/llama.cpp`](https://github.com/evanwtf/llama.cpp), not from
`ggml-org/llama.cpp`. The fork adds the Prometheus `model` label that
separates metrics from different models (#436). The build must descend from
the fork's `master` and include at least
[`171824a`](https://github.com/evanwtf/llama.cpp/commit/171824a31d13b7320a7fdc72c3539d6095b6eb0).
With the fork remote named `fork`, check the revision before you build:

```sh
git fetch fork master
git merge-base --is-ancestor fork/master HEAD
```

A newer upstream-only revision is not the operational build. Merge upstream
into the fork first, then build the fork.

## Ports and binding

| port | engine |
|---|---|
| 8000 | ds4. It is also vLLM's default port, so a single-node vLLM moves to 8030 |
| 8020 | llama.cpp (`--metrics` is **off by default**; pass it) and ds4 under `dgx_server.py` |
| 8030 | single-node vLLM; metrics on the API port |
| 8043 | the video-gen gallery on the head: read-only, LAN only (`scripts/videogen/gallery_serve.py`, #1413) |
| 8888 | the two-Spark recipes |

**Bind `0.0.0.0`.** Loopback binding silently broke monitoring for weeks:
Prometheus targets sat at `up=0` because nothing they pointed at was
reachable. *A monitoring system that reports nothing looks exactly like one
that reports nothing wrong.*

`scripts/local_agent.py` binds `127.0.0.1` on purpose: it is a one-command
setup for someone else's machine, and their network is not this LAN.

## Before and after a launch

```sh
uv run python scripts/machine_health.py boot                  # rebooted since last check?
uv run python scripts/machine_health.py check --for server   # or --for run
uv run python scripts/machine_health.py confirm --pid N --log FILE
```

- **`boot`** compares the kernel boot id against
  `~/.local-llm-bench/last-boot-id` and reports a reboot once, with uptime
  (exit 2). A reboot wipes every server, the run lock's pid and the session
  scratchpad; without this check it reads as a run of "server died" and
  "stale lock" findings.
- **`check`** refuses a launch into a known-bad state: a dirty tree (rows
  would be stamped `harness_dirty`), a held or stale lock, or a port already
  serving. It names the model on the port, because **reusing that server is
  the fix**. A busy port blocks a *server* and is fine for a *run*; `--for`
  says which. It also refuses a server launch while much memory is held and
  nothing answers on any port (`LOCAL_LLM_MEM_SETTLE_MAX_GIB`, default 24): a
  server's memory outlives its PID. On 2026-09-13 a vLLM launched into a
  still-occupied pool sized an oversized KV cache and was OOM-killed at
  startup (#360). `scripts/memory_gate.py` is the matching wait.
- **`confirm`**: a spawned process is not a working one. On 2026-09-12 a
  second vLLM died at once on a bound port with `EADDRINUSE`, and its wait
  loop polled for 25 minutes while the GPU sat at 9 W.

### One lifecycle interface for long-lived single-node servers

Do not find or stop servers by scanning process command lines:

```sh
uv run python scripts/dgx_server.py start vllm --model MODEL --memory-max 96G -- \
    ~/venvs/vllm/bin/vllm serve MODEL --gpu-memory-utilization 0.66
uv run python scripts/dgx_server.py status vllm
uv run python scripts/dgx_server.py stop vllm
```

The profiles own the LAN bind and port: vLLM `:8030`, llama.cpp and ds4
`:8020`, vLLM-Omni `:8041`, clip viewing `:8042`, Ollama `:11434`. Heavy
profiles require `--memory-max`. The wrapper sets zero swap, records the PID
and scope under `~/.local-llm-bench/dgx-servers`, and on stop waits for the
port and the memory pool to clear.

Every heavy start also gets (#456):

- **a MemAvailable watcher**: a detached `dgx_server.py watch <name>` polls
  `MemAvailable` once a second and stops the scope below **14 GiB**
  (`--mem-floor-gib`, `LOCAL_LLM_MEM_FLOOR_GIB`; `0` disables it). It runs
  outside the scope it watches, so its own stop cannot kill it.
- **a JIT build cap**: `MAX_JOBS=3` in the scope (`--max-jobs`,
  `LOCAL_LLM_DEFAULT_MAX_JOBS`; an explicit `MAX_JOBS=` in the command wins).
  Unset, warmup runs ~22 concurrent `nvcc` jobs on top of the loaded weights
  (#406).

Two-node recipes use their own start and stop scripts
([`hardware/Cortex-X925-128GB-GB10-x2/howto.md`](../hardware/Cortex-X925-128GB-GB10-x2/howto.md)).
Stop a server with its own wrapper, never `pkill` (see the last section).

## What kills a process the agent starts (#691)

Four things have stopped work that an agent started on the cluster. Two
removed a safety mechanism (the memory guard, the heartbeat) with no error
shown. Each now has a prevention; causes 1 and 3 still hit the session's own
loops.

Every class below can also be stopped by a memory net: earlyoom, the 14 GiB
`dgx_server.py` watcher, a two-node recipe's 1.5 GiB memory guard, the 24 GiB
client cap, or the kernel OOM killer. Which nets apply to which process is in
[The layers](#the-layers).

| process | where it lives | what else can stop it |
|---|---|---|
| a single-node server (`dgx_server.py`, `scripts/videogen/serve.py`) | a `systemd-run --user --scope` unit | the wrapper's own stop; on a node without linger, the end of the ssh session |
| a two-node launcher, a download, a long copy | a `systemd-run --user --property=KillMode=process` unit | the unit's stop, which kills only the main process: a helper spawned inside it survives; on a node without linger, the end of the ssh session |
| a TensorFold native rank | a `setsid nohup` process (`scripts/tensorfold_native_two_node.py`) | `timeout` after 4 hours (`LIFETIME_S`) |
| an engine rank in a container recipe | a Docker container | the recipe's stop script |
| a client batch | the client machine, under `setsid nohup` | nothing on the cluster |
| a short wait loop or watcher | the agent's background shell | the low-memory reaper (cause 1); the DGX head's harness also kills a background task after about 30 minutes ([opener, step 0](../hardware/agent-opener-prompt.md#0-step-0-the-loop-is-armed-before-anything-else)) |
| the heartbeat readings | `local-llm-cluster-heartbeat.timer`, a `systemd --user` timer on the head | no agent session; it needs linger (cause 4) |
| the chat heartbeat | the session's `/loop 10m` job, plus the `heartbeat.py --watchdog` background task | cause 1 and cause 3 |

The 30-minute kill was observed on 2026-09-30, but it is not constant. On
2026-11-20 a watchdog task on the head ran for more than 33 minutes and exited
by its own 35-minute check.

### 1. The agent's low-memory reaper kills background shells

*Symptom:* a background `Bash` task stops with "stopped because the system is
running low on memory". The task itself used almost no memory.

*Cause:* Claude Code stops every background shell when `MemAvailable` is low.
On a Spark, a resident two-node model **is** low memory, for hours: the head
idles at 2 to 4.5 GiB. On 2026-09-22 the reaper killed a launcher's shell
wrapper, a boot watcher and a `sleep`-based heartbeat shell. On 2026-10-12 a
TensorFold engine's RAM tier held the head's `MemAvailable` near its 4 GiB
floor (minimum 3.7 GiB). Each dip killed every background task: the batch
watcher, the heartbeat watchdog, a server waiter. Docker containers and
systemd units survived.

*Prevention:* run everything that must outlive the session as a
`systemd-run --user` unit. The reaper does not touch units. Keep only short,
disposable waiters in background shells, and expect to lose them while a model
loads. The opt-out, `CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1`, takes
effect only at Claude Code startup. A session cannot set it for itself.

*State:* from 2026-09-23 to 2026-10-12, the last report on #691, every
two-node launch ran as a unit and no server died this way. The opt-out is not
set (checked 2026-11-20), so the reaper still kills the session's wait loops;
last seen 2026-10-12. Re-run the check by hand when one dies.

### 2. A transient unit kills its leftover processes when it exits

*Symptom:* a recipe's memory guard stops when its launcher finishes. Nothing
reports it.

*Cause:* the #685 recipe started its guard with
`nohup setsid bash scripts/memguard.sh …`. `setsid` leaves the session but not
the cgroup. A plain transient unit has `KillMode=control-group`, so systemd
killed the guard when the launcher unit exited. On 2026-09-22 the head then
sat at ~4.5 GiB `MemAvailable`, with earlyoom stopped and no guard, for about
two minutes.

*Prevention:* launch with `--property=KillMode=process`, or run each helper as
its own unit. Then **confirm that each safety process runs**, not only that it
is configured: after the launcher exits, check the guard's PID or unit on both
nodes, and check `systemctl is-active earlyoom` on both nodes.

*State:* the opener
([§10, step 4](../hardware/agent-opener-prompt.md#steps-on-this-machine))
requires `KillMode=process` for every two-node launch.

### 3. A session's scheduled ticks stop while the session is idle

*Symptom:* a 30-minute heartbeat stays registered but does not fire. On
2026-09-22 four ticks did not fire on time. Two came late, each just after an
operator message. The cluster sat idle and loaded for 27 and 21 minutes with
no one told (#648, #672).

*Cause:* the heartbeat was a session cron job (`/loop 30m`). The session
scheduler does not fire ticks while the session sits idle in the background.
The operator reported this upstream.

*Prevention:* partial. `local-llm-cluster-heartbeat.timer` runs
`scripts/cluster_heartbeat.py` at :07 and :37. It records fresh readings from
both nodes in `~/.local-llm-bench/heartbeat.json` and the journal, and it
posts nowhere. The heartbeat that the operator sees still depends on the
session: a `/loop 10m` job, which is the same `CronCreate` mechanism that
skipped ticks. Three things cover for it
([opener, step 0](../hardware/agent-opener-prompt.md#0-step-0-the-loop-is-armed-before-anything-else)
and [§3a](../hardware/agent-opener-prompt.md#3a-the-heartbeat-every-29-minutes-or-more-idle-included)):
the 10-minute loop, the `heartbeat.py --watchdog` task as a second wake path,
and the Stop hook that refuses to end a tick turn with no heartbeat due. On
2026-11-20 at 22:29 the watchdog fired because session ticks had not recorded
`heartbeat.py --tick`; the loop job itself was fine.

*State, 2026-11-20:* the timer is enabled and active on the head.

### 4. Without linger, the worker's user units die with the ssh session

*Symptom:* a unit started on the worker over ssh stops seconds after it
starts. On 2026-10-12 a `docker pull` unit stopped after 18 s, and a #919
engine rank unit stopped after 10 s (exit 143).

*Cause:* the worker had `Linger=no`. Without linger, the user manager stops
every user unit when the last session for that user closes. That includes a
unit started as `ssh <worker> "systemd-run --user …"`. The prevention for
causes 1, 2 and 3 holds only on a node with linger on.

*Prevention:* `loginctl enable-linger "$USER"` on both nodes (no sudo needed).
A rebuilt node needs it again
([`dgx-cluster-howto.md`](dgx-cluster-howto.md#5-ssh-both-directions-by-the-fabric-names)).

*State, 2026-11-20:* `loginctl show-user "$USER" -p Linger` reads `Linger=yes`
on the head and on the worker.

### A related trap: two recipes' NFS servers on one head

This one did not kill a process; it left one that could not stop. On
2026-09-22, two recipes each ran their own NFS server container on the head,
and only one can hold rpcbind, statd and :2049. One recipe's NFS container
blocked the other's. After the run, the second container could not be
stopped: `rpcbind` was stuck in state D, and it needed a head reboot.

The head now exports the cache with the host's kernel `nfs-server`
([`howto.md`](../hardware/Cortex-X925-128GB-GB10-x2/howto.md#the-most-hidden-tests-passed-deepseek-v41-flash-1017-exl3-eaq-v1-29-bpw-on-tensorfold),
step 1), which holds :2049 and rpcbind; it was active on the head on
2026-11-20. Do not start a recipe's own NFS container beside it.
Stop a recipe's NFS server while its clients are still healthy, before you
remove the worker's mounts.

## Unified memory can OOM-lock the whole box

The 128 GB pool is shared by CPU and GPU, with no separate VRAM ceiling. When
it runs out, the kernel OOM killer cannot reclaim the GPU allocation and kills
small daemons instead, **sshd included**, which locks the machine out.
Recovery is a power cycle (the smart plug does it remotely). Write-ups:
[`2026-09-13-oom-lockup.md`](incidents/2026-09-13-oom-lockup.md) (an uncapped
server took the pool to ~560 MB) and
[`2026-09-17-oom-lockup-earlyoom-swap.md`](incidents/2026-09-17-oom-lockup-earlyoom-swap.md)
(a JIT build took 44 GiB to zero in 61 s while earlyoom waited for a swap
condition that never came).

### The layers

| layer | protects | automatic? | ref |
|---|---|---|---|
| **earlyoom** | the whole box: SIGTERMs the largest runaway, never sshd, systemd or dockerd | **yes**, a system service on both nodes | #362, #458, #700 |
| **server MemAvailable watcher** | stops a `dgx_server.py` scope below 14 GiB; the only per-server net that sees CUDA memory | **yes**, with `dgx_server.py start` | #456 |
| **`MAX_JOBS` cap** | the launch itself, against parallel `nvcc` | **yes**, `MAX_JOBS=3` | #406, #456 |
| **client memory cap** | `run.py`'s agent client: the model-written code a trial runs. Killed at 24 GiB on a bare host (`LOCAL_LLM_CLIENT_MEM_CAP_GIB`); inside the client container the container's own limit applies | **yes**, in `run.py` | #379, #680 |
| **server `MemoryMax`** | **CPU-side memory only.** On GB10 a CUDA allocation is not charged to the cgroup (71,776 MiB on the GPU against 3,762 MiB in the scope's counter), so it is not an OOM net | no: `systemd-run --user --scope -p MemoryMax=NNG -p MemorySwapMax=0 …` | #456 |
| **memory gate** | waits for the pool to free before a trial or launch | opt-in: `run.py --memory-gate-gib N`, `scripts/memory_gate.py` | #360 |
| **OOM watchdog** (`scripts/oom_watchdog.py`, `systemd/local-llm-oom-watchdog.timer`) | after the fact: records each kernel or earlyoom kill, and restarts `ssh` or `earlyoom` if either is down | **yes**, every minute | #459 |
| **SBSA hardware watchdog** (`systemd/watchdog.conf`) | a box that is on but unresponsive; resets it with no power cut | **yes**, 60 s timeout | #459 |

A server launched without these (a bare `vllm serve`, a recipe script) leans
on earlyoom alone. **earlyoom firing on a benchmark means an upstream cap was
wrong**: a lost measurement, not a lost machine.

**Two-node servers: earlyoom is the guard (operator, 2026-11-07, #680 finding
9).** The 14 GiB server watcher covers `dgx_server.py` on :8030 only, and its
floor cannot fit two-node recipes, which idle at 3 to 4 GiB available. No
per-backend floor is set. Instead, `scripts/client_container.py` refuses a
two-node batch unless `systemctl is-active earlyoom` reads `active` on both
nodes (`scripts/lib/node_claims.py`, tested in `tests/test_node_claims.py`).

### earlyoom: the universal net

**Its configuration is code.** `scripts/setup_earlyoom.py` holds the
thresholds, the avoid and prefer patterns and the systemd drop-in. Run it with
no arguments to check the box against the repo (preflight does this every
time). Apply it with:

```sh
sudo -E "$(command -v uv)" run python scripts/setup_earlyoom.py --apply
ps -o args= -C earlyoom; journalctl -u earlyoom     # confirm
```

It writes `/etc/default/earlyoom` on both nodes:

```
EARLYOOM_ARGS="-M 1048576,524288 -s 100,100 -r 60 --avoid
  '(^|/)(systemd|systemd-.*|sshd|dockerd|containerd|earlyoom)$' --prefer
  '(^|/)(vllm|VLLM|pt_main_thread|llama-server|ollama|python[0-9.]*)$'"
```

- **`-M 1048576,524288`** (KiB): SIGTERM at 1.0 GiB available, SIGKILL at
  0.5 GiB (#700). It sits below every cleaner stop: the two-node recipes'
  1.5 GiB memory guards and the 14 GiB server watcher. The older 5% line
  (~6.1 GiB) killed two-node models at rest, which idle at 2.7–4.5 GiB, so
  every run had to stop earlyoom by hand. Disabling it was rejected: it is the
  only net that does not depend on a recipe. The cost is runway: ~1 GiB
  against allocation ramps that can move a GiB in under a second.
- **`-s 100,100`**: earlyoom acts only when memory **and** swap are both
  under their lines. The original `-s 20,10` made it unfireable here: on
  2026-09-17 it logged `mem avail: 0` with swap 91% free and killed nothing
  (#458).
- **`--avoid`** keeps the box reachable; **`--prefer`** names the inference
  servers and the agent's model-written Python (#379).

Validated 2026-09-14 (#362): a 106 GiB Python balloon was SIGTERMed while
sshd and the box stayed reachable.

**Swappiness 10.** `/etc/sysctl.d/99-dgx-oom.conf` sets `vm.swappiness = 10`
on node A. At 60 the unified pool thrashes the 16 GiB swapfile; 10 makes the
kernel prefer reclaim. Node B still runs 60 (the cluster README).

**The hardware watchdog works.** On 2026-09-17 a deliberate panic
(`echo c > /proc/sysrq-trigger`) wedged the box; the firmware reset it in
about 2.5 minutes, with no power cut. The SBSA watchdog acts in two stages,
so the reset lands near twice the timeout. `/sys/class/watchdog/watchdog0/bootstatus`
reads 0 even after a watchdog reset; the outlet trace is the evidence.
Install it with:

```sh
sudo mkdir -p /etc/systemd/system.conf.d
sudo cp systemd/watchdog.conf /etc/systemd/system.conf.d/
sudo systemctl daemon-reexec
```

The file needs its `[Manager]` header, or systemd ignores both settings.

### Rules when serving

- **Cap the server's memory** so a large reserve stays free for the host.
  Uncapped, vLLM sizes its KV cache to leave ~7 GiB free, and any spike then
  locks the box. Set the GPU memory utilization, then *measure* steady-state
  `MemAvailable` after a long prompt before you trust it.
- **Never run a second memory-heavy job beside a resident server.** The
  pytest suite and a served model do not share one pool. Sequence them.
- **Wait for memory to release, not for the PID to exit** (`scripts/memory_gate.py`).

## Trial confinement on Linux (#476)

`run.py` wraps every agent call in `bwrap`. The Mac's `sandbox-exec` deny list
becomes tmpfs covers (an empty directory instead of EPERM), `/tmp` and
`/dev/shm` are private per trial, and the worktree is the one writable path.
The network namespace is **shared** on purpose, because the model server is
outside the sandbox, so "loopback only" is not enforced, and each row records
that (#477).

**It needs the AppArmor profile.** Ubuntu 24.04 sets
`kernel.apparmor_restrict_unprivileged_userns=1`. Without `apparmor/bwrap`
the sandbox fails with `bwrap: setting up uid map: Permission denied`, and
trials run unconfined. Install it:

```sh
sudo cp apparmor/bwrap /etc/apparmor.d/bwrap && sudo apparmor_parser -r /etc/apparmor.d/bwrap
```

## Serving to a remote agent client (#562)

The agent and the harness run on a client machine; the Spark's pool holds only
the server. The client's spec sheet is under `hardware/` (tier
`remote-client`). The full routine, with the client container, is in
[`hardware/agent-opener-prompt.md`](../hardware/agent-opener-prompt.md#one-arm-start-to-finish).

1. On the head, start the server bound to `0.0.0.0`, then write its facts:

   ```sh
   uv run python scripts/server_facts.py --backend <name> --out /tmp/server-facts.json \
       [--cluster-peer <worker fabric name>]   # two-node servers
   ```

   The facts carry the server's hardware and engine provenance (including
   `server_argv`), which the client cannot read. `--cluster-peer` checks the
   worker and names the cluster, or refuses (#647).
2. Copy the facts file to the client, and start the run there with
   `scripts/client_container.py --server <head> --facts <file> -- --backend <name> …`.
   The client's OpenCode config names the head; it never goes in this repo.

What remote mode changes (`benchmarks/agent/remote.py`):

- Each backend URL's loopback host becomes the server host.
- The memory gates read the **Spark's** `MemAvailable` from its node_exporter
  (`:9100`), not the client's.
- Rows describe the Spark. The client's hardware goes under
  `env.client_machine`, and `env.topology` is `"remote"`.
- The run lock is the client's, so for a two-node batch (facts with
  `env.cluster.nodes = 2`) `client_container.py` also takes a
  `machine_claim.py` session claim on **both** nodes over ssh before the first
  trial (#680 findings 5 and 6). It holds them for the batch and releases
  them on every exit path, SIGTERM included. If either claim fails, it refuses
  the batch and releases the claim it took. `machine_state.py` on either node
  then reads BUSY mid-batch. The client needs the node names
  (`LOCAL_LLM_DGX_HEAD`, `LOCAL_LLM_DGX_WORKER`, or the hosts file above), ssh
  access to both nodes, and the agent identity variables.

## FlashInfer's autotune cache is kept across starts (#1514)

FlashInfer picks some kernels by timing them at engine start. The result
stayed in the container, so every start paid the cost again, and two starts
could pick different kernels. The #1503 smoke (2026-11-26) estimated HY4
Flash's SM120 sparse-MLA autotune at about 10.5 minutes of an 803 s start.
Measured on 2026-11-27, one start each, with the cache kept: engine init took
306.41 s with an empty cache and 134.87 s with the cache, and the first
completion came 433 s and 273 s after launch. The second start loaded the
cache on both nodes and tuned nothing.

`scripts/vllm_two_node.py start` keeps the result on each node:

- The host directory is `~/.cache/local-llm/flashinfer-autotune/<key>`. The
  container sees it at `/flashinfer-autotune`, and
  `VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR` points there.
- The key is the served model name and a hash of the head's image ID, the
  checkpoint, the `vllm serve` arguments and the `--env` pairs. A new image, a
  rebuilt image under the same tag, or a changed flag gets a new directory and
  tunes again.
- Before the start, the launcher compares the image's layers on both nodes
  (`docker image inspect --format '{{json .RootFS.Layers}}'`). It refuses when
  they differ or the worker lacks the image. The image IDs cannot be compared:
  the head's overlay2 store reports the config digest, and the worker's
  containerd store reports the manifest digest. With different images, both
  nodes re-tune at every start and the worker's sparse-MLA load fails silently.
- The launcher then creates the directory on both nodes. It refuses a
  directory that it cannot create or write, and an image that the head does
  not have.
- Both containers get labels with the key and, per node, `found` or `new`.
  `found` means a finished `*.json` existed before the start. `server_facts.py`
  reads the labels into `env.autotune_cache`, and every row carries it. One key
  does not prove one set of kernels: after a failed tune leaves a partial
  file, the next start tunes the missing shapes, and a file on one node only
  makes both nodes tune again.
- `--no-autotune-cache` turns the cache off (`env.autotune_cache` is
  `{"mode": "off"}`), and autotune runs at every start, as before.
- Serve arguments that turn autotune off (`--no-enable-flashinfer-autotune`,
  `-O0`) get no cache, and `env.autotune_cache` is
  `{"mode": "autotune-disabled"}`. Pass them only when the model card does, as
  #1011's E192 card does. On an arm whose card leaves autotune on, do not use
  them: they change the kernels from the card's.
- `stop` gives the files back to your user before it removes the containers
  (the containers write them as root). If a container is already dead,
  `stop` does the chown in a throwaway container of the same image. If that
  also fails, it logs an error, and you can run the same command by hand on
  that node:

  ```sh
  docker run --rm --entrypoint chown \
      -v ~/.cache/local-llm/flashinfer-autotune/<key>:/c <image> -R "$(id -u):$(id -g)" /c
  ```

  To force a new tune, stop the server and delete that key's directory on
  both nodes.

A cache key is not part of the pooling guard (`results.pool_compatible`).
Autotune keeps the fastest tactic it measures, so two keys differ by about the
timing noise. Rows from before #1514 have no key. Split by `env.autotune_cache`
in an analysis that needs one kernel choice.

## NVMe interrupt coalescing is off (#884)

NVIDIA's `nvidia-nvme-interrupt-coalescing.service` is **masked** on both
nodes, and NVMe feature 0x08 reads 0. With it on, a single 4K read takes
197.6 µs against 53.5 µs. The cluster heartbeat flags a node that reads
non-zero. The test and how to reverse it:
[`dgx-spark-nvme-coalescing.md`](dgx-spark-nvme-coalescing.md).

## A locally built image is checked on both nodes (#1515, #1516)

A backend with `image_source = "local"` serves an image built on the head,
such as a nightly plus one PR's files (`scripts/vllm_pr_overlay.py`). No
registry has it, and `vllm.__version__` reads the same as the base nightly's.
So the image itself is checked, on each node. `scripts/node_image.py` holds
the shared reader and the rules; the launcher, the daily gate and `run.py`
all use it.

- **The daily gate** (`scripts/preflight_daily.py`) no longer asks a registry
  about a local image. Before #1515 it refused every local backend every day.
  Now it checks that the image exists on the head, and on the worker for a
  `gb10-spark-x2` backend. It checks that both nodes hold the same
  `RootFS.Layers`. Where the image has the label `org.local-llm.vllm-pr-sha`
  (the overlay writes it) and a backend that uses the image declares
  `recipe_pin`, the label must start with the pin. An image without the label
  (the inkling2 image) cannot be checked against its pin: the gate logs
  `unlabeled: pin <pin> not checked` as a WARNING, and does not refuse. A
  worker that ssh cannot reach is reported as unreachable, not as a missing
  image. A registry image keeps the registry check.
- **A vLLM container row carries `env.serving_image`** when the head finds the
  serving vLLM container (`docker ps` on the head, matched on the served model
  name). It comes from the server only (`remote.CLIENT_ENGINE_KEYS`):

  ```json
  {"rank0": {"id": "sha256:...", "layer_count": 42, "layers_sha256": "...",
             "labels": {"org.local-llm.vllm-pr-sha": "6ca1fee..."}},
   "rank1": {...}}
  ```

  `id` is the node's own image ID. The two stores give one image two IDs, so
  compare `layers_sha256` (a sha256 of the ordered layer list) across nodes.
  `labels` holds only the `org.local-llm.*` labels. A one-node server carries
  `rank0` only. When the head's command has `--nnodes 2`, `run.py` also reads
  the worker's rank-1 container over the fabric.
- **Some launchers give no record.** A server that the head cannot see as a
  vLLM container (a `bash /start.sh` launcher whose container command hides
  `vllm serve`, or a server started with `docker exec`) gets no
  `serving_image`, so nothing is checked. When a `gb10-spark-x2` backend has
  no record with `rank1`, `run.py` logs a WARNING that says so.
- **`run.py` refuses the run** (and `server_facts.py` refuses to write
  facts) when:
  - a node's image cannot be read. The refusal names the cause: ssh to the
    worker failed, no rank-1 vLLM container, or a failed `docker inspect`;
  - the two nodes' layers or `org.local-llm.*` labels differ;
  - a node's PR-sha label does not start with the backend's `recipe_pin`.

  Restart the server from the right image on both nodes. To run anyway, pass
  `--no-image-check-reason TEXT` (to `run.py` or `server_facts.py`). Every row
  then carries `env.serving_image_unchecked`: the reason and each refusal.

Rows from before #1516 have no `env.serving_image`.

## Packages added beyond the base image

| what | how | version | for |
|---|---|---|---|
| earlyoom | `sudo apt-get install earlyoom` | 1.7-2 | the OOM net (#362) |
| fio | `sudo apt-get install fio` | 3.36 | `scripts/nvme_coalescing_ab.py` (#884) |
| Rust | rustup: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh -s -- -y --default-toolchain stable --profile minimal` | 1.99.1 in `~/.cargo/bin`, both nodes | engine builds (#341, [`flare-setup.md`](flare-setup.md)) |

**Do not `apt install rustc cargo`.** Ubuntu noble ships 1.75.0, which cannot
parse a version-4 `Cargo.lock` (cargo 1.78 or later is needed).

## Matching a process by name will bite you

Five incidents in one session came from `pkill -f` matching **the shell that
quoted the pattern**, once inside the idle check written to catch the others.
Bracketing (`[p]attern`) does not help when a parent shell's command line
holds the words. Use `scripts/unitctl.py`, a recorded pid, or a port. When you
walk `/proc`, exclude your own process ancestry.

## Two guards that will stop you, correctly

- **Commits are refused while a benchmark holds the run lock.** A commit moves
  `HARNESS_HEAD` mid-run and splits `harness_dirty` across the arms of a
  comparison.
- **`pytest` is refused during a run.** A suite lands on some arms and not
  others; three runs were voided that way on 2026-09-06.

Both have overrides. A measurement taken with one is not publishable.
