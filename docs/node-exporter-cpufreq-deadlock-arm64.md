# node_exporter deadlocks on arm64 with `cppc_cpufreq`

**Not a benchmark finding.** This is a monitoring failure hit while bringing up
the DGX Spark (`Cortex-X925-GB10`), recorded because it costs an afternoon to
diagnose and the symptom points away from the cause. Nothing in this repository
depends on it.

**Applies to:** any aarch64 host whose CPU scaling driver is `cppc_cpufreq` —
Grace-based NVIDIA systems, and Arm server parts generally. Check with:

```sh
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver
```

Diagnosed 2026-09-11T10:30:00-0400.

---

## Symptom

`node_exporter` stops serving metrics. Prometheus shows the target DOWN, and by
hand:

```
$ curl localhost:9100/metrics
Limit of concurrent requests reached (40), try again later.
```

It recovers on restart and degrades again over roughly half an hour at a 10 s
scrape interval.

## Why the message is misleading

It reads as load — something hammering the exporter. It is not. Check the
connections:

```sh
$ ss -tn state established '( sport = :9100 )' | wc -l
2
```

**Forty in-flight requests against two open connections.** Those are leaked
handler goroutines, not clients. Prometheus gave up and closed its side long
ago; the handler never returned, so the slot was never released. Every scrape
leaks one more until the 40th, after which the exporter answers nothing.

That gap — in-flight count versus actual connections — is the diagnostic. If
they disagree, the problem is inside the exporter, not in front of it.

## Cause

`node_exporter` reads
`/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq` concurrently from
goroutines. Under `cppc_cpufreq` those concurrent reads hit a kernel wait
condition where the descriptor is never marked readable, and the goroutine
blocks indefinitely in `runtime_pollWait`.

Nothing logs. No collector reports failure. The scrape simply never completes.

## Fix

```
--no-collector.cpufreq
```

Twelve consecutive scrapes were clean immediately afterwards, where two of
three had hung before.

The cost is `node_cpu_scaling_frequency_hertz`. On a machine pinned to the
`performance` governor with a static `scaling_max_freq` that is close to
nothing; read CPU frequency out-of-band if it is ever needed, rather than
re-enabling the collector.

## What the bisection looked like, and the trap in it

Worth recording because the obvious method gives the wrong answer first.

| test | result |
|---|---|
| each of 49 collectors individually | **all 49 pass** |
| full scrape | hangs 2 of 3 |
| first 24 collectors | hangs |
| last 25 collectors | passes |
| `cpu cpufreq diskstats` | hangs |
| `cpu`, `cpufreq`, `diskstats` each alone, 3× | all pass |
| `cpufreq` alone, 6× — *after the above* | **hangs 6/6** |

`cpufreq` passes alone until something wedges, and then fails alone forever.
Testing each collector once in isolation clears it, which is exactly the first
thing anyone tries. The group test is what exposes it.

## It is not a kernel read that blocks

Worth knowing before filing this against the kernel or the firmware:

```sh
# sequential, all 20 CPUs
20/20 read in 0.02s

# 20 concurrent `cat` processes
ok=20 hang=0 in 0.00s
```

Plain `read(2)` on those files is fine, sequential or concurrent. The deadlock
is specific to Go's netpoller handling these sysfs descriptors, which is why it
appears in `node_exporter` and not in a shell loop — and why reproducing it
outside Go is likely to be a waste of time.

## Related

The same machine's monitoring stack lives in
[`evandhoffman/dgx-utils`](https://github.com/evandhoffman/dgx-utils), where the
flag is set with this reasoning attached. Two further notes from that bring-up
that are the same shape — correct on one machine, silently wrong on another:

- `--collector.perf` errors on every scrape where `kernel.perf_event_paranoid`
  is above 2.
- `--collector.hwmon` and `--collector.thermal_zone` report the same `acpitz`
  sensors on this hardware, so enabling both duplicates every series.
