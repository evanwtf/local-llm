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

**This is a known upstream bug with a fix in flight. It was not found here.**

- <https://github.com/prometheus/node_exporter/issues/3791> — opened
  2026-08-18 by `pablohoffman`, on this same hardware class (GB10 / DGX Spark,
  `cppc_cpufreq`, 20 CPUs). Open.
- <https://github.com/prometheus/procfs/pull/861> — proposed fix: bound the
  per-policy sysfs reads with `SetReadDeadline` (5 s) so a stuck read degrades
  gracefully instead of hanging the collector.

Hit independently on `Cortex-X925-GB10` on 2026-09-11T10:30:00-0400 and
diagnosed from scratch before the upstream issue was known. Everything below
matches what upstream reports; it is kept because the diagnostic path is the
useful part, not because anything here is new.

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

Two options. Both work; they trade different things.

**Disable the collector** — keeps a current node_exporter:

```
--no-collector.cpufreq
```

Twelve consecutive scrapes were clean immediately afterwards, where two of
three had hung before. The cost is `node_cpu_scaling_frequency_hertz`. On a
machine pinned to the `performance` governor with a static `scaling_max_freq`
that is close to nothing.

**Or pin node_exporter to 1.11.1** — keeps the cpufreq metrics. Reported
upstream by `rafaelkallis`: 1.11.1 vendors procfs < 0.21.x, which does not
parallelize `SystemCpufreq`, and "completely resolves it". The cost is running
a pinned older exporter.

The regression is squarely in **node_exporter 1.12.0/1.12.1 with procfs
v0.21.x**, so anything older than that parallel read path is unaffected.

### When to revisit

Re-enable `cpufreq` once procfs#861 is merged **and** node_exporter has bumped
its procfs dependency past it. Neither had happened as of
2026-09-11T10:30:00-0400. Until then the flag must stay.

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

Worth knowing before filing this against the kernel or the firmware — and
upstream reports the same trap, independently:

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

Upstream's stack trace names the exact path, which is worth having if this ever
needs re-confirming after a version bump:

```
runtime_pollWait -> os.ReadFile
  procfs/internal/util.ReadUintFromFile
  procfs/sysfs.parseCpufreqCpuinfo      sysfs/system_cpu.go:284
  procfs/sysfs.FS.SystemCpufreq.func1   sysfs/system_cpu.go:249  (errgroup)
```

The parallelism at `system_cpu.go:249` is deliberate — it exists to hide the
kernel's intentional 50 ms per-CPU delay — which is why the fix is a read
deadline rather than making the walk sequential.

## Related

The same machine's monitoring stack lives in
[`evandhoffman/dgx-utils`](https://github.com/evandhoffman/dgx-utils), where the
flag is set with this reasoning attached. Two further notes from that bring-up
that are the same shape — correct on one machine, silently wrong on another:

- `--collector.perf` errors on every scrape where `kernel.perf_event_paranoid`
  is above 2.
- `--collector.hwmon` and `--collector.thermal_zone` report the same `acpitz`
  sensors on this hardware, so enabling both duplicates every series.
