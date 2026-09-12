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
uv run python scripts/machine_health.py check --for server   # or --for run
uv run python scripts/machine_health.py confirm --pid N --log FILE
```

`check` refuses a launch into a known-bad state: dirty tree (rows would be
stamped `harness_dirty` and may not be published), lock held, a **stale** lock
whose pid is gone, or a port already serving — naming the model it serves,
because **reusing that server is the fix**. A busy port blocks a *server* and is
fine for a *run against it*; `--for` says which.

`confirm` answers the other half: **a spawned process is not a working one.** On
2026-09-12 a second vLLM was launched onto a bound port, died instantly with
`EADDRINUSE`, and its wait loop then polled for a readiness line that would never
appear — the GPU sat at 9 W for twenty-five minutes while a healthy server was
already listening.

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
