# Building the two-node DGX Spark cluster from scratch

A procedure, in order, with the checks that catch each step going wrong. If
both Sparks were wiped tomorrow, this is what to do.

It is deliberately separate from
[`dgx-cluster-setup.md`](dgx-cluster-setup.md), which explains *why* each
thing is the way it is and carries the gotcha catalogue. This file is the
short path. When a step here fails, that file is where the explanation lives.

Every number quoted is measured on this pair, so a departure is visible.

## 0. What you need

- Two DGX Sparks, same account name, **same uid and gid** on both. Check with
  `id` before anything else; changing a uid later means re-owning a home
  directory. A fresh DGX OS install gives its first account uid 1000, so
  creating the same username first on each box matches for free.
- One or two approved QSFP DAC cables: Amphenol **NJAAKK-N911** (400 mm,
  NVIDIA PN `930-51986-0000-000`), Amphenol NJAAKK0006, or Luxshare
  LMTQF022-SD-R. 400–500 mm is the binding constraint — the chassis end up
  almost touching.
- **One cable is enough.** The second is worth +4.8% on the collective, not a
  doubling, because the PCIe budget binds before the wire does
  ([`second-cable-dgx-spark-cluster.md`](second-cable-dgx-spark-cluster.md)).

## 1. Cable it

Use the rear **QSFP cages**, not the RJ45 — the RJ45 stays as management.

With nothing plugged in, a Spark shows **no ConnectX-7 on the PCI bus**, an
empty `/sys/class/infiniband/`, and `mlx5_ib` bound to zero devices. That
looks exactly like a firmware fault and is not one. Attach the cable and the
device appears.

## 2. Learn which interface is which

```sh
for i in enp1s0f0np0 enP2p1s0f0np0 enp1s0f1np1 enP2p1s0f1np1; do
  echo "$i -> cage $(cat /sys/class/net/$i/phys_port_name)"
done
```

Four interfaces, two cages. Each cage is reached over **two PCIe paths**
(`0000:01:00.x` and `0002:01:00.x`), and each path is Gen5 x4, walling at
~112 Gb/s. **Both paths must be driven to saturate one port.** Count cages,
not interfaces: one cable lights two interfaces, which is easy to misread as
two cables.

## 3. Address the fabric

On the **head**. NetworkManager is the renderer on DGX OS, so this is `nmcli`,
not netplan. Find the profile bound to each interface by **device**, never by
its shipped name — the `Wired connection N` numbering differs between the two
machines, and on this pair `Wired connection 3` was the management NIC on one
node and a fabric NIC on the other.

```sh
P=$(nmcli -t -f NAME,DEVICE con show | awk -F: '$2=="enp1s0f1np1"{print $1}')
sudo nmcli con mod "$P" connection.id cx7-p1-path0 \
  connection.interface-name enp1s0f1np1 \
  ipv4.method manual ipv4.addresses 10.0.0.1/24 \
  ipv4.never-default yes ipv6.method disabled \
  802-3-ethernet.mtu 9000 connection.autoconnect yes
sudo nmcli con up cx7-p1-path0
```

Repeat for each interface. One /24 per interface; the worker takes `.2`:

| cage | interface | subnet | head | worker |
|---|---|---|---|---|
| p1 | `enp1s0f1np1` | 10.0.0.0/24 | .1 | .2 |
| p1 | `enP2p1s0f1np1` | 10.0.1.0/24 | .1 | .2 |
| p0 | `enp1s0f0np0` | 10.0.2.0/24 | .1 | .2 |
| p0 | `enP2p1s0f0np0` | 10.0.3.0/24 | .1 | .2 |

**Bring a profile down before moving an address to it**, or activation fails
with `IP configuration could not be reserved` — which reads like DHCP and is
duplicate-address detection.

MTU goes on with the address, never later and never on one side only: a
mismatch passes small pings and deadlocks a collective.

## 4. Name the fabric in `/etc/hosts`

On **both** nodes, identically. Not `.local` — that is mDNS, and resolving a
fabric peer through mDNS leaks the query onto the management network, which
silently runs "fabric" traffic at 1 GbE.

```
10.0.0.1	dgx1-cx7	dgx1-cx7-p1a
10.0.0.2	dgx2-cx7	dgx2-cx7-p1a
10.0.1.1	dgx1-cx7-p1b
10.0.1.2	dgx2-cx7-p1b
10.0.2.1	dgx1-cx7-p0a
10.0.2.2	dgx2-cx7-p0a
10.0.3.1	dgx1-cx7-p0b
10.0.3.2	dgx2-cx7-p0b
```

## 5. SSH, both directions, by the fabric names

```sh
ssh-keygen -t ed25519            # if needed
ssh-copy-id dgx2-cx7             # and the reverse, from the worker
```

Then accept the host key for **every** fabric name and address you will use,
in both directions. Each is a separate host identity from the LAN name. A
157 GiB download once completed and died instantly at the worker step on
`Host key verification failed`, because SSH had only ever been verified by
the LAN name.

## 6. Reboot both nodes

Before measuring anything. Configuration churn leaves a node in a state that
passes every functional check while running the collective at 13% of the
link, and `nmcli con down/up` does not clear it.

## 7. Verify, bottom-up

```sh
sudo ethtool <iface> | grep -E 'Speed|Link detected'   # 200000Mb/s, yes
rdma link show                                          # ACTIVE per cabled device
ping -c5 10.0.0.2                                       # under 2 ms
ping -M do -s 8972 10.0.0.2                             # jumbo, no fragmentation
```

Then the two that matter:

```sh
# raw link: run BOTH PCIe paths of a cage at once, on separate ports
ib_write_bw -d rocep1s0f1   -p 18515 -D 15 -s 1048576 --report_gbits [10.0.0.2]
ib_write_bw -d roceP2p1s0f1 -p 18516 -D 15 -s 1048576 --report_gbits [10.0.1.2]

# the collective, which is the real gate
torchrun --nnodes 2 --node-rank <0|1> --nproc-per-node 1 \
  --master-addr 10.0.0.1 --master-port 29500 scripts/cluster_allreduce.py
```

| check | healthy here | threshold |
|---|---|---|
| `ib_write_bw`, both paths, one cage | 196.08 Gb/s | — |
| `ib_write_bw`, all four paths, two cables | 218.36 Gb/s | — |
| **two-node all-reduce, 1 GiB** | **187–196 Gb/s** | **> 180 Gb/s** |
| ping RTT | 0.3–1.2 ms | **< 2 ms** |

**The collective is the gate.** RTT decides nothing on its own, and
`ib_write_bw` read 196 Gb/s throughout the incident where the collective ran
at 24 Gb/s.

Run the collective inside the serving image with `--device
/dev/infiniband:/dev/infiniband`, `--ulimit memlock=-1`, `--ulimit
stack=67108864`, `--network host`, `--ipc=host`, and **all** interface and HCA
names in `NCCL_SOCKET_IFNAME` / `NCCL_IB_HCA`. Without the device, NCCL falls
back to TCP and keeps working, slower, with no error.

## 8. Telemetry

Both nodes, from `dgx-utils/docker-compose/`:

```sh
docker compose -f node-exporter.yaml up -d      # :9100
docker compose -f dcgm.yaml up -d               # :5555
docker compose -f dcgm-exporter.yaml up -d      # :9400
docker compose -f cadvisor.yaml up -d           # :8084
```

Neither Spark registers an `nvidia` Docker runtime; they use CDI
(`/var/run/cdi/nvidia.yaml`), and the compose files reach the GPU through
`deploy.resources.reservations.devices`. If a GPU container fails, look
there, not at `--runtime=nvidia`.

Then add both nodes to Prometheus by their **DNS names** and SIGHUP it
(`docker kill -s HUP prometheus`) — the HTTP reload endpoint is not enabled.
Verify series arrive, not just that `up == 1`.

Wall power is worth having beside DCGM: DCGM sees the GPU die only and reads
~4–5 W idle, while the plug reads ~45 W for the same machine.

## 9. Bulk transfers between the nodes

Two things, worth 11x together:

```sh
rsync -Wah --progress -e 'ssh -c aes128-gcm@openssh.com' \
  <src> dgx2-cx7:<dst>
```

- **Address the peer by a fabric name.** By the LAN name it runs at
  **111 MiB/s** — gigabit, saturated. By the fabric name, 640 MiB/s.
- **Change the cipher.** The default (`chacha20-poly1305`) gives 640 MiB/s;
  `aes128-ctr` 1102 MiB/s; **`aes128-gcm@openssh.com` 1256 MiB/s**.
- `-W` skips the delta algorithm, which is the right trade when the link is
  fast and the CPU is the limit.

Prove the path rather than assuming it: read `tx_bytes` under
`/sys/class/net/<iface>/statistics/` on both sides during the transfer.

## 10. Serve a model

Recipes differ; the constants on this pair are:

- **Start the worker rank first, then the head.**
- **Allow a long health poll.** A 320B-class MoE took 9 min 7 s from launch to
  a live API. Recipes allow 3600 s for a reason.
- **`/health` is not proof** — a cluster passes it with a dead peer rank. Wait
  on a real completion.
- **Lower `GPU_MEMORY_UTILIZATION` below the recipe's default** if the head
  also runs the harness and monitoring. 0.835 hit `NVRM: Out of memory` on
  this pair; 0.78 works.
- **Leave `earlyoom` running, at 1.0 / 0.5 GiB, on both nodes (#700).** At its
  old 5% line (~6.1 GiB) it killed large two-node models at rest (GLM-5.3-Flash
  idles at 2.7 GiB on the head), so it used to be stopped for every run. Since
  #700 it fires only below the recipes' own 1.5 GiB memguards, and its
  `--prefer` list names the engine processes, so it is the last net rather than
  the first. Install and configure it with `scripts/setup_earlyoom.py --apply`
  on each node.
- **Check the model's chat template defaults.** Qwen3.8-Flash-Next defaults to
  `reasoning_effort: xhigh` and will spend an entire token budget reasoning
  and return `content: null`. Set the server default rather than making every
  client know.

Confirm both ranks loaded by comparing `MemAvailable` on the two nodes. One
node holding everything while the other sits near-empty means the split is not
what you think.

## 11. Release the pair

- Stop the servers with the recipe's own wrapper, never `pkill`.
- **Confirm `earlyoom` is running** on both nodes (`systemctl is-active earlyoom`).
- Leave the monitoring containers up.
