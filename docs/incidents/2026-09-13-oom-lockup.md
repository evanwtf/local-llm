# Incident: unified-memory OOM locked the DGX Spark, killing sshd (2026-09-13)

**Severity:** high — the machine became unreachable and recovered **only by the
physical power button**. It happened repeatedly: six boots on 2026-09-13.

**One-line cause:** on the GB10's 122 GiB *unified* pool, an uncapped vLLM (and
other memory-heavy work run beside it) drove free memory to a few hundred MB;
the kernel's global OOM killer, unable to reclaim the GPU allocation, killed
small userspace processes instead — **including sshd** — and shredded the
system while the real consumer survived.

## What happened

`nvidia-smi` cannot see the pool (`memory.used` reads `[N/A]` on GB10), so all
figures here are host `/proc/meminfo` and `/var/log/syslog`.

OOM invocations were not a one-off. `grep "invoked oom-killer" /var/log/syslog`
shows clusters through the night of 2026-09-12→13:

| window | what invoked the killer | note |
|---|---|---|
| 09-13 01:01–02:02 | dashboard-admin, apport, systemd | during the speculation cell (#354) |
| 09-13 02:55–03:15 | dbus-daemon, dcgm-exporter, bluetoothd, node_exporter | monitoring + desktop bits dying |
| 09-13 07:47 | VLLM::EngineCor | a server startup |
| **09-13 08:34** | **cadvisor, containerd(-shim), cron, systemd, python3, sudo, kthreadd** | **the cascade that killed sshd and locked the box** |
| 09-13 09:39 | VLLM::EngineCor (pid 48803) | concurrent vLLM startup **+ pytest suite** |

At 08:34:45 the kernel's Mem-Info reported `free:144248` pages — **~560 MB free
of 122 GiB** — with a process holding `total-vm:157659196kB` (~150 GB virtual;
a `VLLM::EngineCor`). In thirteen invocations in that one second it killed, among
others:

```
sshd, systemd, dbus/dbus-daemon, cron (x3), sudo (x2), bash (x2),
pipewire, pipewire-pulse, wireplumber, gnome-keyring-d, upowerd,
switcheroo-control, snapd-desktop-integration, dcgm-exporter,
node_exporter, cadvisor, fusermount3, xdg-document-portal, a `claude` process
```

Killing **sshd** removed remote access; killing **dbus/systemd** children left
the session unusable. There is no clean recovery from that state over the
network — hence the power button.

## Memory timeline (node_exporter via Prometheus)

Pulled from Prometheus (`gcx metrics query`, datasource `uMatQbvMk`,
`node_memory_MemAvailable_bytes{instance="dgx.internal:9100"}`). The DGX ran on
fumes for hours, and the monitoring itself went dark in the OOM windows.

- **Minimum available: 3.1 GiB of 121.7** at 01:25 EDT, during the #354
  speculation cell. Available sat at **3–4 GiB for sustained stretches**
  (01:25, 02:15–02:30, 03:20–03:50) — a served, uncapped vLLM holding the pool.
- **07:30–07:38: a flat 3.3 GiB free** while serving — the thin-margin state
  that any spike converts into the global OOM.
- **node_exporter scrape gaps line up with the OOM events**, because the
  exporter was itself killed (it appears in the 03:15 and 08:34 kill lists):
  25- and 45-minute holes at 01:00, 01:30, 02:35, and a 40-minute hole from
  07:55 that ends at the **08:34 lockup**. Monitoring is blind exactly when the
  box is dying — a Grafana dashboard showing "no data" here is the symptom, not
  the absence of one.
- Each recovery jumps straight back to ~119 GiB available (01:39, 07:39, 08:35),
  the signature of a kill/reboot freeing the whole pool at once.

The shape repeats all morning: a server loads, the pool falls to single-digit
GiB, the OOM fires, the box reboots, repeat — six times.

## Why it locks the box instead of just killing vLLM

Three facts combine, all specific to this hardware:

1. **Unified memory, no VRAM ceiling.** CPU and GPU share one 122 GiB pool.
   There is no separate device memory the driver would refuse to over-allocate;
   an oversized GPU request succeeds against host RAM.

2. **The GPU allocation is not reclaimable and barely shows in RSS.** vLLM's KV
   cache / CUDA pinned memory occupies the physical pool but is not attributed
   to a killable resident set the OOM killer can free. So when the pool is
   exhausted the killer **cannot free the actual hog** — it walks the process
   table and kills whatever it *can*: small, innocent, often root-owned system
   daemons. At 08:34 the 150 GB-total-vm vLLM process was **not** the victim;
   sshd, cron and dbus were.

3. **vLLM sizes its KV cache from free-memory-at-startup and takes almost all of
   it.** Left uncapped it profiled ~84 GiB available and built a
   2.2-million-token KV cache, leaving the box serving at **~7 GiB free**. At
   that margin any second consumer — a background command, a cron job, sshd's
   own growth, a runaway trial, a second server, the pytest suite — tips the
   whole system into the global OOM above.

## What made it fire, each time

- **Uncapped vLLM** (no `--gpu-memory-utilization` limit, no host reserve): the
  standing condition that leaves only ~7 GiB free.
- **A second memory consumer beside a resident server**: the 09:39 event was a
  vLLM startup run **concurrently with the pytest suite** (my error). On unified
  memory a resident vLLM (~115 GiB) and the ~3 min test suite cannot coexist.
- **Starting a server before a departing one released memory**: a vanished PID
  is not freed memory; the new server profiled a still-occupied pool and
  over-sized its cache (earlier #360 event).
- **Speculation runaway trials** (#354): trials generated to a ~32k-token
  ceiling, spiking KV/activation memory, coincident with the 01:xx–03:xx OOMs.

## Recovery

**Physical power button only.** With sshd dead there is no remote path back.
Document this on the machine itself: whoever is nearby power-cycles it.

## Fixes

**Landed this session (committed, pushed, 2909 tests green):**

- `scripts/machine_health.py boot` — detects a reboot between turns from the
  kernel boot id, so a fresh boot reads as one line instead of a baffling run
  of "server died" / "stale lock" findings.
- `scripts/memory_gate.py` + `run.py --memory-gate-gib N` — before each trial,
  block until host memory is above a floor **and** no longer falling; refuse
  (exit 1) rather than launch into a squeeze. Off by default.
- The #355 archiver/classifier fix (unrelated to OOM but part of the same
  clean-up): stopped valid rows being archived out of the ledger.

**Required before running a server again (the real fix — NOT yet applied):**

1. **Cap vLLM memory so the host keeps a large reserve.** The KV cache must not
   be allowed to consume the pool. Set `--gpu-memory-utilization` (and/or an
   explicit KV cap) so **~30–40 GiB stays free for the host** at steady state.
   The field recipe on #347 pointed the same way: `HOST_RESERVE_GIB=26`,
   `KV_TARGET_GIB=16`. A box that must keep sshd alive cannot serve at 7 GiB
   free.
2. **Sequence, never parallelize, memory-heavy work.** A resident vLLM and the
   pytest suite do not coexist. Run the suite only with the pool free.
3. **Wait for memory to release, not for the PID to exit,** before launching the
   next server (`memory_gate.py`, or poll `MemAvailable`).
4. **`machine_health.py check --for server` should consult the gate** and refuse
   a server launch while the pool is not free — the same guard shape as its
   dirty-tree / occupied-port checks. (Follow-up on #360.)
5. **Consider a systemd protection for sshd** — `OOMScoreAdjust=-1000` on
   `ssh.service`, or an MemoryMin reservation on `system.slice` — so remote
   access survives a future pool exhaustion even if prevention fails. Belt and
   braces; the cap in (1) is the primary fix.

## Evidence

`/var/log/syslog` and `/var/log/kern.log` on `spark-231e`, 2026-09-12 04:07
through 2026-09-13 09:39. Boot history: `journalctl --list-boots` (six boots on
09-13). Related issues: #360 (unified-memory OOM), #354 (speculation runaways),
#347 (the field recipe's host-reserve flags).
