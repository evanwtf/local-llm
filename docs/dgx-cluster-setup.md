# Setting up and operating the dual DGX Spark cluster

How the two Sparks are joined, how to prove each layer works before you trust
the layer above it, and what each layer looks like when it fails.

**Read this before you change either node's network configuration, before you
launch anything that spans both nodes, and before you blame the fabric.**

- To build the pair from nothing, follow
  [`dgx-cluster-howto.md`](dgx-cluster-howto.md): the steps in order. This
  file is the reasoning behind them and the gotcha catalog.
- The current kernel, driver, firmware and fabric readings of each node are in
  [`hardware/Cortex-X925-128GB-GB10-x2/README.md`](../hardware/Cortex-X925-128GB-GB10-x2/README.md).

Bring-up (#646) closed on 2026-09-23. The pair has served models to a remote
client since then (#648, #649). Measurements below carry their date.

## The principle: verify bottom-up, never skip a layer

Each layer fails in a way that looks like a fault one layer up. A model that
hangs on load looks like a vLLM bug and is usually NCCL. An NCCL hang looks
like a fabric outage and is usually an unpinned interface. **Prove each layer
with its own test before you move up.** The hard failures here are silent: a
link can pass every ping and still stall the moment a collective runs.

| layer | what it is | proof it works |
|---|---|---|
| 0 | cable, port | `ethtool` reports a speed and `Link detected: yes` |
| 1 | ethernet link | both ends agree on 200000Mb/s |
| 2 | IP addressing | all four fabric subnets ping, about 1 ms or less |
| 3 | RoCE / RDMA | `rdma link show` ACTIVE; `ib_write_bw` moves data |
| 4 | host trust | passwordless SSH both ways; matching OS build and UID |
| 5 | collectives | `all_reduce_perf` completes across both nodes |
| 6 | engine | the server loads both ranks and answers |
| 7 | serving | a real completion, with tool calls, from a remote client |

---

## Layers 0–1: physical and link

Each Spark has **two QSFP cages**, `p0` and `p1`. Each cage is reached over
**two PCIe paths** (domains `0000:01:00.x` and `0002:01:00.x`). So each node
has four ethernet interfaces and four RoCE devices for two physical cages.
The interface name does not tell you the cage: read
`/sys/class/net/<iface>/phys_port_name`.

Both cages are cabled (two NJAAKK-N911 DACs), cage `p0` to `p0` and `p1` to
`p1`:

| interface | PCI | cage | RoCE device |
|---|---|---|---|
| `enp1s0f1np1` | `0000:01:00.1` | p1 | `rocep1s0f1` |
| `enP2p1s0f1np1` | `0002:01:00.1` | p1 | `roceP2p1s0f1` |
| `enp1s0f0np0` | `0000:01:00.0` | p0 | `rocep1s0f0` |
| `enP2p1s0f0np0` | `0002:01:00.0` | p0 | `roceP2p1s0f0` |

An uncabled cage has no NIC on the PCI bus at all (gotcha 2).

```sh
sudo ethtool <iface> | grep -Ei 'speed|link detected'
cat /sys/class/net/<iface>/phys_port_name
```

**Which cage pairs with which.** With no addresses configured, the interfaces
carry no IPv6 link-local, so a neighbor probe returns nothing. Address one
pair, ping, and swap if it fails. Then record the answer, because every NCCL
and Ray configuration names interfaces, and a wrong pairing hangs rather than
errors. Do not assume straight-through: MiaAI-Lab's published two-Spark
topology cables `enp1s0f1np1` on the head to `enp1s0f0np0` on the worker, and
their recipe works.

## Layer 2: addressing

Static point-to-point addresses through `nmcli`. NetworkManager is the
renderer on DGX OS, so do not add a netplan stanza (gotcha 8). MTU 9000, no
default route, IPv6 off, autoconnect on. One /24 per interface pair:

| profile | interface | node A | node B |
|---|---|---|---|
| `cx7-p1-path0` | `enp1s0f1np1` | 10.0.0.1/24 | 10.0.0.2/24 |
| `cx7-p1-path1` | `enP2p1s0f1np1` | 10.0.1.1/24 | 10.0.1.2/24 |
| `cx7-p0-path0` | `enp1s0f0np0` | 10.0.2.1/24 | 10.0.2.2/24 |
| `cx7-p0-path1` | `enP2p1s0f0np0` | 10.0.3.1/24 | 10.0.3.2/24 |

```sh
sudo nmcli con mod <profile> \
  connection.interface-name <iface> \
  ipv4.method manual ipv4.addresses <addr>/24 ipv4.never-default yes \
  ipv6.method disabled 802-3-ethernet.mtu 9000 connection.autoconnect yes
```

`/etc/hosts` on both nodes names every fabric address, so nothing resolves a
fabric peer through mDNS (gotcha 11). The names are in
[`dgx-cluster-howto.md`](dgx-cluster-howto.md), step 4; the older aliases
`spark-a-cx7` and `spark-b-cx7` also point at 10.0.0.1 and 10.0.0.2.

**Two subnets on one cage is intentional.** It is how both PCIe paths of a
cage are driven at once, which is the only way to reach line rate (Layer 3).

**Independent subnets, not a bond.** LACP needs a switch, and there is none.
NVIDIA's guidance and every third-party recipe read here hand all interface
names to NCCL and let it use the rails itself. A bond also hides a failed
rail: it degrades with no error.

**The second cable** moved the raw link from 196.08 to 218.36 Gb/s and the
all-reduce from 187.10 to 196.09 Gb/s (+4.8%, 2026-09-22). The PCIe budget
binds before the wire does. The full write-up:
[`second-cable-dgx-spark-cluster.md`](second-cable-dgx-spark-cluster.md).

## Layer 3: RoCE and RDMA

```sh
rdma link show        # ACTIVE / LINK_UP on each cabled device
ibdev2netdev          # the RoCE-to-ethernet mapping
```

**NCCL needs both names.** The RoCE device (`rocep1s0f1`) goes in
`NCCL_IB_HCA`, and the ethernet interface (`enp1s0f1np1`) goes in
`NCCL_SOCKET_IFNAME`. They are not interchangeable. ACTIVE means the link
layer is up. It does **not** mean RDMA can move data.

### Throughput, and the PCIe ceiling

`ib_write_bw`, 1 MiB messages, 12–15 s, `--report_gbits`, 2026-09-22, one
cable:

| configuration | Gb/s |
|---|---|
| one PCIe path (`rocep1s0f1`), 1 QP | 109.29 |
| one PCIe path (`rocep1s0f1`), 8 QPs | 111.86 |
| **both paths of cage p1 at once** | **196.08** (98.04 + 98.04) |

TCP for comparison, `iperf3 -P 8` on one path: 111 Gb/s, zero retransmits.

Each PCIe path is **Gen5 x4** (`LnkSta: Speed 32GT/s, Width x4`): about
126 Gb/s of payload, of which 111.86 is 89%. Eight queue pairs moved it only
2.4%, so this is a PCIe wall, not a queue-depth limit. Two consequences:

1. **One PCIe path cannot saturate a port.** Every NCCL, Ray and engine
   configuration must name **all** interfaces and **all** HCAs.
2. **The host-to-NIC budget is about 224 Gb/s** (2 paths × ~112). That is why
   the second cable added only 11.4% raw.

```sh
# server on the peer, client here; one pair per PCIe path, at the same time
ib_write_bw -d <hca> -p <port> -D 15 -s 1048576 --report_gbits [<peer ip>]
```

---

## Layer 4: host trust and parity

- **Passwordless SSH in both directions.** Collective launchers, Ray, and
  every `docker save | ssh docker load` step assume the head reaches the
  worker *and* the worker reaches back. Accept the host key for every name
  and address a launcher uses (gotchas 13 and 20).
- **The same OS build, kernel and driver on both nodes.** A mismatch gives
  every collective failure a second candidate cause. The nodes match since
  2026-09-23 (the README's software table). To move a node, see gotcha 12.
- **The same account:** uid 1000, gid 1000, in `sudo`, on both. The NVIDIA
  Cluster Assistant requires it.

## Layer 5: collectives

`scripts/cluster_allreduce.py` under `torchrun`, one rank per node, inside the
serving image. bf16, median of 20 iterations, bus bandwidth as nccl-tests
defines it (`algbw * 2(n-1)/n`, equal to algbw at two ranks). The check also
asserts that an all-reduce of ones returns the world size.

| date | cables | runs at 1 GiB | median |
|---|---|---|---|
| 2026-09-22 | one | 184.84, 187.14, 187.10 | 187.10 Gb/s |
| 2026-09-22 | two | 196.09, 196.26, 194.12 | 196.09 Gb/s |
| 2026-09-23 | two, after node A moved to kernel 7.0 | 192.07, 185.60, 179.23 | **185.60 Gb/s** |

The gate is **above 180 Gb/s**. A one-cable run before a reboot read
24.20 Gb/s; see gotcha 19 before you trust a low number.

```sh
torchrun --nnodes 2 --node-rank <0|1> --nproc-per-node 1 \
    --master-addr <head fabric ip> --master-port 29500 \
    scripts/cluster_allreduce.py
```

Run it inside the serving image with `--device /dev/infiniband`,
`--ulimit memlock=-1`, `--network host`, `--ipc=host`, and **all** names in
`NCCL_SOCKET_IFNAME` and `NCCL_IB_HCA` (gotchas 17 and 18).

## Layer 6: the engine

Every stack measured on the pair runs one rank per node, tensor-parallel
(TP=2) over the fabric: vLLM (with ExLlamaV3 or DSpark on top), or
TensorFold's own TP2 engine. None uses llama.cpp RPC. What runs, and how:
[`hardware/Cortex-X925-128GB-GB10-x2/howto.md`](../hardware/Cortex-X925-128GB-GB10-x2/howto.md).

The constants across the recipes:

- Docker on both nodes, containers run `--network host --ipc=host`.
- The worker rank starts first, then the head.
- Weights are copied to both nodes at the same path, or shared from the head
  over NFS on the fabric (gotchas 21, 24 and 25).
- The health poll needs a timeout in the thousands of seconds. A large MoE
  took 9 min 7 s from launch to a live API.
- Memory is the first thing to check (gotchas 1 and 16).

## Layer 7: serving to a client

- **Only the head serves the API.** A client pointed at the worker fails in a
  way that looks like a network problem.
- **`/health` is not proof.** A cluster passes a health check with a dead
  peer rank. Wait on a real completion, long enough to cross the inter-node
  hop: a short reply can succeed on a deployment that stalls on sustained
  decode.

---

## Gotchas — the full list

Each one bit during bring-up (**HIT**), was checked and found correct
(**CLEAR**), was avoided by a choice (**AVOIDED**), or comes from a third
party and has not been met here (**REPORTED**). Several are silent. The
numbers are stable: scripts and other docs cite them.

### 1. Memory: check it before anything significant, and stop at 50% — **HIT**

*Symptom:* background jobs on the head killed for memory pressure, 7 GiB of
swap in use, a resident server one allocation from the OOM killer taking
`sshd`. No error from the jobs themselves.

*Cause (#672, 2026-09-22):* GLM-5.3-Flash stayed resident on both nodes after
its result was posted, leaving the head 3–8 GiB available. A 161 GiB
download, a ~20 GB image pull and a throughput sweep went on top. A resident
model is host memory; page cache, image extraction and load tests compete for
what is left.

*Fix:* before a download, an image pull, a launch, a load test or a run,
read `MemAvailable` and swap on **every node the job touches**. **If more than
50% of memory is in use, stop and evaluate** what holds it. Usually the right
course is to stop a server whose result is already posted, with its own
wrapper. Never keep a model "warm" while the next one downloads. After a stop,
confirm `earlyoom` runs on both nodes, then run
`scripts/machine_health.py check` and `scripts/machine_state.py`.

**The #1 DGX rule.** The other entries cost time. This one can cost the node:
when the pool runs out, the OOM killer takes small daemons first, and the
worker can be lost with no console
([`incidents/2026-09-13-oom-lockup.md`](incidents/2026-09-13-oom-lockup.md)).

### 2. The ConnectX-7 does not exist until a cable is plugged in — **HIT**

*Symptom:* no Mellanox device in `lspci`, an empty `/sys/class/infiniband/`,
`ibdev2netdev` and `rdma link show` print nothing, `mlx5_ib` bound to zero
devices. It looks like a NIC disabled in firmware.

*Cause:* the port does not enumerate on the PCI bus with no cable. That is
normal for this integrated NIC.

*Fix:* attach the cable. Do not debug a missing ConnectX-7 on an uncabled
Spark (it cost #641 weeks).

### 3. Four interfaces, two physical cages — **HIT**

*Cause:* each QSFP cage is reached over two PCIe paths, so a two-port NIC
shows four interfaces, and the name does not say which cage.

*Fix:* `cat /sys/class/net/<iface>/phys_port_name` (`p0` or `p1`). A static
address on an uncabled cage's interface is a silent blackhole: the config
applies, and nothing moves.

### 4. Two interfaces up does **not** mean two cables — **HIT**

*Cause:* one cable lights **both PCIe paths to one cage**. Bring-up recorded
"both cables are in" with one cable connected.

*Fix:* count *cages* with a link, not interfaces. Group interfaces by
`phys_port_name` first.

### 5. One PCIe path cannot saturate the port — **HIT**

*Symptom:* a 200 Gb/s link benchmarks at ~110 Gb/s, and more queue pairs do
not help.

*Fix:* drive **both** paths of a cage at once (196.08 Gb/s, Layer 3). Give
every NCCL, Ray and engine configuration all interface and HCA names. Naming
one silently halves the fabric.

### 6. NetworkManager profile names map to different interfaces on each node — **HIT**

*Symptom:* the same `nmcli` command on both nodes leaves the subnets crossed,
and the peers do not ping.

*Cause:* DGX OS pre-creates `Wired connection 1..5` DHCP profiles, and the
number-to-interface binding differs between machines.

*Fix:* never address a profile by its shipped name. Pin it with
`connection.interface-name <iface>`, and verify with
`nmcli -t -f NAME,DEVICE con show --active`.

### 7. Reusing a profile while the address is live fails — **HIT**

*Symptom:* `Connection activation failed: IP configuration could not be
reserved (no available address, timeout, etc.)`. It reads like DHCP.

*Cause:* duplicate address detection: for a moment the address exists twice.

*Fix:* `nmcli con down` **both** profiles, modify, then bring both up.

### 8. Configure with `nmcli`, not netplan — **AVOIDED**

*Cause:* DGX OS uses NetworkManager as the renderer
(`/etc/netplan/00-installer-config.yaml`, `renderer: NetworkManager`).
Static netplan stanzas on top make NetworkManager keep trying DHCP on the same
interfaces: repeated "Activation of network connection failed" popups and
reset routes.

*Fix:* use one manager. To use netplan instead, set `renderer: networkd`,
mark the ConnectX interfaces unmanaged in `/etc/NetworkManager/conf.d/`, and
`chmod 600` the netplan file. The MTU property is `802-3-ethernet.mtu` (or
`ethernet.mtu`); `ipv4.mtu` does not exist.

### 9. MTU mismatch passes ping and deadlocks collectives — **CLEAR**

*Cause:* MTU 9000 on one side only. Small ICMP fits; a full-size frame does
not.

*Fix:* set the MTU with the address, in the same command, on both nodes.
Verify with `ping -M do -s 8972 <peer>`: `-M do` forbids fragmentation, so a
mismatch fails loudly.

### 10. Strict reverse-path filtering drops replies across two subnets — **CLEAR**

*Cause:* `rp_filter=1` rejects a packet whose reply route does not match the
inbound interface, which is the normal case when two subnets share a cage.

*Fix:* `rp_filter=2` (loose). DGX OS ships 2 on `all`, `default` and each
fabric interface. Check it on both nodes rather than assume it.

### 11. mDNS leaks fabric lookups onto the management network — **AVOIDED**

*Cause:* each unit ships with a `spark-xxxx.local` mDNS name. With no
upstream DNS, resolving the peer falls back to Wi-Fi or management ethernet,
so "fabric" traffic silently runs at 1 GbE.

*Fix:* name the fabric addresses in `/etc/hosts` on both nodes, and point
every launcher at those names. Never use `.local` for cluster traffic.

### 12. `apt-get upgrade` will not move you to a new kernel — **HIT**

*Symptom:* both nodes fully upgraded, and still on different kernels
(6.17.0-1032 against 7.0.0-1019, 2026-09-22).

*Cause:* both track `linux-nvidia-hwe-24.04`. The lagging node's candidate
was already the new kernel, but `apt-get upgrade` never installs *new*
packages, and the move needs a new `linux-image-*`.

*Fix:* `sudo apt-get full-upgrade`, then reboot.
`apt-cache policy linux-nvidia-hwe-24.04` shows installed against candidate.
Until the reboot the GPU is unusable (`Driver/library version mismatch`).

*Consequence:* an out-of-tree signed module (`nvfanread`, fan RPM into
node_exporter) must be rebuilt and re-signed for the new kernel, or the fan
series disappears from Prometheus with no error.

### 13. Key auth configured does not mean this account can connect — **HIT**

*Symptom:* `Host key verification failed`, which reads as an authentication
problem.

*Cause:* the peer is absent from **this** account's `known_hosts`.

*Fix:* accept the host key once per account, in **both** directions.

### 14. NCCL cannot use IP aliases, and hangs rather than errors — **REPORTED**

*Symptom:* a two-node launch prints nothing more and never exits. It looks
like a slow model load.

*Cause:* `ncclCommInitRank` waits forever because NCCL is not pinned to the
fabric. Ray tolerates aliases; NCCL does not.

*Fix:* set `NCCL_SOCKET_IFNAME` (ethernet names) and `NCCL_IB_HCA` (RoCE
names) explicitly, with all of them. Check this before you suspect the fabric.

### 15. DAC link flap after a one-sided reboot — **REPORTED**

*Symptom:* `NO-CARRIER` on a plugged-in interface after one node reboots
while the other stays up.

*Fix:* `sudo ip link set <iface> down && sudo ip link set <iface> up`, and
check FEC with `sudo ethtool --show-fec <iface>`.

### 16. The OOM killer cannot see the model — **REPORTED**

*Symptom:* something innocent is killed under memory pressure, and the real
consumer survives.

*Cause:* on a Spark, host RAM *is* GPU memory. Weights live in driver
allocations that are not charged to any process's RSS, and the OOM killer
scores by RSS.

*Fix:* watch `MemAvailable` **after a long prompt**, not after boot: the
floor comes during prefill. Keep `earlyoom` on both nodes at SIGTERM 1.0 GiB,
SIGKILL 0.5 GiB (`-M 1048576,524288`, #700), with `--prefer` naming the
engine processes, so RSS does not choose the victim. Its older 5% line
(~6.1 GiB) sat above the 2.7 GiB GLM-5.3-Flash idles at, and it killed a GLM
launch on 2026-09-23. The fix was the line, not disabling earlyoom.

### 17. A container without `/dev/infiniband` silently falls back to TCP — **HIT**

*Symptom:* the collective runs, returns correct results, and is slow. No
error at default verbosity.

*Cause:* `docker run` without the RDMA devices. NCCL logs
`NET/IB : No device found`, selects `NET/Socket`, and keeps working over TCP:
16.35 Gb/s against 24.20 Gb/s with RDMA, in the same degraded state.

*Fix:* `--device /dev/infiniband:/dev/infiniband`. Confirm the transport with
`NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=NET`: look for `NET/IB : Using [0]...`,
not `Using network Socket`.

**The most dangerous entry here**, because the fallback gives plausible
numbers. Always confirm the transport; never infer it from "it worked".

### 18. RDMA needs unlimited locked memory in the container — **HIT**

*Symptom:* `ibv_reg_mr_iova2 failed with error Cannot allocate memory`, then
`ibv_create_qp failed`, then `ncclSystemError` at init.

*Cause:* Docker's default `memlock` limit. RDMA must pin its memory regions.
The host limit is already `unlimited`, so this appears only in a container.

*Fix:* `--ulimit memlock=-1 --ulimit stack=67108864`.

On kernel 7.0.0-1019 the same ENOMEM has a second cause: Kexec HandOver with
no CMA. Boot with `kho=off` (package `nvidia-spark-grub-kho`); the README's
software table has it.

### 19. A reconfigured node stays degraded until rebooted — **HIT**

*Symptom:* every functional check passes (link up, RoCE ACTIVE, subnets
pinging, jumbo frames clean, `ib_write_bw` at 196 Gb/s, a correct
all-reduce), and the collective runs at 24.20 Gb/s, 13% of the link.

*Cause:* NetworkManager profiles created, renamed, re-addressed and
re-activated many times (gotchas 6 and 7) leave state that
`nmcli con down/up` does not clear.

*Fix:* **reboot both nodes after you configure the fabric, before you measure
anything.** One reboot took the all-reduce from 24.20 to 187.10 Gb/s with no
other change.

*The trap inside the trap:* three tuning attempts agreed within 1%
(24.20 / 24.25 / 24.02). That read as a hardware plateau, and it was
published as one, blamed on GB10's lack of GPUDirect RDMA. GDR is still off at
187 Gb/s. **A consistent number is not a correct number.** The only clue was
ping RTT: 1.016 ms before the reboot, 0.282 ms after. **Treat an unexplained
latency anomaly as a blocker.**

### 20. The fabric addresses are separate SSH identities — **HIT**

*Symptom:* `Host key verification failed` at a launcher's worker step, after
SSH between the nodes was verified.

*Cause:* SSH was verified by the LAN names. A launcher drives the worker by
its fabric address, which is a different host identity, and so is each
`/etc/hosts` alias.

*Cost:* a 157 GiB head download completed, and the worker step died at once,
after 1 h 43 m.

*Fix:* accept every fabric address and alias in **both** directions before a
launch. **Verify SSH by the exact name the launcher uses.**

### 21. Each node may download the whole checkpoint from the internet — **HIT**

*Symptom:* the worker stages its weights slowly, and the fabric is idle.

*Cause:* a recipe default such as `DSPARK_WORKER_HF_NFS=0` gives each node
its own Hugging Face cache, so the worker downloads the checkpoint again.
Interface counters during the copy: 48 MiB/s on the worker's management
interface, 0 on every fabric path. The pair spent 2 × 157 GiB of WAN.

*Fix:* share the head's cache over NFS on the fabric
(`DSPARK_WORKER_HF_NFS=1`, or `--nfs` in sibling recipes), or copy over the
fabric (gotcha 24). **Set it before the first download.** The standing rule
(AGENTS.md): look in the NAS archive first, then download once on one node,
then copy node to node and check the sha256 on each.

*Check:* read `tx_bytes`/`rx_bytes` under `/sys/class/net/<iface>/statistics/`
on both nodes during any bulk transfer.

### 22. A committed `hardware/<dir>/` needs a registry entry — **HIT**

*Symptom:* CI fails on a docs-only branch with
`committed machine directories not in the registry`.

*Cause:* `tests/test_machines.py` reads **git-tracked** paths. A local run
before `git add` passes; CI does not.

*Fix:* add the entry to `scripts/machines.py` and regenerate
`hardware/MACHINES.md`. **Run `pytest` after `git add`** for any test that
reads tracked files.

### 23. A large model's default reasoning effort eats the whole token budget — **HIT**

*Symptom:* the server answers `/v1/models`, and a plain chat request comes
back empty or cut off with `finish_reason: length`. It looks like a broken
template or a dead rank.

*Cause:* the chat template defaults to its highest effort. Qwen3.8-Flash-Next
defaults to `xhigh` and returned `content: null`; GLM-5.3-Flash renders Max;
DeepSeek V4 needed the same fix.

*Fix:* set effort **low** as the server default:
`--default-chat-template-kwargs '{"reasoning_effort":"low"}'`, or the recipe's
own variable (GLM: `GLM53_DEFAULT_REASONING_EFFORT=low`). Confirm it in the
server's argv, not the `.env`. A plain request must then end in
`finish_reason: stop`. Operator rule (2026-09-22): low is the default for
every large model, and a higher effort is its own arm under its own backend
name.

### 24. A node-to-node copy runs at a fifth of the link: one ssh stream is cipher-bound — **HIT**

*Symptom:* `rsync` of a checkpoint to the worker over a fabric name holds at
~310–330 MB/s.

*Cause:* one `rsync` is one ssh connection, which encrypts on one core.

*Fix:* copy over a fabric name, **never a LAN name**, with
`aes128-gcm@openssh.com` (GB10's Arm cores have AES instructions), no
compression, and parallel streams:

```sh
ls "$SNAPSHOT" | sort > files.txt && split -n r/4 -d files.txt part.
for i in 0 1 2 3; do
  rsync -aL --partial --files-from=part.0$i \
    -e 'ssh -c aes128-gcm@openssh.com -o Compression=no' \
    "$SNAPSHOT/" spark-b-cx7:<dest>/ &
done; wait
```

Measured 2026-09-24: one stream with the default cipher, 310–330 MB/s; four
streams with `aes128-gcm`, 1,739 MiB/s on one fabric path. The cipher and the
stream count changed together, so this does not separate them. Run long
copies as a `systemd-run --user` unit, so the session reaper cannot kill them
(#691).

### 25. A recipe's worker sync can copy only symlinks: HF's shared blob store — **HIT**

*Symptom:* a launcher reports `Worker has a local checkpoint copy`, the
worker's model directory holds ~10 MB, and the worker rank fails at load.

*Cause:* the `hf` CLI (xet) stores shards in the **shared**
`~/.cache/huggingface/hub/blobs/`, and the snapshot's safetensors are symlinks
into it. A recipe that `rsync`s `hub/models--<org>--<name>/` copies dangling
symlinks, and a directory check passes (#721).

*Fix:* check the worker **by size, not by directory**:
`find -L <snapshot> -name '*.safetensors' -size +100M | wc -l` must equal the
shard count. Copy with `rsync -L`, as in gotcha 24.

## Verification checklist

Run it top to bottom after any change to either node. Do not skip a line
because the one above "obviously" works.

| # | check | pass |
|---|---|---|
| 1 | `sudo ethtool <iface>`, both nodes | 200000Mb/s, `Link detected: yes`, all four interfaces |
| 2 | `rdma link show` | four ACTIVE per node |
| 3 | `phys_port_name`: group interfaces by cage | two cabled cages |
| 4 | `ibdev2netdev` | the mapping in the Layer 0–1 table |
| 5 | ping all four subnets, both directions | 0% loss, under 2 ms |
| 6 | `ping -M do -s 8972` | no fragmentation |
| 7 | `rp_filter` on both nodes | 2 |
| 8 | reboot both nodes; repeat 1–7 | unchanged |
| 9 | `ssh` both directions, by each fabric name | no password, no host-key prompt |
| 10 | kernel, driver, `kho=off` on both | the same (README software table) |
| 11 | `id` on both | uid 1000, gid 1000 |
| 12 | `ib_write_bw`, one path, then both paths of a cage | about 110, then about 196 Gb/s |
| 13 | all-reduce (`scripts/cluster_allreduce.py`) | correct, and above 180 Gb/s at 1 GiB |
| 14 | only now, a model | the check in the two-Spark howto |

## References

- #641: procurement, cabling, the original plan
- #646: bring-up, addressing, the measured collective
- #647: the cluster's identity in the machine registry
- #648, #649: the first model across both nodes, and the first remote client
- #650: the third-party two-Spark recipes
- #659: the second cable
- [NVIDIA: Spark Stacking](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html)
- [NVIDIA Sync: Cluster Assistant](https://docs.nvidia.com/sync/latest/cluster-assistant.html)
