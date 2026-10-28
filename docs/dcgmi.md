# dcgmi on the DGX Sparks

DCGM runs in a container named `dcgm` (`nvidia/dcgm`, port 5555) on both
cluster nodes, beside `dcgm-exporter` (port 9400), which feeds Prometheus. The
compose files are in
[`evandhoffman/dgx-utils`](https://github.com/evandhoffman/dgx-utils)
([`dgx-cluster-howto.md`](dgx-cluster-howto.md), step 8). To decide whether a
Spark is busy, read power, not utilization
([`dgx-spark-runbook.md`](dgx-spark-runbook.md)).

## Read some fields

`dmon` polls every 1000 ms by default. `-c N` stops after N samples:

```sh
docker exec dcgm dcgmi dmon -e 150,155,157,203 -c 5
```

```
#Entity         TMPTR        POWER             POWINST           GPUTL
ID               C            W                 W
GPU 0           47           7.307             10.857            0
GPU 0           47           10.989            10.842            0
```

## List every field

```sh
docker exec dcgm dcgmi dmon -l      # long name, short name, field ID
```

## Fields worth knowing

| ID | short name | field | note |
|---|---|---|---|
| 100 | SMCLK | `sm_clock` | MHz |
| 101 | MMCLK | `memory_clock` | `N/A` on GB10: the memory is unified |
| 150 | TMPTR | `gpu_temp` | °C |
| 155 | POWER | `power_usage` | W; the busy/idle signal (`DCGM_FI_DEV_POWER_USAGE`) |
| 157 | POWINST | `power_usage_instant` | W |
| 203 | GPUTL | `gpu_utilization` | occupancy, not work: do not decide on it |
| 204 | MCUTL | `mem_copy_utilization` | |
| 252 | FBUSD | `fb_used` | `N/A` on GB10 |

**The profiling fields do not load on GB10.** `sm_active` (1002),
`gr_engine_active` (1001) and `dram_active` (1005) fail with
`Error setting watches. Result: -33: This request is serviced by a module of
DCGM that is not currently loaded` (2026-10-28).

DCGM sees the GPU die only. It reads about 4–11 W at idle; the wall plug reads
about 45 W for the same machine.
