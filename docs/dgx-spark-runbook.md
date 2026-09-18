# Operating the DGX Spark

The `m5max-runbook.md` analogue for `Cortex-X925-128GB-GB10`. Machine facts are
in [`hardware/Cortex-X925-128GB-GB10/README.md`](../hardware/Cortex-X925-128GB-GB10/README.md);
this is how to drive it without wasting its time.

## Is the machine working? Power, not utilization

**`nvidia-smi --query-gpu=utilization.gpu` is wrong in both directions here.**
It reports occupancy over time — the fraction of a sample window in which any
kernel was resident — so it reads **96% while a model is merely loading**, and
**0% mid-trial** whenever an agent is running a tool or the test oracle instead
of decoding. Deciding on it calls a loading server busy and a working machine
idle, and both errors happened on 2026-09-12.

Measured on this GB10:

| state | power | temp |
|---|---|---|
| idle | flat **9–11 W** | 37 °C |
| real work | **35–83 W** | 53–63 °C |

A 4× gap with no overlap, so **power decides**. Temperature corroborates but
lags tens of seconds, so it is reported and never used to decide.

### The window matters more than the statistic

**p90 over 15 minutes**, threshold 25 W. Over five minutes a *running* agent
trial reads p50 10 W and p90 19 W — indistinguishable from idle, because most
samples fall in the gaps between decodes; only p99 at 82 W betrays it. Fifteen
minutes is long enough that a live run cannot hide in the gaps and short enough
to notice a machine that stopped.

Validated against stretches whose state was known:

```
now        76.6 W   running      7h ago     11.4 W   idle
1h ago     77.9 W   running     11h ago      9.4 W   idle
```

```sh
uv run python scripts/gpu_utilization.py state     # BUSY / WORKING / LOADING / IDLE / STALLED
```

Only **IDLE** and **STALLED** are defects. `LOADING` and `WORKING` are the
machine between phases, and treating them as faults is how a correct action gets
interrupted.

### Loading a checkpoint is disk-bound, not GPU-bound

Power stays at the **idle floor** for the whole shard read, so low power alone
cannot separate loading from idle. Measured 2026-09-12 while an 84 GiB GGUF
loaded:

| | disk read | GPU power |
|---|---|---|
| before | ~0.01 MB/s | 10 W |
| **during** | **499 MB/s** | **10 W** |
| after | ~0.05 MB/s | 10 W |

The separating fact is that **a server process exists and does not answer yet**.
`machine_state` uses exactly that.

## Prometheus, via gcx

Prometheus runs on lunix and already scrapes this machine. The datasource UID is
**`uMatQbvMk`**; `docs/dcgmi.md` covers dcgmi itself.

### Build llama.cpp from the evanwtf fork

On the DGX Spark, build and run llama.cpp from
[`evanwtf/llama.cpp`](https://github.com/evanwtf/llama.cpp), not directly from
`ggml-org/llama.cpp`. The fork carries the Prometheus `model` label required to
separate metrics from different models (#436); it was verified both at the
local `/metrics` endpoint and after the `dgx.internal:8020` Prometheus scrape.
The build must descend from the fork's `master` branch and include at least
[`171824a`](https://github.com/evanwtf/llama.cpp/commit/171824a31d13b7320a7fdc72c3539d6095b6eb0).

The existing checkout may keep `ggml-org/llama.cpp` as an additional upstream
remote. This example assumes the fork remote is named `fork`. Before a DGX Spark
build, fetch it and verify the selected revision contains its current `master`:

```sh
git fetch fork master
git merge-base --is-ancestor fork/master HEAD
```

An upstream-only revision is not the DGX Spark operational build even when it
is newer. Reconcile upstream changes into the fork first, then build the forked
revision so model-labeled telemetry remains present.

**The token is not in a non-interactive shell's environment.** It lives at
`~/.config/gcx/token` and is exported as `GRAFANA_TOKEN` by `~/.bashrc`, which
only interactive login shells source. Every `gcx` call fails `Unauthorized`
otherwise while working fine in the operator's shell:

```sh
export GRAFANA_TOKEN="$(cat ~/.config/gcx/token)"
```

**`gcx config check` is not an auth check.** It reports `Connectivity: online`
and a Grafana version with no credential at all. `gcx datasources list` is the
real test.

Series worth knowing, all labelled `instance="dgx.internal:<port>"`:

| series | port | use |
|---|---|---|
| `DCGM_FI_DEV_POWER_USAGE` | 9400 | the idle/busy signal above |
| `node_disk_read_bytes_total` | 9100 | measures a model load directly |
| `vllm:time_to_first_token_seconds` | 8030 | prefill, which dominates agent wall time (#14) |
| `vllm:num_requests_running` | 8030 | queue depth — saturation vs queueing |
| `vllm:prefix_cache_hits_total` | 8030 | cache effectiveness (#330) |
| `llamacpp:*` | 8020 | the same, once `--metrics` is passed |

Engine counters are **per-server** and reset to zero on restart, so use
`rate()`/`increase()` rather than raw deltas; a restart looks like a counter
reset, not a drop to idle.

## Ports and binding

| port | engine | note |
|---|---|---|
| 8000 | ds4 | vLLM's *default* port — taken here, so vLLM must be moved |
| 8020 | llama.cpp | `--metrics` is **off by default**; pass it |
| 8030 | vLLM | metrics are on by default, same port as the API |

**Bind `0.0.0.0`.** Loopback binding silently broke monitoring for weeks:
Prometheus had `vllm dgx.internal:8030` and `llamacpp dgx.internal:8020`
configured and both sat at `up=0`, because nothing they pointed at was
reachable. *A monitoring system reporting nothing looks exactly like one
reporting nothing wrong.* The machine is `dgx.internal` / 192.168.1.180.

**`scripts/local_agent.py` stays on `127.0.0.1`** — it is what
`RECOMMENDATIONS.md` tells a stranger to run, and their network is not this LAN.

## Before and after a launch

```sh
uv run python scripts/machine_health.py boot                  # rebooted since last check?
uv run python scripts/machine_health.py check --for server   # or --for run
uv run python scripts/machine_health.py confirm --pid N --log FILE
```

`boot` answers "did this machine reboot between turns" -- it compares the kernel
boot id against the last-seen value in `~/.local-llm-bench/last-boot-id` (durable
across sessions and scratchpad wipes) and reports the reboot once, with uptime.
Run it at the top of the health-check loop: a reboot wipes every server, the run
lock's pid, and the session scratchpad, so without it a fresh boot reads as a
baffling run of "server died" and "stale lock" findings instead of one line that
explains them all. It exits 2 on a detected reboot.

Long-lived servers use one recorded lifecycle interface; do not find or stop
them by scanning process command lines:

```sh
uv run python scripts/dgx_server.py start vllm --model MODEL --memory-max 96G -- \
    ~/venvs/vllm/bin/vllm serve MODEL --gpu-memory-utilization 0.66
uv run python scripts/dgx_server.py status vllm
uv run python scripts/dgx_server.py stop vllm
```

The profiles own the LAN bind and fixed port: vLLM `:8030`, llama.cpp/ds4
`:8020`, vLLM-Omni `:8041`, clip viewing `:8042`, and Ollama `:11434`.
llama.cpp metrics are enabled by the wrapper; vLLM exposes metrics by default;
current ds4 has no metrics option. Heavy profiles require `--memory-max`; the
wrapper also sets zero swap, records the PID and scope under
`~/.local-llm-bench/dgx-servers`, and waits for the port and unified-memory pool
to clear on stop.

**Every heavy launch also gets a MemAvailable watcher and a JIT build cap
(#456).** `start` spawns a detached `dgx_server.py watch <name>` that polls
`MemAvailable` once a second and stops the scope below **14 GiB**
(`--mem-floor-gib`, `LOCAL_LLM_MEM_FLOOR_GIB`; `0` disables it), and it sets
**`MAX_JOBS=3`** in the scope environment (`--max-jobs`,
`LOCAL_LLM_DEFAULT_MAX_JOBS`; an explicit `MAX_JOBS=` in the command wins).
`status` reports `mem_available_gib` and `watcher_live`. The watcher runs
*outside* the scope it watches, so the stop it issues does not kill it first,
and an unreadable `/proc/meminfo` never stops a server.

## Unified memory can OOM-lock the whole box — cap the server

The 128 GB pool is shared between CPU and GPU with no separate VRAM ceiling, so
an oversized GPU allocation lands on host RAM. When the pool is exhausted the
kernel's global OOM killer cannot reclaim the GPU allocation and kills small
userspace daemons instead — **including sshd** — which locks the machine out.
Recovery is the **physical power button** — or, since the smart plug, a remote
power cycle; on 2026-09-13 this happened six times, and again on 2026-09-17.
Write-ups: [`2026-09-13-oom-lockup.md`](incidents/2026-09-13-oom-lockup.md) (an
uncapped server drove the pool to ~560 MB and the kernel killed sshd) and
[`2026-09-17-oom-lockup-earlyoom-swap.md`](incidents/2026-09-17-oom-lockup-earlyoom-swap.md)
(a JIT kernel build took 44 GiB to zero in 61 s while earlyoom sat idle, because
its swap threshold was never met).

### The layers (defense in depth)

Four protections stack. The first two are **always on**; the rest are launch
discipline that reduce how often the always-on nets have to fire.

| layer | protects | automatic? | ref |
|---|---|---|---|
| **earlyoom** | the **whole box** — SIGTERMs the largest runaway (inference server / model-written python), never sshd/systemd/dockerd | **yes** — systemd service, enabled at boot; **memory-only since #458**; its config is `scripts/setup_earlyoom.py`, and preflight reports drift from it | #362 / #458 |
| **server MemAvailable watcher** | the model server — stops its scope below 14 GiB available, which is the only reading that sees a CUDA allocation here | **yes** — `dgx_server.py start` spawns it | #456 |
| **`MAX_JOBS` cap on JIT builds** | the launch itself — an unset `MAX_JOBS` runs ~22 concurrent `nvcc` jobs during warmup, on top of the loaded weights | **yes** — `MAX_JOBS=3` unless the command sets it | #406 / #456 |
| **client memcap** | `run.py`'s agent-client phase — the model-written code a trial executes; killed locally at 24 GiB (`LOCAL_LLM_CLIENT_MEM_CAP_GIB`, 0 disables) | **yes** — built into `run.py` | #379 / #380 |
| **server `MemoryMax`** | **CPU-side memory only.** It does **not** bound CUDA allocations on GB10 — measured 71,776 MiB on the GPU against 3,762 MiB in the scope's counter — so it is not an OOM net for a model server | no — set it at launch: `systemd-run --user --scope -p MemoryMax=NNG -p MemorySwapMax=0 …` | #362 / #456 |
| **memory-gate** | waits for the pool to actually free before each trial / next launch | opt-in — `run.py --memory-gate-gib N`, `scripts/memory_gate.py` | #360 |
| **`machine_health check --for server`** | refuses a launch while a departing server's memory is still held | run it before launching | #360 |
| **OOM watchdog** (`scripts/oom_watchdog.py`, `systemd/local-llm-oom-watchdog.timer`) | **after** the fact — records every kernel/earlyoom kill where the journal cannot rotate it away, and restarts `ssh`/`earlyoom` if either is inactive | **yes** — installed 2026-09-17, runs every minute | #459 |
| **SBSA hardware watchdog** | a box that is powered on but unresponsive — the only layer that can act when nothing schedulable is left | **yes** — `systemd/watchdog.conf` installed to `/etc/systemd/system.conf.d/`, 60 s timeout; **tested**: a deliberate panic was reset in ~2.5 min with no power cut | #459 |

**Trial confinement on Linux (#476).** `run.py` wraps every agent invocation in
`bwrap`: the deny list the Mac expresses as `sandbox-exec` rules becomes tmpfs
covers (an empty directory instead of EPERM), `/tmp` and `/dev/shm` are private
per trial, and the worktree is the one writable path. Verified 2026-09-17 —
inside the sandbox `~/bench-solutions` shows 0 of its 516 entries, `tasks.toml`
is unreadable, `/tmp` holds 1 entry against the host's 580, and the 9 CUDA
device nodes are still there.

**It needs the AppArmor profile.** Ubuntu 24.04 sets
`kernel.apparmor_restrict_unprivileged_userns=1`, so without
`apparmor/bwrap` installed to `/etc/apparmor.d/` the sandbox fails with
`bwrap: setting up uid map: Permission denied` and trials run unconfined. The
profile grants `userns` to that one binary, the same shape as the shipped
`ch-run` and `crun` profiles. Install with
`sudo cp apparmor/bwrap /etc/apparmor.d/bwrap && sudo apparmor_parser -r /etc/apparmor.d/bwrap`.
The network namespace is **shared** on purpose — the model server is outside the
sandbox on `:8030` — so "loopback only" is not enforced here, and each row
records that (#477).

**earlyoom's configuration is code.** `scripts/setup_earlyoom.py` holds the
thresholds, the avoid/prefer regexes and the systemd drop-in; run it with no
arguments to check the box against the repo (preflight does this on every
invocation), and `sudo -E uv run python scripts/setup_earlyoom.py --apply` to
write it. The setting that matters is `-s 100,100`: earlyoom acts only when
memory **and** swap are both under their thresholds, so the original `-s 20,10`
made it unfireable here — 2026-09-17 06:48 logged `mem avail: 0` with swap
91.45% free and killed nothing (#458).

**Both #459 layers are live, and the hardware watchdog is proven.** On
2026-09-17 a deliberate kernel panic (`echo c > /proc/sysrq-trigger`) at
22:39:30 left the box wedged at a flat ~30 W; the firmware reset it at
22:41:58 (the outlet dipped to 11 W and never to zero, so it was not a power
cut) and the kernel was booting by 22:42:21. About 2.5 minutes, not 60 s: the
SBSA watchdog acts in two stages, and the reset lands near twice the timeout
after the last pet. `/sys/class/watchdog/watchdog0/bootstatus` reads **0** even
after a watchdog reset — this driver does not report the cause — so the outlet
trace and a previous boot with no shutdown record are the evidence. Install
with `sudo mkdir -p /etc/systemd/system.conf.d && sudo cp systemd/watchdog.conf
/etc/systemd/system.conf.d/ && sudo systemctl daemon-reexec`; the file needs
its `[Manager]` header, or systemd ignores both settings.

**earlyoom is the universal net** — it protects every process on the box
(benchmarks, model servers, the H3 video pipeline), not only `run.py`.
**Validated 2026-09-14 (#362):** a controlled 106 GiB `python` balloon was
SIGTERM'd at the 10% line (`earlyoom: sending SIGTERM to … "python3" … VmRSS
108256 MiB`) while sshd and the box stayed fully reachable. The `run.py` client
memcap closes the specific hole behind the 2026-09-13 lockup — model-written code
growing unbounded in the agent-client phase (#379).

**`MemoryMax` is not the per-server net it looks like (#456).** On GB10 a CUDA
allocation is not charged to the process's cgroup, so the ceiling bounds only
the few GiB of CPU-side memory. The nets that see the GPU side are the
MemAvailable watcher (per server, since #456) and earlyoom (box-wide). The
2026-09-17 lockup is the evidence: a server under `MemoryMax=108G` took the
pool to zero, and earlyoom could not fire because its swap condition was never
met (#458).

The per-server `MemoryMax` and the memory-gate are *discipline*, not enforced: a
server launched without the scope (a bare `vllm serve`, the published
`local_agent.py`) has no per-server ceiling and leans entirely on earlyoom.
**earlyoom firing on a benchmark means an upstream cap was wrong** — a lost
measurement, not a lost machine. Full rationale and the syslog evidence: #390.

Non-negotiable rules when serving here:

- **Cap the server's memory** so ~30–40 GiB stays free for the host. Uncapped,
  vLLM sizes its KV cache to leave only ~7 GiB free, and any spike then locks
  the box. Set `--gpu-memory-utilization` low enough that steady-state
  `MemAvailable` holds a large reserve, and *measure* it before trusting it.
- **Never run a second memory-heavy job beside a resident server** — the pytest
  suite and a served vLLM do not coexist in one pool. Sequence them.
- **Wait for memory to release, not for the PID to exit,** before the next
  launch (`scripts/memory_gate.py`), and gate each trial with
  `run.py --memory-gate-gib N`.

**The safety net: `earlyoom` + low swappiness (#362).** Prevention above is the
first line; `earlyoom` is the backstop that keeps the box *reachable* when
prevention fails, so a mistake costs one killed process, not a physical reboot.
Installed and enabled as a system service (2026-09-14):

- `/etc/default/earlyoom` — `EARLYOOM_ARGS="-m 10,5 -s 20,10 -r 60 --avoid
  '(^|/)(systemd|systemd-.*|sshd|dockerd|containerd|earlyoom)$' --prefer
  '(^|/)(vllm|VLLM|pt_main_thread|llama-server|ollama|python[0-9.]*)$'"`. It
  SIGTERMs at 10% available memory (~12 GiB) — before the kernel thrashes the
  box unreachable — and **avoids** sshd/systemd/dockerd (reachability) while
  **preferring** the inference servers and the agent's model-written python (the
  #379 runaway). Confirm with `ps -o args= -C earlyoom` and
  `journalctl -u earlyoom`.
- `/etc/sysctl.d/99-dgx-oom.conf` — `vm.swappiness = 10` (was 60). Unified
  LPDDR5X thrashes the 16 GiB swapfile hard at 60; 10 makes the kernel prefer
  reclaim (and lets earlyoom act) over swapping anon pages.

This does not replace capping the server — a SIGTERM'd run is a lost measurement,
just not a lost machine. `earlyoom` firing on a benchmark means the cap was wrong.


`check` refuses a launch into a known-bad state: dirty tree (rows would be
stamped `harness_dirty` and may not be published), lock held, a **stale** lock
whose pid is gone, or a port already serving — naming the model it serves,
because **reusing that server is the fix**. A busy port blocks a *server* and is
fine for a *run against it*; `--for` says which.

It also refuses a *server* launch when a large amount of memory is held but
**nothing is answering on any port** — a departing server that freed its port but
not its ~115 GiB. This is the third unified-memory trap: a server's memory
outlives its PID, so waiting for the PID to vanish is not waiting for the pool to
free. Launch then, and vLLM profiles the still-occupied pool, sizes an oversized
KV cache, and is OOM-killed at startup — on 2026-09-13 it chose a KV cache
*larger* than the identically-shaped server it was replacing and died once both
were resident (#360). The threshold is `LOCAL_LLM_MEM_SETTLE_MAX_GIB` (default
24; idle baseline is a few GiB). `scripts/memory_gate.py` is the matching *wait*;
`check` is the *refusal* if you skip it.

`confirm` answers the other half: **a spawned process is not a working one.** On
2026-09-12 a second vLLM was launched onto a bound port, died instantly with
`EADDRINUSE`, and its wait loop then polled for a readiness line that would never
appear — the GPU sat at 9 W for twenty-five minutes while a healthy server was
already listening.

## Toolchains installed on this box (provisioning record)

Packages added to `spark-231e` beyond the base image — what, how, and the version, so a rebuild is reproducible:

| what | how | version | for |
|---|---|---|---|
| **earlyoom** | `sudo apt-get install earlyoom` | 1.7-2 | the OOM safety net (#362); config + tuning in the unified-memory section above |
| **Rust (via rustup)** | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh -s -- -y --default-toolchain stable --profile minimal` | cargo/rustc **1.98.1** in `~/.cargo/bin` (user-local, no sudo) | building the Layr-Labs cudafast ds4 CUDA engine (#341) |

**Do NOT `apt install rustc cargo` for the cudafast build.** Ubuntu noble ships **1.75.0**, which cannot parse that repo's version-4 `Cargo.lock` (`lock file version 4 requires -Znext-lockfile-bump`; v4 needs cargo ≥1.78). It was tried first and failed at the Rust adapter after the ds4 CUDA/C objects had already built — use rustup (above). `nvcc` / CUDA 13.0.88 and the vLLM venv (`~/venvs/vllm`, with its `ninja`/`nvcc` PATH gotcha) were already present and were not reinstalled.

## Matching a process by name will bite you

Five separate incidents in one session came from `pkill -f` matching **the shell
that quoted the pattern**, including once inside the idle check written to catch
the others. Bracketing (`[p]attern`) does not help when a parent shell's command
line contains the words. Use `scripts/unitctl.py`, a recorded pid, or a port —
and when walking `/proc`, exclude your own process ancestry.

## Two guards that will stop you, correctly

- **Commits are refused while a benchmark holds the run lock.** Committing moves
  `HARNESS_HEAD` mid-run and splits `harness_dirty` across the arms of a
  comparison.
- **`pytest` is refused during a run.** A suite lands on some arms and not
  others; three runs were voided that way on 2026-09-06. Both have overrides.
  Using one means the measurement is not publishable.
