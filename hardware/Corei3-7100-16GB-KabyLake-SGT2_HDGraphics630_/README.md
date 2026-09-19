# Corei3-7100-16GB-KabyLake-SGT2_HDGraphics630_

**Role: the remote agent client for #562.** This machine serves no model. It runs
OpenCode and the harness against a model served on the DGX Spark over the LAN, so
the Spark's memory holds only the server. It is driven over ssh from the Spark;
host names and addresses live in each machine's ssh and DNS configuration, never
in this public repo.

The directory name is what `scripts/hardware_id.py` computes on this machine
(the trailing `_` comes from sanitizing the integrated GPU's name). The machine
is not in `scripts/machines.py`: it has no `tier` because it serves nothing.

Audited 2026-09-19 over ssh, read-only apart from cloning this repo into
`~/git/local-llm`.

## Hardware

| | |
|---|---|
| CPU | Intel Core i3-7100, **2 cores / 4 threads**, 3.9 GHz, 3 MiB L3 |
| Memory | 16 GB installed (2 × 8 GB DDR4-2133), 15.0 GiB usable |
| GPU | Intel HD Graphics 630 (integrated); no NVIDIA GPU, no `nvidia-smi` |
| Board | ASUS PRIME B250M-C |
| System disk | Crucial MX300 275 GB SATA SSD |
| Other disks | 4 × HGST 4 TB SATA HDD |
| Network | **wired gigabit** (`1000Mb/s`, full duplex); no WiFi interface up |
| Arch | x86_64 |

## Software, as of 2026-09-19

| | |
|---|---|
| OS | Ubuntu 26.04.1 LTS, kernel 7.0.0-31-generic |
| Python | 3.14.4 |
| bubblewrap | 0.11.1. **Works**: the harness's namespace probe (`--unshare-pid --tmpfs /tmp`) succeeds even though `kernel.apparmor_restrict_unprivileged_userns = 1` |
| git | 2.53.0 |
| Docker | 29.8.1 |
| sudo | passwordless |
| Clock | NTP-synchronized; time zone **UTC**, so row timestamps from this machine carry `+0000` |
| **Missing** | `uv`, `opencode`, `node`. `apt` offers `nodejs` 22.22.1 but no `uv` package |

## Network path to the Spark

- Round trip, Spark to this machine, 20 pings on 2026-09-19: min 0.181 / avg 0.591 /
  max 0.895 ms.
- This machine resolves the Spark by its internal DNS name, not by its hostname. The
  server address reaches the harness through the environment, never through
  `tasks.toml`.
- **Wired**, so a result from this client is the best-case LAN cost. A laptop on
  WiFi, the deployment the DGX RECOMMENDATIONS describe, adds its own latency on top.

## Things a run here must account for

1. **The machine serves other things.** A Postgres-backed web service with an IMAP
   importer runs in Docker from `~/git/gmail-archive`, beside a node_exporter. That
   checkout is also a benchmark target repo. The harness's default target layout
   (`--targets legacy`) parks the operator's checkout aside during a run, which
   would remove it from under the running service. **Runs here use
   `--targets sandbox`**, which uses the harness's own clones.
2. **It is a small client.** Two cores and 15 GiB, against the Spark's 20 cores and
   121.7 GiB. OpenCode startup, the agent's own test runs and the oracle's pytest
   all run here, so a trial's wall time includes more client-side time than the
   same trial on the Spark. A topology A/B from this machine measures LAN cost
   **plus** client CPU, and must say so.
3. **Client memory cap.** The harness default (`LOCAL_LLM_CLIENT_MEM_CAP_GIB=24`) is
   larger than this machine's memory; set it below 15 GiB.
4. **Background load.** The services above were idle during the audit (load average
   0.10–0.19), but their activity during a run is noise in the client-side time.
