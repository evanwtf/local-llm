# Setting up and operating the dual DGX Spark cluster

How the two Sparks are joined, how to prove each layer works before trusting
the one above it, and what each layer looks like when it fails. Bring-up is
tracked in #646; the ledger separation the cluster needs is #647.

**Read this before touching either node's network configuration, before
launching anything that spans both nodes, and before concluding that the
fabric is at fault.**

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

**Unexplained: RTT is ~1 ms where a direct DAC should give tens of
microseconds.** Throughput is at line rate, so this is not a link fault;
CPU idle states are the likely cause. Recorded, not chased.

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

**PLANNED.** The structural test, and the one that catches the failure this
whole document exists for: a link that pings and still stalls.

```sh
# nccl-tests, across both nodes
NCCL_SOCKET_IFNAME=<ethernet ifaces, both rails>
NCCL_IB_HCA=<roce devices>
NCCL_IB_DISABLE=0
NCCL_NET_GDR_LEVEL=5
all_reduce_perf ...
```

**Pin the interfaces explicitly. This is the known hang.** MiaAI-Lab's
two-Spark recipe records it plainly (REPORTED):

> Ray may use the `10.0.0.1`/`10.0.0.2` aliases; **NCCL cannot** — `start.sh`
> pins the CX7 NICs and IB HCAs so `ncclCommInitRank` does not hang.

The failure signature is a process that starts, prints nothing further, and
never exits — `ncclCommInitRank` waiting forever. It is not a crash, there is
no error message, and it looks exactly like a slow model load. If a two-node
launch appears to hang during initialisation, check the interface pinning
before anything else.

Record the achieved bandwidth in #646. **200 Gb/s is the port's rating, not a
measurement**, and must never be quoted as one.

---

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

### 1. The ConnectX-7 does not exist until a cable is plugged in — **HIT**

*Symptom:* no Mellanox device in `lspci`, `/sys/class/infiniband/` empty,
`ibdev2netdev` and `rdma link show` print nothing, `mlx5_ib` bound to zero
devices. Looks exactly like a NIC disabled in firmware.

*Cause:* the port does not enumerate on the PCI bus with no cable attached.
This is normal for this integrated NIC and is **not** how ordinary PCIe
devices behave, which is why it reads as a fault.

*Fix:* attach the cable. Do not debug a missing ConnectX-7 on an uncabled
Spark. This cost #641 an open question for weeks.

### 2. Four interfaces, two physical cages — **HIT**

*Symptom:* `enp1s0f0np0`, `enp1s0f1np1`, `enP2p1s0f0np0`, `enP2p1s0f1np1`
for what is obviously a two-port NIC.

*Cause:* each QSFP cage is reachable over **two PCIe paths** (domains
`0000:01:00.x` and `0002:01:00.x`). The interface name does not tell you
which cage it speaks for.

*Fix:* `cat /sys/class/net/<iface>/phys_port_name` — `p0` or `p1`. Binding a
static address to an interface on the uncabled cage produces a silent
blackhole: the config applies, nothing moves.

### 3. Two interfaces up does **not** mean two cables — **HIT**

*Symptom:* two interfaces at 200000Mb/s with `Link detected: yes`, two
reporting `No cable`, and two cables sitting in the box. Reads as "both
cables are in."

*Cause:* one cable lights **both PCIe paths to one cage**. This bring-up
recorded "both cables are in" and was wrong; it was a single cable between
cage `p1` on each node.

*Fix:* count *cages* with a link, not interfaces. Group interfaces by
`phys_port_name` first, then count.

### 4. One PCIe path cannot saturate the port — **HIT**

*Symptom:* a correctly configured 200 Gb/s link benchmarks at ~110 Gb/s and
more queue pairs do not help.

*Cause:* each PCIe path is **Gen5 x4**, about 126 Gb/s of payload. Measured:
109.29 Gb/s at 1 QP, 111.86 at 8 QPs — a 2.4% spread, so the wall is PCIe,
not concurrency.

*Fix:* drive **both** paths concurrently; that reaches 196.08 Gb/s, 98% of
the port rating. Give every NCCL/Ray/engine configuration both interface
names and both HCA names. Naming one silently halves the fabric.

### 5. NetworkManager profile names map to different interfaces on each node — **HIT**

*Symptom:* the same `nmcli` command run on both nodes leaves the subnets
crossed — the peers do not ping even though every address looks right.

*Cause:* DGX OS pre-creates `Wired connection 1..5` DHCP profiles, and the
number-to-interface binding is **not the same on two machines**. Node B's
"Wired connection 5" was a different interface than node A's.

*Fix:* never address a profile by its shipped name. Pin it explicitly with
`connection.interface-name <iface>`, and verify with
`nmcli -t -f NAME,DEVICE con show --active`.

### 6. Reusing a profile while the address is live fails — **HIT**

*Symptom:* `Connection activation failed: IP configuration could not be
reserved (no available address, timeout, etc.)`.

*Cause:* duplicate address detection. Moving an address between two profiles
while the old one is still up means the address exists twice for a moment.

*Fix:* `nmcli con down` **both** profiles, then modify, then bring both up.
The error names DHCP-ish causes and is really a duplicate-address conflict.

### 7. Configure with `nmcli`, not netplan — **AVOIDED**

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

### 8. MTU mismatch passes ping and deadlocks collectives — **CLEAR**

*Symptom:* everything works until a real collective, which hangs.

*Cause:* MTU 9000 on one side only. Small ICMP fits either way; a full-size
frame does not.

*Fix:* set MTU with the address, in the same command, on both nodes. Verify
with `ping -M do -s 8972 <peer>` — `-M do` forbids fragmentation, so it
fails loudly instead of silently degrading. Verified here, 3/3.

### 9. Strict reverse-path filtering drops replies across two subnets — **CLEAR**

*Symptom:* traffic sent on one subnet and answered on the other is dropped
with no log.

*Cause:* `rp_filter=1` (strict) rejects packets whose reply route does not
match the inbound interface — the normal case when two subnets share a wire.

*Fix:* `rp_filter=2` (loose). **Already 2 on both nodes here**, on `all`,
`default` and each fabric interface — DGX OS ships it that way. Check rather
than assume, in both directions.

### 10. mDNS leaks fabric lookups onto the management network — **AVOIDED**

*Cause:* both units ship with `spark-xxxx.local` mDNS names. Back-to-back
with no upstream DNS, resolution for the peer falls back to Wi-Fi or the
management ethernet — so a "fabric" connection silently runs at 1 GbE.

*Fix:* hardcode the fabric addresses in `/etc/hosts` on both nodes and point
every launcher at those names. Never use `.local` for cluster traffic.
Done here: `spark-a-cx7` / `spark-b-cx7`.

### 11. `apt-get upgrade` will not move you to a new kernel — **HIT**

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

### 12. Key auth configured does not mean this account can connect — **HIT**

*Symptom:* `Host key verification failed`, which reads as an authentication
problem.

*Cause:* the peer is absent from **this** account's `known_hosts`. Key auth
was set up; the host key was not accepted for every account that needs it.

*Fix:* accept it once per account, in **both** directions. Collective
launchers and every `docker save | ssh docker load` step need the reverse
direction too, and a one-way setup passes a casual test.

### 13. NCCL cannot use IP aliases, and hangs rather than errors — **REPORTED**

*Symptom:* a two-node launch starts, prints nothing further, and never exits.
Indistinguishable from a slow model load.

*Cause:* `ncclCommInitRank` waiting forever because NCCL was not pinned to
the fabric. Ray tolerates aliases; NCCL does not.

*Fix:* set `NCCL_SOCKET_IFNAME` (ethernet names, **both**) and `NCCL_IB_HCA`
(RoCE device names, **both**) explicitly. Check this before suspecting the
fabric.

### 14. DAC link flap after a one-sided reboot — **REPORTED**

*Symptom:* `NO-CARRIER` on an interface that is physically plugged in, after
one node reboots while the other stays up.

*Fix:* `sudo ip link set <iface> down && sudo ip link set <iface> up`, and
check FEC negotiation with `sudo ethtool --show-fec <iface>`.

### 15. The OOM killer cannot see the model — **REPORTED**

*Symptom:* something innocent is killed under memory pressure while the
process actually consuming memory survives.

*Cause:* on a Spark, host RAM *is* GPU memory, and weights live in driver
allocations not charged to any process's RSS. The OOM killer scores by RSS.

*Fix:* watch `MemAvailable` **after a long prompt**, not after a boot — the
floor comes during prefill, not at load. Disable `earlyoom` on both nodes if
present.

### 16. Repo gotcha: a committed `hardware/<dir>/` needs a registry entry — **HIT**

*Symptom:* CI red on a docs-only branch:
`committed machine directories not in the registry`.

*Cause:* `tests/test_machines.py` reads **git-tracked** paths. A local suite
run before `git add` passes; CI, which sees the staged tree, does not.

*Fix:* add the entry to `scripts/machines.py` and regenerate
`hardware/MACHINES.md`. **Run `pytest` after `git add`, not before** — that
is the general lesson, and it applies to any test that reads tracked files.

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
| 8 | reboot one node; re-run 1–7 unchanged | **not yet done** |
| 9 | `ssh` both directions, passwordless | working |
| 10 | same `DGX_SWBUILD_VERSION` and kernel on both | **mismatched** — gotcha 11 |
| 11 | `id` matches on both | uid 1000, gid 1000, both |
| 12 | `ib_write_bw`, one path, then both concurrently | 111.86 / **196.08 Gb/s** |
| 13 | `iperf3 -P 8` on the IP path | 111 Gb/s, 0 retransmits |
| 14 | `all_reduce_perf` across both nodes | **not yet done** |
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
