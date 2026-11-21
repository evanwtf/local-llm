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

Four things have killed processes that an agent started on the cluster. Three
removed a safety process with no error shown. Each one is now prevented, but
only if the launch follows the rules below.

| process | where it lives | what can kill it |
|---|---|---|
| a server, a download, a long copy, a client batch, a memory guard | its own `systemd-run --user --property=KillMode=process` unit | the unit's own stop; on a node without linger, the end of the ssh session |
| a short wait loop or watcher | the agent's background shell | the agent's low-memory reaper, at any time while a model is resident |
| an engine rank in a recipe | a Docker container | the recipe's stop script, or earlyoom |
| the heartbeat cadence | `local-llm-cluster-heartbeat.timer`, a `systemd --user` timer on the head | nothing that an agent session does |

### 1. The agent's low-memory reaper kills background shells

*Symptom:* a background `Bash` task stops with "stopped because the system is
running low on memory". The task itself used almost no memory.

*Cause:* Claude Code stops every background shell when `MemAvailable` is low.
On a Spark, a resident two-node model **is** low memory, for hours: the head
idles at 2 to 4.5 GiB. On 2026-09-22 the reaper killed a launcher's shell
wrapper, a boot watcher and a `sleep`-based heartbeat shell. On 2026-10-12 a TensorFold
engine's RAM tier dipped to 3.7 GiB, and each dip killed the batch watcher.
Docker containers and systemd units survived each time.

*Prevention:* run everything that must outlive the session as a
`systemd-run --user` unit. The reaper does not touch units. Keep only short,
disposable waiters in background shells, and expect to lose them while a model
loads. The opt-out, `CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1`, takes
effect only at Claude Code startup. A session cannot set it for itself.

*State, 2026-11-20:* every launch since 2026-09-23 runs as a unit, and no
server has died this way since. The reaper still kills the session's own wait
loops; re-run the check by hand when one dies.

### 2. A transient unit kills its leftover processes when it exits

*Symptom:* a recipe's memory guard stops when its launcher finishes. Nothing
reports it.

*Cause:* the #685 recipe starts its guard with
`nohup setsid bash scripts/memguard.sh …`. `setsid` leaves the session but not
the cgroup. A plain transient unit has `KillMode=control-group`, so systemd
killed the guard when the launcher unit exited. On 2026-09-22 the head then
sat at ~4.5 GiB `MemAvailable` with no guard for about two minutes.

*Prevention:* launch with `--property=KillMode=process`, or run each helper as
its own unit. Then **confirm that each safety process runs**, not only that it
is configured: after the launcher exits, check the guard's PID or unit on both
nodes, and check `systemctl is-active earlyoom` on both nodes.

*State, 2026-11-20:* every launch uses `KillMode=process`
([`agent-opener-prompt.md`](../hardware/agent-opener-prompt.md), step 4).

### 3. A session's scheduled ticks stop while the session is idle

*Symptom:* a 30-minute heartbeat stays registered but does not fire. On
2026-09-22 four ticks did not fire on time. Two came late, each just after an
operator message. The cluster sat idle and loaded for 27 and 21 minutes with
no one told (#648, #672).

*Cause:* the heartbeat was a session cron job (`/loop 30m`). The session
scheduler does not fire ticks while the session sits idle in the background.
The operator reported this upstream.

*Prevention:* the cadence does not depend on the session.
`local-llm-cluster-heartbeat.timer` runs `scripts/cluster_heartbeat.py` at :07
and :37 and records fresh readings from both nodes. The session runs its own
recurring loop for the chat update
([`AGENTS.md`](../AGENTS.md)), and the timer does not depend on it.

*State, 2026-11-20:* the timer is enabled and active on the head.

### 4. Without linger, the worker's user units die with the ssh session

*Symptom:* a unit started on the worker over ssh stops seconds after it
starts. On 2026-10-12 a `docker pull` unit stopped after 18 s, and a #919
engine rank stopped after 10 s (exit 143).

*Cause:* the worker had `Linger=no`. Without linger, the user manager stops
every user unit when the last session for that user closes. That includes a
unit started as `ssh <worker> "systemd-run --user …"`. The prevention for
causes 1 and 2 holds only on a node with linger on.

*Prevention:* `loginctl enable-linger "$USER"` on both nodes (no sudo needed).
A rebuilt node needs it again
([`dgx-cluster-howto.md`](dgx-cluster-howto.md#5-ssh-both-directions-by-the-fabric-names)).

*State, 2026-11-20:* `loginctl show-user evan -p Linger` reads `Linger=yes` on
the head and on the worker.

### A related trap: two recipes' NFS servers on one head

This one does not kill a process; it leaves one that cannot stop. Two recipes
each run their own NFS server container on the head, and only one can hold
rpcbind, statd and :2049. On 2026-09-22 one recipe's NFS container blocked the
other's. After the run, the second container could not be stopped:
`rpcbind` was stuck in state D, and only a head reboot cleared it. Run one
recipe's NFS server at a time. Stop it while its clients are still healthy,
before you remove the worker's mounts.

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

## NVMe interrupt coalescing is off (#884)

NVIDIA's `nvidia-nvme-interrupt-coalescing.service` is **masked** on both
nodes, and NVMe feature 0x08 reads 0. With it on, a single 4K read takes
197.6 µs against 53.5 µs. The cluster heartbeat flags a node that reads
non-zero. The test and how to reverse it:
[`dgx-spark-nvme-coalescing.md`](dgx-spark-nvme-coalescing.md).

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
