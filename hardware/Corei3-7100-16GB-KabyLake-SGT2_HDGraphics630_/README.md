# Corei3-7100-16GB-KabyLake-SGT2_HDGraphics630_

**Status: fallback remote client, idle since 2026-10-05.** The Core i3-7100
serves no model. It runs OpenCode and the harness against a model served on the
DGX Spark over the LAN, so the Spark's memory holds only the server (#562, the
topology that became the standard). Since 2026-09-30 the cluster's client is
the Core i9-13900H laptop; the i3 is the fallback only (§10 of
[`hardware/agent-opener-prompt.md`](../agent-opener-prompt.md)). Its rows are
the `@ Corei3-7100-16GB` rows in [`docs/results.md`](../../docs/results.md),
from 2026-09-19 to 2026-10-05. It has no operator session of its own; the
Spark drives it over ssh. Host names and addresses live in each machine's ssh
and DNS configuration, never in this public repo.

The directory name is what `scripts/hardware_id.py` computes on the machine
(the trailing `_` comes from sanitizing the integrated GPU's name). It is
registered in `scripts/machines.py` with `tier = "remote-client"`, which no
backend carries, because it serves nothing.

## Hardware

| | |
|---|---|
| CPU | Intel Core i3-7100, **2 cores / 4 threads**, 3.9 GHz, 3 MiB L3 |
| Memory | 16 GB installed (2 × 8 GB DDR4-2133), 15.0 GiB usable |
| GPU | Intel HD Graphics 630 (integrated); no NVIDIA GPU |
| Board | ASUS PRIME B250M-C |
| System disk | Crucial MX300 275 GB SATA SSD |
| Other disks | 4 × HGST 4 TB SATA HDD |
| Network | **wired gigabit** (`1000Mb/s`, full duplex); no WiFi interface up |
| Arch | x86_64 |

## Software, as audited 2026-09-19

| | |
|---|---|
| OS | Ubuntu 26.04.1 LTS, kernel 7.0.0-31-generic |
| bubblewrap | 0.11.1. **Works**: the harness's namespace probe (`--unshare-pid --tmpfs /tmp`) succeeds with `kernel.apparmor_restrict_unprivileged_userns = 1` |
| Docker | 29.8.1 |
| sudo | passwordless |
| Clock | NTP-synchronized; time zone **UTC**, so row timestamps from it carry `+0000` |

Since #680 the client runs in the client image (`scripts/client_container.py`),
and each row records the image's OpenCode, uv and Python versions in
`env.client_machine.client_image`.

## Network path to the Spark

- Round trip, 20 pings on 2026-09-19: min 0.181 / avg 0.591 / max 0.895 ms.
- The i3 resolves the Spark by its internal DNS name. The server address
  reaches the harness through the environment, never through `tasks.toml`.
- **Wired**, so a result from this client is the best-case LAN cost. A laptop
  on WiFi adds its own latency on top.

## What a run on the i3 must account for

1. **The machine serves other things.** A Postgres-backed web service with an
   IMAP importer runs in Docker from `~/git/gmail-archive`, beside a
   node_exporter. That checkout is also a benchmark target repo, and the
   default `--targets legacy` parks it aside during a run, under the running
   service. **Use `--targets sandbox`**, which uses the harness's own clones.
2. **It is a small client.** Two cores and 15 GiB, against the Spark's 20 cores
   and 121.7 GiB. OpenCode, the agent's own test runs and the oracle's pytest
   all run on the i3, so a trial's wall time includes more client-side time
   than on a larger client. Its times compare only with rows from the same
   client.
3. **Client memory cap.** Inside the client container the kernel enforces the
   container's limit and the harness's watcher is off. On the bare host the
   watcher's default (`LOCAL_LLM_CLIENT_MEM_CAP_GIB=24`) is larger than the
   machine's memory; set it below 15 GiB.
4. **Background load.** The services above were idle during the audit (load
   average 0.10–0.19), but their activity during a run is noise in the
   client-side time.
