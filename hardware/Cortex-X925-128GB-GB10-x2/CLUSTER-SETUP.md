# Setting up and operating the dual DGX Spark cluster

How the two Sparks are joined, how to prove each layer works before trusting
the one above it, and what each layer looks like when it fails. Bring-up is
tracked in #646; the ledger separation the cluster needs is #647.

**Read this before touching either node's network configuration, before
launching anything that spans both nodes, and before concluding that the
fabric is at fault.**

## Status of every claim in this document

The cluster is **not assembled**. To keep that honest, every factual claim is
tagged:

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

**PLANNED. Nothing is configured.** As of 2026-09-22 no ConnectX-7 interface
on either node holds an address, so every packet between the nodes — including
SSH — crosses the 1 GbE management interface. The 200 Gb/s fabric carries
nothing.

The intended shape: static point-to-point addressing, one private subnet per
link, no DHCP, no default route. Node A takes the lower host address on each
subnet, node B the higher.

Two constraints that are easy to get wrong:

- **Pick a range that cannot collide with the LAN**, and do not put the fabric
  on the LAN's subnet. The fabric is not a route to anywhere; it is a private
  wire between two boxes.
- **The renderer here is NetworkManager**, not `networkd`. `/etc/netplan/`
  holds only `00-installer-config.yaml`, which sets `renderer: NetworkManager`
  and no `ethernets` stanza. So either an `nmcli` connection profile or a
  netplan stanza will work — **choose one and record which**, because a
  half-migrated configuration that works until reboot is the expensive
  outcome.

Whichever is chosen must survive a reboot, and MTU 9000 goes on at the same
time as the address: raising MTU later, on one side only, produces a link that
passes small pings and drops under load, which is the single most misleading
failure in this whole stack.

### Bonding, and why the default is not to

**PLANNED — decision open.** With one cable connected there is nothing to
bond for bandwidth: the two live interfaces are two PCIe paths to a single
wire, and no bonding mode makes one cable carry more than 200 Gb/s. Bonding
them would buy path redundancy against a PCIe-path failure, not throughput.

If the second cable is connected later, two genuinely independent wires
exist and the question becomes real. Even then the default should be **two
independent subnets, not a bond**. LACP needs a switch and there is none,
leaving `balance-rr` or `active-backup`. NVIDIA's own guidance and every
third-party recipe read here configure independent interfaces and hand *both*
names to NCCL via `NCCL_SOCKET_IFNAME`, letting the collective library use
both rails itself. That is also easier to diagnose: with a bond, a single
failed rail degrades silently.

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

ACTIVE here means the link layer is up. It does **not** mean RDMA can move
data — that needs `ib_write_bw` between the two nodes, which is a layer-3
test and belongs in #646's results.

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

## Failure modes, and what each looks like

| symptom | most likely cause | check |
|---|---|---|
| no ConnectX-7 on the PCI bus | no cable attached | attach the cable; the device appears |
| `Host key verification failed` | this account's `known_hosts` lacks the peer | accept the key once |
| ping works, large transfers stall | MTU set on one side only | compare MTU on both ends |
| `ncclCommInitRank` never returns | NCCL not pinned to the fabric NIC/HCA | set `NCCL_SOCKET_IFNAME` and `NCCL_IB_HCA` |
| a rail is silently unused | bonded interfaces hiding a dead link | prefer independent subnets |
| addressing lost after reboot | configuration applied live but not persisted | reboot and re-verify, every time |
| a process is OOM-killed but not the big one | Spark weights are invisible to RSS-based scoring | watch `MemAvailable` during prefill |
| cluster serves short replies, stalls on long ones | inter-node path degrading under sustained decode | a smoke test will not find it; run a real task |
| results appear in the single-Spark tables | the cluster has no machine identity yet | #647 |

## Verification checklist

Run top to bottom. Do not skip a line because the one above it "obviously"
works.

1. `sudo ethtool <iface>` — speed and `Link detected: yes`, both nodes
2. `rdma link show` — ACTIVE on the cabled devices, both nodes
3. `ibdev2netdev` — the ethernet/RoCE mapping, written down
4. ping both fabric subnets, both directions, sub-millisecond
5. reboot one node; re-run 1–4 unchanged
6. `ssh` both directions, passwordless, no prompt
7. same `DGX_SWBUILD_VERSION` and kernel on both
8. `id` matches on both
9. `ib_write_bw` between the nodes — record the number
10. `iperf3` multi-stream on the IP path — record the number
11. `all_reduce_perf` across both nodes — record the bandwidth
12. only now, a model

## Open decisions

Each needs an answer recorded here, not in a session's memory:

- which physical port pairs with which
- the fabric subnets, and netplan or `nmcli`
- independent subnets or a bond
- which node is the serving head
- which checkpoints and container images are approved to pull (#650) —
  weights and images are executable trust, and most of the third-party
  recipes' artifacts are undecided

## References

- #641 — procurement, cabling, the original plan
- #646 — bring-up: addressing, validation, the measured collective
- #647 — the cluster's identity in the machine registry
- #648 — the first model across both nodes
- #649 — end-to-end from a remote client
- #650 — the five third-party two-Spark recipes, and the source decision
- [NVIDIA — Spark Stacking](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html)
- [NVIDIA Sync — Cluster Assistant](https://docs.nvidia.com/sync/latest/cluster-assistant.html)
