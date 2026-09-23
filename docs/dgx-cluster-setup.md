# Setting up and operating the dual DGX Spark cluster

How the two Sparks are joined, how to prove each layer works before trusting
the one above it, and what each layer looks like when it fails. Bring-up is
tracked in #646; the ledger separation the cluster needs is #647.

**Read this before touching either node's network configuration, before
launching anything that spans both nodes, and before concluding that the
fabric is at fault.**

**Building the pair from nothing?** [`dgx-cluster-howto.md`](dgx-cluster-howto.md)
is the short path: the steps in order, with the check that catches each one
going wrong. This file is the reasoning behind them and the gotcha
catalogue — read it when a step there fails.

## Status of every claim in this document

The fabric is **up and measured**; nothing has been served across the pair
yet. To keep that honest, every factual claim is tagged:

- **OBSERVED** — read off the machines, with the date. A fact.
- **PLANNED** — the intended configuration. Not yet applied, not yet true.
- **REPORTED** — from a third party's published recipe, on their hardware.
  A lead, never a result here.

When a PLANNED item is applied, change its tag to OBSERVED **in the same
commit that applies it**, and put the evidence in #646. A document that
describes a configuration nobody applied is worse than no document, because
the next session will act on it.

## The principle: verify bottom-up, never skip a layer

Every layer below depends on the one under it, and each fails in a way that
looks like a problem at a higher layer. A model that hangs on load looks like
a vLLM bug and is usually NCCL; an NCCL hang looks like a fabric outage and is
usually an unpinned interface. **Prove each layer with its own test before
moving up.** The whole reason this pair is hard to debug is that the
interesting failures are silent — a link that passes every ping and still
stalls the moment a collective runs.

| layer | what it is | proof it works |
|---|---|---|
| 0 | cable, port | `ethtool` reports a speed and `Link detected: yes` |
| 1 | ethernet link | both ends agree on 200000Mb/s |
| 2 | IP addressing | both fabric subnets ping, sub-millisecond |
| 3 | RoCE / RDMA | `rdma link show` ACTIVE; `ib_write_bw` moves data |
| 4 | host trust | passwordless SSH both ways; matching OS build and UID |
| 5 | collectives | `all_reduce_perf` completes across both nodes |
| 6 | engine | vLLM TP=2 loads and reaches `/health` |
| 7 | serving | a real completion, with tool calls, from a remote client |

Layers 6 and 7 are #648 and #649. Everything below them is #646.

---

## Layer 0–1: physical and link

**OBSERVED 2026-09-22T00:33-0400. Exactly one cable is connected, not two.**

Each Spark has **two QSFP cages**, `p0` and `p1`. Each cage is reachable over
**two PCIe paths** (domains `0000:01:00.x` and `0002:01:00.x`), so four
ethernet interfaces and four RoCE devices exist for two physical cages. Read
`/sys/class/net/<iface>/phys_port_name` to learn which cage an interface
speaks for — the interface name does not tell you.

| interface | PCI | cage | node A | node B |
|---|---|---|---|---|
| `enp1s0f1np1` | `0000:01:00.1` | **p1** | 200000Mb/s, Direct Attach Copper, link yes | 200000Mb/s, link yes |
| `enP2p1s0f1np1` | `0002:01:00.1` | **p1** | 200000Mb/s, Direct Attach Copper, link yes | 200000Mb/s, link yes |
| `enp1s0f0np0` | `0000:01:00.0` | p0 | no cable | no cable |
| `enP2p1s0f0np0` | `0002:01:00.0` | p0 | no cable | no cable |

**Both live interfaces are the same physical wire.** They are two PCIe paths
to cage `p1`, and cage `p1` on node A is cabled to cage `p1` on node B. Cage
`p0` reports "No cable" on both nodes, so the second NJAAKK-N911 cable in hand
is **not plugged in**. Connecting it needs physical access to both chassis.

The consequence that matters: **the ceiling is one 200 Gb/s link, not two.**
Traffic over `enp1s0f1np1` and `enP2p1s0f1np1` shares one wire. Using both
can help saturate the wire if a single PCIe path cannot, but it cannot exceed
it, and nothing here should be described as 400 Gb/s.

**The NIC does not exist until a cable is attached.** Before cabling, node A
showed no Mellanox device on the PCI bus at all, an empty
`/sys/class/infiniband/`, and `mlx5_ib` bound to zero devices. This was an
open question in #641 and looked like a firmware problem. It was not. With a
cable attached the device enumerates normally. **Do not debug a missing
ConnectX-7 on an uncabled Spark** — that is its documented-by-experiment
resting state.

```sh
# what to run
sudo ethtool <iface> | grep -Ei 'speed|link detected'
sudo lshw -c net -short
```

### Which cage pairs with which — resolved

**OBSERVED.** Cage `p1` on node A to cage `p1` on node B. It is not an
inference: `p1` is the only cage showing a link on either node, and a cable
has two ends.

This was open while the topology was assumed to be two cables. It is worth
keeping the method, because it returns the moment the second cable goes in:
with no addresses configured, the interfaces carry no IPv6 link-local either,
so a multicast neighbour probe returns nothing and neither node can say which
of its cages reaches which of the peer's. Resolve it by addressing one pair,
pinging, and swapping if it fails — then **record the answer here**, because
every NCCL and Ray configuration afterwards names interfaces explicitly and a
wrong pairing hangs rather than errors.

Do not assume a straight-through pairing when the second cable is added.
MiaAI-Lab's published two-Spark topology (REPORTED) cables `enp1s0f1np1` on
the head to `enp1s0f0np0` on the worker — different cages at the two ends —
and their recipe works. A cross-cage pairing is normal, not a miscabling.

## Layer 2: addressing

**OBSERVED 2026-09-22T00:43-0400. Applied and verified.**

Static point-to-point addressing through `nmcli` — NetworkManager is the
renderer on DGX OS, so this is deliberately *not* a netplan stanza (see the
gotchas). MTU 9000, no default route, IPv6 off, autoconnect on.

| profile | interface | cage | node A | node B |
|---|---|---|---|---|
| `cx7-p1-path0` | `enp1s0f1np1` | p1 | 10.0.0.1/24 | 10.0.0.2/24 |
| `cx7-p1-path1` | `enP2p1s0f1np1` | p1 | 10.0.1.1/24 | 10.0.1.2/24 |

Both subnets traverse **the same wire** — they are two PCIe paths to cage p1,
not two links. Two subnets on one segment is intentional: it is how both PCIe
paths get driven at once, which is the only way to reach line rate (Layer 3).

```sh
sudo nmcli con mod <profile> \
  connection.interface-name <iface> \
  ipv4.method manual ipv4.addresses <addr>/24 ipv4.never-default yes \
  ipv6.method disabled 802-3-ethernet.mtu 9000 connection.autoconnect yes
```

`/etc/hosts` on both nodes carries `spark-a-cx7` / `spark-b-cx7` for the
10.0.0.0/24 pair, so nothing resolves a fabric peer through mDNS.

**Verified:** both subnets ping with 0% loss (10.0.0.0/24 min/avg/max
0.661/1.016/1.645 ms; 10.0.1.0/24 1.100/1.297/1.484 ms), and jumbo frames
pass end-to-end — `ping -M do -s 8972`, 3/3, no fragmentation.

**The RTT anomaly was the one real warning sign, and it was dismissed.**
Before the peer reboot, RTT averaged 1.016 ms where a direct DAC should give
tens of microseconds; it was recorded as probably CPU idle states. After the
reboot it averaged **0.282 ms**. The same reboot took the all-reduce from
24.20 to 187.10 Gb/s (Layer 5). An unexplained order-of-magnitude latency
anomaly is a blocker, not a footnote.

### Is the second cable worth it? No, and it is measured

**OBSERVED 2026-09-22.** Both cables are now connected. The second one moved
the raw link from 196.08 to 218.36 Gb/s (+11.4%) and the two-node all-reduce
from 187.10 to 196.09 Gb/s (**+4.8%**). It cannot do better: the host-to-NIC
PCIe budget is about 224 Gb/s and one cable already delivered 196, because
both cages are served by the same two Gen5 x4 devices.

The demonstration is that every path halves when all four run at once —
109.3 Gb/s alone, 54.59 Gb/s together, and 4 x 54.59 = 218.36 against
2 x 109.3 = 218.6, agreeing to 0.1%.

Full write-up, aimed at someone deciding whether to buy or connect the second
cable: [`second-cable-dgx-spark-cluster.md`](second-cable-dgx-spark-cluster.md).
Working notes in #659.

### Bonding, and why the default is not to

**PLANNED — decision open.** With one cable connected there is nothing to
bond for bandwidth: the two live interfaces are two PCIe paths to a single
wire, and no bonding mode makes one cable carry more than 200 Gb/s. Bonding
them would buy path redundancy against a PCIe-path failure, not throughput.

If the second cable is connected later, two genuinely independent wires
exist — but see Layer 3 for why that buys far less than it appears to. Even
then the default should be **two independent subnets, not a bond**. LACP
needs a switch and there is none, leaving `balance-rr` or `active-backup`.
NVIDIA's own guidance and every third-party recipe read here configure
independent interfaces and hand *both* names to NCCL, letting the collective
library use both rails itself. That is also easier to diagnose: with a bond,
a single failed rail degrades silently.

## Layer 3: RoCE and RDMA

**OBSERVED 2026-09-22T00:06-0400.** RoCE devices exist and the cabled ones are
live on both nodes:

```
link rocep1s0f0/1    state DOWN    physical_state DISABLED   netdev enp1s0f0np0
link rocep1s0f1/1    state ACTIVE  physical_state LINK_UP    netdev enp1s0f1np1
link roceP2p1s0f0/1  state DOWN    physical_state DISABLED   netdev enP2p1s0f0np0
link roceP2p1s0f1/1  state ACTIVE  physical_state LINK_UP    netdev enP2p1s0f1np1
```

Note the naming: the RoCE device `rocep1s0f1` corresponds to the ethernet
interface `enp1s0f1np1`. **Both names are needed** — NCCL wants the RoCE
device in `NCCL_IB_HCA` and the ethernet interface in `NCCL_SOCKET_IFNAME`,
and they are not interchangeable.

```sh
rdma link show
ibdev2netdev          # the mapping, explicitly
```

ACTIVE means the link layer is up. It does **not** mean RDMA can move data.

### Measured throughput, and the PCIe ceiling that shapes everything

**OBSERVED 2026-09-22T00:43-0400.** `ib_write_bw`, 1 MiB messages, 12-15 s,
`--report_gbits`:

| configuration | Gb/s |
|---|---|
| single PCIe path (`rocep1s0f1`), 1 QP | 109.29 |
| single PCIe path (`rocep1s0f1`), 8 QPs | 111.86 |
| **both paths concurrently** (`rocep1s0f1` + `roceP2p1s0f1`) | **196.08** (98.04 + 98.04) |

TCP for comparison, `iperf3 -P 8` on one path: **111 Gb/s**, zero retransmits.

196.08 Gb/s is **98.0% of the port's 200 Gb/s rating**. The link is
saturated.

**Each PCIe path is Gen5 x4.** `LnkSta: Speed 32GT/s, Width x4` on both
`0000:01:00.0` and `0002:01:00.0` — about 126 Gb/s of theoretical payload,
of which 111.86 is 89%. Raising queue pairs from 1 to 8 moved it 2.4%, so
this is a **PCIe wall, not a queue-depth limit**.

Two consequences, and they are the most important facts in this document:

1. **One PCIe path cannot saturate the port.** Both must be driven together
   or 43% of the link is unused. Every NCCL, Ray and engine configuration
   must name *both* interfaces and *both* HCAs, never one.
2. **The host-to-NIC PCIe budget is ~224 Gb/s total** (2 paths x ~112), and
   one cable already delivers 196. So a **second cable cannot come close to
   doubling throughput** — the wire stopped being the constraint once both
   PCIe paths were in use. Its ceiling is the PCIe budget, an upper bound of
   roughly 14% more aggregate, and only with traffic balanced perfectly
   across both cages. A second cable is worth adding for redundancy, or if a
   measurement later shows a tensor-parallel split starving on the link — not
   as a throughput upgrade.

```sh
# server on the peer, client here; run one pair per PCIe path, concurrently
ib_write_bw -d <hca> -p <port> -D 15 -s 1048576 --report_gbits [<peer ip>]
```

---

## Layer 4: host trust and parity

### Passwordless SSH, both directions

**OBSERVED 2026-09-22.** Working in both directions after each node's host key
was accepted.

Bidirectional is not optional. Collective launchers, Ray, and every
`docker save | ssh docker load` step in the third-party recipes assume the
head can reach the worker *and* that the worker can reach back. A one-way
setup passes a casual test and fails at cluster start.

**Watch for the host-key gap.** Key authentication being configured does not
mean a given shell can connect: a session whose `known_hosts` lacks the peer
fails with `Host key verification failed`, which reads like an authentication
problem and is not one. Accept the key once per account that needs it.

### Matching OS builds

**OBSERVED — mismatched.** Node A is 7.2.3 / kernel 6.17.0-1032-nvidia; node B
is 7.5.0 / kernel 7.0.0-1019-nvidia. Six months and a major kernel version
apart.

Bring them to the same build **before** clustering. Debugging a two-node
collective across mismatched driver and kernel versions means every failure
has a second candidate cause. Node A is the one that moves; see this
directory's README for the `nvfanread` consequence.

### Matching account

**OBSERVED — matched.** uid 1000, gid 1000, `sudo` on both.

---

## Layer 5: collectives

**OBSERVED 2026-09-22T01:40-0400. At line rate, after a reboot — see the
correction below, it matters more than the number.**

`scripts/cluster_allreduce.py` under `torchrun`, one rank per node, inside the
serving image. bf16, median of 20 iterations, bus bandwidth by nccl-tests'
definition (`algbw * 2(n-1)/n`, which equals algbw at two ranks). Three
consecutive runs at 1 GiB: **184.84, 187.14, 187.10 Gb/s** — median
**187.10 Gb/s**, which is **95.4% of the 196.08 Gb/s** the link delivers
under `ib_write_bw`.

Representative run:

| message | latency | busbw |
|---|---|---|
| 1 MiB | 1.160 ms | 7.23 Gb/s |
| 4 MiB | 0.655 ms | 51.23 Gb/s |
| 16 MiB | 0.894 ms | 150.06 Gb/s |
| 64 MiB | 3.407 ms | 157.57 Gb/s |
| 256 MiB | 12.908 ms | 166.37 Gb/s |
| 1 GiB | 46.473 ms | 184.84 Gb/s |

Correctness passes: an all-reduce of ones returns exactly the world size.

The collective reaches line rate. **Plan around ~187 Gb/s.**

### The correction: a reconfigured node stays degraded until it is rebooted

Before the peer was rebooted, the identical test plateaued at **24.20 Gb/s**
— 12% of the link — and stayed there across three tuning attempts (default
24.20, `NCCL_NET_GDR_LEVEL=5` + `NCCL_DMABUF_ENABLE=1` 24.25,
`NCCL_BUFFSIZE=16M` with 4 QPs per connection 24.02). The consistency made it
look like a hardware ceiling, and it was written up as one, with GB10's lack
of GPUDirect RDMA as the explanation.

**That explanation was wrong.** GPUDirect RDMA is still disabled after the
reboot — `GPU Direct RDMA Disabled for HCA` on both — and the collective now
runs at 187 Gb/s anyway. Staging through host buffers was never the limiter.

What actually changed: the peer had had its NetworkManager fabric profiles
created, renamed, re-addressed and re-activated several times during bring-up
(gotchas 6 and 7). Something in that churn left the node in a state that
passed **every** functional check — link up, RoCE ACTIVE, both subnets
pinging, jumbo frames clean, `ib_write_bw` at 196 Gb/s, a numerically correct
all-reduce — while the collective ran at 13% of its speed. The only visible
symptom was latency: **ping RTT averaged 1.016 ms before the reboot and
0.282 ms after**, a 3.6x drop that was noted at the time and dismissed as
CPU idle states.

The lesson is procedural, not architectural:

- **Reboot both nodes after configuring the fabric, before measuring
  anything.** Configuration churn leaves state that survives `nmcli con
  down/up` and is invisible to every functional test.
- **A consistent number is not a correct number.** Three tuning attempts
  agreeing within 1% was read as a plateau; they were all measuring the same
  degraded state.
- **Treat an unexplained latency anomaly as a blocker, not a footnote.** The
  1 ms RTT was the only evidence, and it was recorded and set aside.

```sh
torchrun --nnodes 2 --node-rank <0|1> --nproc-per-node 1 \
    --master-addr <head fabric ip> --master-port 29500 \
    scripts/cluster_allreduce.py
```

Run it inside the serving image with `--device /dev/infiniband`,
`--ulimit memlock=-1`, `--network host`, `--ipc=host`, and **both** names in
`NCCL_SOCKET_IFNAME` and `NCCL_IB_HCA` (gotchas 17 and 18).

## Layer 6: the engine

**PLANNED.** #648 owns the first model. What is settled is the mechanism: all
five third-party two-Spark recipes read so far (#650) are **vLLM with
`tensor-parallel-size 2`**, one GB10 per node, over the ConnectX fabric. Some
overlay custom kernels or EXL3 on top of vLLM; none use llama.cpp RPC.

Operational facts common to those recipes (REPORTED):

- Docker on both nodes, containers run `--network host --ipc=host`
- The worker rank starts first, then the head
- Weights are either copied to both nodes or shared from the head over NFS on
  the fabric link; budget **~200 GiB free per node** for the largest. Both
  nodes have 3.7 TiB, so disk is not a constraint here
- Model load is slow enough that health polling needs a timeout in the
  thousands of seconds, not the tens. A cluster that has not answered
  `/health` after two minutes is not necessarily broken

**Host RAM is GPU memory on a Spark.** Every allocation is committed
immediately and the weights live in driver allocations that are not charged to
any process's RSS — so the kernel OOM killer, which scores by RSS, cannot see
the real consumer and will kill something innocent instead. Watch
`MemAvailable` **after a long prompt**, not after a boot: the floor comes
during prefill.

---

## Layer 7: serving to a client

**PLANNED.** #649. Two things that differ from the single-Spark case:

- **A two-node deployment has a head**, and only the head serves the API. A
  client pointed at the worker fails in a way that looks like a network
  problem. Record which node serves.
- **`/health` is not proof.** A cluster can pass a health check with a dead
  peer rank. Wait on a real completion, and make it long enough to cross the
  point where the inter-node hop shows up — a short reply can succeed on a
  deployment that stalls on sustained decode.

---

## Gotchas — the full list

Every one of these either bit during this bring-up (**HIT**), was checked and
found already correct (**CLEAR**), was avoided by a deliberate choice
(**AVOIDED**), or is carried from a third party and not yet met here
(**REPORTED**). Read the whole list before redoing this; several are silent
and several cost real time.

### 1. Memory: check it before anything significant, and stop at 50% — **HIT**

*Symptom:* background jobs on the head killed for critical memory pressure,
7 GiB of swap in use, a resident server one allocation away from the OOM
killer taking `sshd`. No error from the jobs themselves.

*Cause:* 2026-09-22 (#672). GLM-5.3-Flash was left resident across both
nodes at `GPU_MEM_UTIL=0.86` after its verdict was posted, leaving the head
3–8 GiB of `MemAvailable`. On top of it went a 161 GiB `hf download`, a
`docker pull` of a ~20 GB image, and a throughput sweep. On a Spark the
128 GB pool is shared between CPU and GPU: a resident model is host memory,
and page cache, image extraction and the load test all compete for what is
left.

*Fix:* before anything significant (a download, an image pull, a launch, a
load test, a trial run), read `MemAvailable` and swap on **every node it
touches**. **If more than 50% of memory is in use, stop and evaluate**: what
holds it, what the new job adds, and the right course, which is usually to stop
a server whose result is already posted, with its own wrapper, before
starting. A model is never kept "warm until the next one is ready" when the
next one needs a download. After a server stops, confirm `earlyoom` is running on both nodes and run
preflight (`scripts/machine_health.py check`, `scripts/machine_state.py`,
`MemAvailable` and swap on both nodes) before resuming. Stopping the server
freed 107.5 GiB on the head (8.7 to 116.2 GiB available) and 112.2 GiB on the worker (4.4 to 116.6).

**The #1 DGX rule.** Every other entry in this list costs time. This one can
cost the node: when the pool runs out, the OOM killer takes small daemons
first, and on the worker that means losing it with no console
([`incidents/2026-09-13-oom-lockup.md`](incidents/2026-09-13-oom-lockup.md)).

### 2. The ConnectX-7 does not exist until a cable is plugged in — **HIT**

*Symptom:* no Mellanox device in `lspci`, `/sys/class/infiniband/` empty,
`ibdev2netdev` and `rdma link show` print nothing, `mlx5_ib` bound to zero
devices. Looks exactly like a NIC disabled in firmware.

*Cause:* the port does not enumerate on the PCI bus with no cable attached.
This is normal for this integrated NIC and is **not** how ordinary PCIe
devices behave, which is why it reads as a fault.

*Fix:* attach the cable. Do not debug a missing ConnectX-7 on an uncabled
Spark. This cost #641 an open question for weeks.

### 3. Four interfaces, two physical cages — **HIT**

*Symptom:* `enp1s0f0np0`, `enp1s0f1np1`, `enP2p1s0f0np0`, `enP2p1s0f1np1`
for what is obviously a two-port NIC.

*Cause:* each QSFP cage is reachable over **two PCIe paths** (domains
`0000:01:00.x` and `0002:01:00.x`). The interface name does not tell you
which cage it speaks for.

*Fix:* `cat /sys/class/net/<iface>/phys_port_name` — `p0` or `p1`. Binding a
static address to an interface on the uncabled cage produces a silent
blackhole: the config applies, nothing moves.

### 4. Two interfaces up does **not** mean two cables — **HIT**

*Symptom:* two interfaces at 200000Mb/s with `Link detected: yes`, two
reporting `No cable`, and two cables sitting in the box. Reads as "both
cables are in."

*Cause:* one cable lights **both PCIe paths to one cage**. This bring-up
recorded "both cables are in" and was wrong; it was a single cable between
cage `p1` on each node.

*Fix:* count *cages* with a link, not interfaces. Group interfaces by
`phys_port_name` first, then count.

### 5. One PCIe path cannot saturate the port — **HIT**

*Symptom:* a correctly configured 200 Gb/s link benchmarks at ~110 Gb/s and
more queue pairs do not help.

*Cause:* each PCIe path is **Gen5 x4**, about 126 Gb/s of payload. Measured:
109.29 Gb/s at 1 QP, 111.86 at 8 QPs — a 2.4% spread, so the wall is PCIe,
not concurrency.

*Fix:* drive **both** paths concurrently; that reaches 196.08 Gb/s, 98% of
the port rating. Give every NCCL/Ray/engine configuration both interface
names and both HCA names. Naming one silently halves the fabric.

### 6. NetworkManager profile names map to different interfaces on each node — **HIT**

*Symptom:* the same `nmcli` command run on both nodes leaves the subnets
crossed — the peers do not ping even though every address looks right.

*Cause:* DGX OS pre-creates `Wired connection 1..5` DHCP profiles, and the
number-to-interface binding is **not the same on two machines**. Node B's
"Wired connection 5" was a different interface than node A's.

*Fix:* never address a profile by its shipped name. Pin it explicitly with
`connection.interface-name <iface>`, and verify with
`nmcli -t -f NAME,DEVICE con show --active`.

### 7. Reusing a profile while the address is live fails — **HIT**

*Symptom:* `Connection activation failed: IP configuration could not be
reserved (no available address, timeout, etc.)`.

*Cause:* duplicate address detection. Moving an address between two profiles
while the old one is still up means the address exists twice for a moment.

*Fix:* `nmcli con down` **both** profiles, then modify, then bring both up.
The error names DHCP-ish causes and is really a duplicate-address conflict.

### 8. Configure with `nmcli`, not netplan — **AVOIDED**

*Cause:* DGX OS ships Ubuntu Desktop with NetworkManager as the renderer, and
`/etc/netplan/` holds only `00-installer-config.yaml` with
`renderer: NetworkManager`. Adding static netplan stanzas on top means
NetworkManager keeps trying to DHCP the same interfaces — recurring
"Activation of network connection failed" popups and reset routes.

*Fix:* pick one manager and stay there. This bring-up used `nmcli`
throughout, so the conflict never arose. If you prefer netplan, set
`renderer: networkd` **and** mark the ConnectX interfaces unmanaged in
`/etc/NetworkManager/conf.d/`, and `chmod 600` the netplan file or `netplan
apply` warns.

Note the property name: NetworkManager wants `802-3-ethernet.mtu` (or
`ethernet.mtu`); `ipv4.mtu` does not exist and errors.

### 9. MTU mismatch passes ping and deadlocks collectives — **CLEAR**

*Symptom:* everything works until a real collective, which hangs.

*Cause:* MTU 9000 on one side only. Small ICMP fits either way; a full-size
frame does not.

*Fix:* set MTU with the address, in the same command, on both nodes. Verify
with `ping -M do -s 8972 <peer>` — `-M do` forbids fragmentation, so it
fails loudly instead of silently degrading. Verified here, 3/3.

### 10. Strict reverse-path filtering drops replies across two subnets — **CLEAR**

*Symptom:* traffic sent on one subnet and answered on the other is dropped
with no log.

*Cause:* `rp_filter=1` (strict) rejects packets whose reply route does not
match the inbound interface — the normal case when two subnets share a wire.

*Fix:* `rp_filter=2` (loose). **Already 2 on both nodes here**, on `all`,
`default` and each fabric interface — DGX OS ships it that way. Check rather
than assume, in both directions.

### 11. mDNS leaks fabric lookups onto the management network — **AVOIDED**

*Cause:* both units ship with `spark-xxxx.local` mDNS names. Back-to-back
with no upstream DNS, resolution for the peer falls back to Wi-Fi or the
management ethernet — so a "fabric" connection silently runs at 1 GbE.

*Fix:* hardcode the fabric addresses in `/etc/hosts` on both nodes and point
every launcher at those names. Never use `.local` for cluster traffic.
Done here: `spark-a-cx7` / `spark-b-cx7`.

### 12. `apt-get upgrade` will not move you to a new kernel — **HIT**

*Symptom:* both nodes are fully upgraded and still differ — 7.2.3 with kernel
`6.17.0-1032-nvidia` against 7.5.0 with `7.0.0-1019-nvidia`.

*Cause:* both track the same meta-package, `linux-nvidia-hwe-24.04`, from the
same sources, with nothing held. The lagging node's **candidate is already
`7.0.0-1019`** — it simply is not installed, because `apt-get upgrade` never
installs *new* packages, and moving the meta-package forward requires
installing a new `linux-image-*` package.

*Fix:* `sudo apt-get full-upgrade` (or install the meta-package by name),
then reboot. `apt-cache policy linux-nvidia-hwe-24.04` shows installed
against candidate and settles the question in one command.

*Consequence here:* an out-of-tree signed module (`nvfanread`, fan RPM into
node_exporter) must be rebuilt and re-signed against the new kernel, or the
fan series disappears from Prometheus with no error.

### 13. Key auth configured does not mean this account can connect — **HIT**

*Symptom:* `Host key verification failed`, which reads as an authentication
problem.

*Cause:* the peer is absent from **this** account's `known_hosts`. Key auth
was set up; the host key was not accepted for every account that needs it.

*Fix:* accept it once per account, in **both** directions. Collective
launchers and every `docker save | ssh docker load` step need the reverse
direction too, and a one-way setup passes a casual test.

### 14. NCCL cannot use IP aliases, and hangs rather than errors — **REPORTED**

*Symptom:* a two-node launch starts, prints nothing further, and never exits.
Indistinguishable from a slow model load.

*Cause:* `ncclCommInitRank` waiting forever because NCCL was not pinned to
the fabric. Ray tolerates aliases; NCCL does not.

*Fix:* set `NCCL_SOCKET_IFNAME` (ethernet names, **both**) and `NCCL_IB_HCA`
(RoCE device names, **both**) explicitly. Check this before suspecting the
fabric.

### 15. DAC link flap after a one-sided reboot — **REPORTED**

*Symptom:* `NO-CARRIER` on an interface that is physically plugged in, after
one node reboots while the other stays up.

*Fix:* `sudo ip link set <iface> down && sudo ip link set <iface> up`, and
check FEC negotiation with `sudo ethtool --show-fec <iface>`.

### 16. The OOM killer cannot see the model — **REPORTED**

*Symptom:* something innocent is killed under memory pressure while the
process actually consuming memory survives.

*Cause:* on a Spark, host RAM *is* GPU memory, and weights live in driver
allocations not charged to any process's RSS. The OOM killer scores by RSS.

*Fix:* watch `MemAvailable` **after a long prompt**, not after a boot — the
floor comes during prefill, not at load. Keep `earlyoom` on both nodes at the
#700 line — SIGTERM at 1.0 GiB, SIGKILL at 0.5 GiB (`-M 1048576,524288`) —
with `--prefer` naming the engine processes, so the RSS score does not decide
the victim. Its older 5% line (~6.1 GiB) sat above the 2.7 GiB GLM-5.3-Flash
idles at, and on 2026-09-23 it SIGTERMed a GLM launch during DFlash2 capture;
the fix was the line, not disabling it (the GLM recipe has no memguard of its
own).

### 17. A container without `/dev/infiniband` silently falls back to TCP — **HIT**

*Symptom:* the collective runs, returns correct results, and is slow. No
error, no warning at default verbosity.

*Cause:* `docker run` without the RDMA character devices. NCCL logs
`NET/IB : No device found`, selects `NET/Socket`, and everything keeps
working over TCP. Measured here: **16.35 Gb/s** on the socket path against
**24.20 Gb/s** once RDMA was available, and 3.013 ms against 0.800 ms of
latency at 1 MiB.

*Fix:* `--device /dev/infiniband:/dev/infiniband`. The recipe's own compose
file does this; a hand-rolled `docker run` for testing is what missed it.
Confirm with `NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=NET` and look for
`NET/IB : Using [0]... [1]...` rather than `Using network Socket`.

**This is the most dangerous entry in this list**, because the fallback path
produces plausible numbers. Always confirm the transport; never infer it from
"it worked".

### 18. RDMA needs unlimited locked memory in the container — **HIT**

*Symptom:* `ibv_reg_mr_iova2 failed with error Cannot allocate memory`, then
`ibv_create_qp failed`, then `ncclSystemError`. The job dies at init.

*Cause:* Docker's default `memlock` limit. RDMA registers pinned memory
regions and cannot fall back to unpinned.

*Fix:* `--ulimit memlock=-1 --ulimit stack=67108864`. The host's own limit is
already `unlimited`, which is why this appears only inside a container. The
recipe's compose file sets both.

### 19. A reconfigured node stays degraded until rebooted — **HIT**

*Symptom:* everything passes and the collective runs at 13% of the link.
Link up, RoCE ACTIVE, both subnets pinging, jumbo frames clean,
`ib_write_bw` at 196 Gb/s, a numerically correct all-reduce — and 24.20 Gb/s
where 187 was available.

*Cause:* NetworkManager fabric profiles created, renamed, re-addressed and
re-activated repeatedly during bring-up (gotchas 6 and 7) leave state that
`nmcli con down/up` does not clear.

*Fix:* **reboot both nodes after configuring the fabric, before measuring
anything.** One reboot took the all-reduce from 24.20 to 187.10 Gb/s with no
other change.

*The trap inside the trap:* three different tuning attempts agreed within 1%
(24.20 / 24.25 / 24.02), which read as a hardware plateau and was published
as one, with GB10's lack of GPUDirect RDMA as the explanation. GDR is still
disabled at 187 Gb/s, so that explanation was simply wrong. **A consistent
number is not a correct number** — repeated measurements of one degraded
state agree with each other perfectly.

The only symptom that pointed at it was ping RTT: 1.016 ms before, 0.282 ms
after. It was recorded and dismissed as CPU idle states.

### 20. The fabric addresses are separate SSH identities — **HIT**

*Symptom:* `Host key verification failed` at a launcher's worker step, after
passwordless SSH between the nodes was verified and working.

*Cause:* SSH was verified by the nodes' **LAN names**. A launcher drives the
worker by its **fabric address** (`10.0.0.2`), which is a different host
identity with its own `known_hosts` entry — as are `10.0.1.2` and any
`/etc/hosts` alias for them.

*Cost here:* a 157 GiB head download completed and then the worker-staging
step died immediately, at the end of 1 h 43 m.

*Fix:* accept every fabric identity in **both** directions before launching —
each address and each alias, head to worker and worker to head. **Verify SSH
by the exact name the launcher will use**, not by any name that reaches the
box. This is gotcha 13 in a second costume, and knowing gotcha 13 did not
prevent it.

### 21. Each node may download the whole checkpoint from the internet — **HIT**

*Symptom:* the worker stages its weights slowly and the fabric is idle.

*Cause:* the recipe's default `DSPARK_WORKER_HF_NFS=0` gives each node its
own Hugging Face cache, so the worker fetches the checkpoint from the
internet rather than from the head. Measured with interface counters during
the copy, not assumed:

| interface | throughput |
|---|---|
| head, fabric path 0 tx | 0 MiB/s |
| head, fabric path 1 tx | 0 MiB/s |
| head, management tx | 0 MiB/s |
| **worker, management rx** | **48 MiB/s** |
| worker, fabric rx | 0 MiB/s |

Nothing leaves the head at all. The pair spends **2 x 157 GiB of WAN
transfer** while a link that measures 196.08 Gb/s sits unused.

*Fix:* `DSPARK_WORKER_HF_NFS=1` (or the equivalent `--nfs` in the sibling
recipes) shares the head's cache over NFS on the ConnectX link and skips the
second download entirely. **Set it before the first download, not after** —
by the time the second copy is visible, the transfer that would have been
saved is already most of the way done.

*Check it the same way:* read `tx_bytes`/`rx_bytes` under
`/sys/class/net/<iface>/statistics/` on both nodes during any bulk transfer.
A staging step that is not moving bytes on the fabric is not using it,
whatever the configuration says.

### 22. Repo gotcha: a committed `hardware/<dir>/` needs a registry entry — **HIT**

*Symptom:* CI red on a docs-only branch:
`committed machine directories not in the registry`.

*Cause:* `tests/test_machines.py` reads **git-tracked** paths. A local suite
run before `git add` passes; CI, which sees the staged tree, does not.

*Fix:* add the entry to `scripts/machines.py` and regenerate
`hardware/MACHINES.md`. **Run `pytest` after `git add`, not before** — that
is the general lesson, and it applies to any test that reads tracked files.

### 23. A large model's default reasoning effort eats the whole token budget — **HIT**

*Symptom:* the cluster serves, `/v1/models` answers, and a plain chat
request comes back with empty or truncated content and
`finish_reason: length`. It looks like a broken template or a dead rank.

*Cause:* the checkpoint's chat template defaults to its highest reasoning
effort, and thinking spends the whole `max_tokens` before the answer starts.
Hit on three two-node arms in a row: Qwen3.8-Flash-Next defaults to `xhigh`
and returned `content: null`; GLM-5.3-Flash renders effort Max and, at
`max_tokens` 600, ended at the cap with 2,473 characters of reasoning and a
cut-off answer; DeepSeek V4-Flash needed the same fix.

*Fix:* launch with reasoning effort **low** as a server default:
`--default-chat-template-kwargs '{"reasoning_effort":"low"}'`, or the recipe's
own variable (GLM: `GLM53_DEFAULT_REASONING_EFFORT=low`). Confirm it in the
server's argv, not the `.env`. Then a plain request with no
`chat_template_kwargs` must end in `finish_reason: stop` with content: GLM
went to 188, 199 and 223 tokens, all `stop`. The operator made this the
default for every large model on 2026-09-22. A higher effort is its own arm
under its own backend name.

## Verification checklist

Run top to bottom. Do not skip a line because the one above it "obviously"
works. Results from 2026-09-22 in the right column.

| # | check | result |
|---|---|---|
| 1 | `sudo ethtool <iface>` — speed and `Link detected: yes`, both nodes | 200000Mb/s, both cage-p1 interfaces, both nodes |
| 2 | `rdma link show` — ACTIVE on cabled devices | ACTIVE on `rocep1s0f1`, `roceP2p1s0f1` |
| 3 | `phys_port_name` — group interfaces by cage, count cages not interfaces | one cabled cage (`p1`) |
| 4 | `ibdev2netdev` — write the ethernet/RoCE mapping down | `enp1s0f1np1`↔`rocep1s0f1`, `enP2p1s0f1np1`↔`roceP2p1s0f1` |
| 5 | ping both subnets, both directions | 0% loss; 1.016 ms and 1.297 ms avg |
| 6 | `ping -M do -s 8972` — jumbo end-to-end | 3/3, no fragmentation |
| 7 | `rp_filter` is 2 on both nodes | already 2, no change |
| 8 | reboot one node; re-run 1–7 unchanged | survived: addresses, MTU 9000, RoCE ACTIVE, no link flap |
| 9 | `ssh` both directions, passwordless | working |
| 10 | same `DGX_SWBUILD_VERSION` and kernel on both | **mismatched** — gotcha 12 |
| 11 | `id` matches on both | uid 1000, gid 1000, both |
| 12 | `ib_write_bw`, one path, then both concurrently | 111.86 / **196.08 Gb/s** |
| 13 | `iperf3 -P 8` on the IP path | 111 Gb/s, 0 retransmits |
| 14 | all-reduce across both nodes (`scripts/cluster_allreduce.py`) | correct; **187.10 Gb/s** median at 1 GiB |
| 15 | only now, a model | #648 |

## Open decisions

Each needs an answer recorded here, not in a session's memory:

- ~~which physical cage pairs with which~~ — resolved, `p1` to `p1`
- ~~the fabric subnets, and netplan or `nmcli`~~ — resolved, `nmcli`,
  10.0.0.0/24 and 10.0.1.0/24
- whether to connect the second cable: it buys redundancy and at most ~14%
  aggregate, not a doubling, because the PCIe budget binds before the wire
  does
- whether to bring the lagging node to the same kernel before clustering, at
  the cost of a reboot
- independent subnets or a bond, if a second cable is added
- which node is the serving head
- which checkpoints and container images are approved to pull (#650) —
  weights and images are executable trust

## References

- #641 — procurement, cabling, the original plan
- #646 — bring-up: addressing, validation, the measured collective
- #647 — the cluster's identity in the machine registry
- #648 — the first model across both nodes
- #649 — end-to-end from a remote client
- #650 — the five third-party two-Spark recipes, and the source decision
- [NVIDIA — Spark Stacking](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html)
- [NVIDIA Sync — Cluster Assistant](https://docs.nvidia.com/sync/latest/cluster-assistant.html)
