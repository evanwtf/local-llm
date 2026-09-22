# Do I need a second cable for my DGX Spark cluster?

## TL;DR

**One cable can carry the full ~200 Gb/s. The machine cannot carry much more
than that in total. So a second cable is not worth it for speed.**

The NIC sits behind **two PCIe Gen5 x4 devices**, about 126 Gb/s of payload
each — roughly **252 Gb/s of host-to-NIC bandwidth for the whole machine**,
no matter how many cables are plugged in. A single 200 Gb/s cable, driven
properly, already reaches **196.08 Gb/s** of that. The headroom a second cable
can compete for is what is left, and both cages are served by those same two
PCIe devices, so it is not additional capacity.

Measured, rather than argued:

| | one cable | two cables | change |
|---|---|---|---|
| raw RDMA write, all paths at once | 196.08 Gb/s | 218.36 Gb/s | +11.4% |
| **NCCL all-reduce, 1 GiB** | **187.10 Gb/s** | **196.09 Gb/s** | **+4.8%** |

218.36 Gb/s is the practical ceiling, against the ~252 Gb/s the PCIe links are
rated for. Two cables never approach 400 Gb/s, and nothing you configure will
make them.

**What to do instead:** make sure your one cable is using **both** of its PCIe
paths. That is worth **+79%** (109 to 196 Gb/s) and costs nothing but naming
two interfaces instead of one. Most single-cable setups that feel slow are
using one.

Keep the second cable only if you want redundancy against a dead cable or a
dead cage. That is a real benefit, and it is not a throughput one.

---

Measured 2026-09-22 on a two-Spark pair. Everything below is reproducible with
the commands given. Working notes are in #646 and #659.

## Which number matters

Two DGX Sparks cabled directly to each other, no switch.

Of the two rows above, **the all-reduce is the one to care about**. Raw RDMA
between two host buffers is a synthetic number. The all-reduce is what a
tensor-parallel split across two machines actually spends its time on, and it
gains less than half of what the raw link gains.

Each all-reduce figure is the median of three runs. The individual runs, so
the spread is visible rather than hidden behind a median:

- One cable: 184.84, 187.14, 187.10 Gb/s
- Two cables: 196.09, 196.26, 194.12 Gb/s

Three runs is enough to separate 187 from 196. It is not enough to argue about
1%.

## Why a second cable cannot help much

A DGX Spark has **two QSFP cages** on the back. Two cages sounds like two
independent 200 Gb/s pipes, so two cables should give 400.

They are not independent.

```sh
for i in enp1s0f0np0 enP2p1s0f0np0 enp1s0f1np1 enP2p1s0f1np1; do
  echo "$i -> cage $(cat /sys/class/net/$i/phys_port_name)"
done
```

There are **four** network interfaces for **two** cages:

| interface | PCI address | cage |
|---|---|---|
| `enp1s0f0np0` | `0000:01:00.0` | p0 |
| `enp1s0f1np1` | `0000:01:00.1` | p1 |
| `enP2p1s0f0np0` | `0002:01:00.0` | p0 |
| `enP2p1s0f1np1` | `0002:01:00.1` | p1 |

Read the PCI column carefully. There are **two PCIe devices**, `0000:01:00`
and `0002:01:00`, and **each one serves both cages**. Now check how wide they
are:

```sh
for d in 0000:01:00.0 0002:01:00.0; do
  sudo lspci -s $d -vv | grep -E 'LnkCap:|LnkSta:'
done
```

```
LnkCap: Port #0, Speed 32GT/s, Width x4, ASPM not supported
LnkSta: Speed 32GT/s, Width x4
```

**PCIe Gen5 x4** is about 126 Gb/s of usable payload each. Two of them is
about **252 Gb/s of host-to-NIC bandwidth for the whole machine**, however
many cables are plugged in. One 200 Gb/s cable already uses most of it.

## The test setup

**Hardware.** Two NVIDIA DGX Sparks (GB10, 20-core Arm, 128 GB unified memory
each), connected directly. No switch.

**Cables.** Amphenol NJAAKK-N911, 400 mm passive QSFP DAC, NVIDIA part number
`930-51986-0000-000`. NVIDIA names three specific parts for DGX Spark; this is
one of them. The 400 mm length is a real constraint — the two chassis end up
almost touching.

**OS.** DGX OS on Ubuntu 24.04.5. The two machines were not on identical
kernels (`6.17.0-1032-nvidia` and `7.0.0-1019-nvidia`). That did not affect
these results.

**Addressing.** Static point-to-point, one private /24 per interface, no
default route, MTU 9000. NetworkManager is the renderer on DGX OS, so this is
`nmcli` and not a netplan file:

```sh
sudo nmcli con mod <profile> \
  connection.interface-name enp1s0f1np1 \
  ipv4.method manual ipv4.addresses 10.0.0.1/24 \
  ipv4.never-default yes ipv6.method disabled \
  802-3-ethernet.mtu 9000 connection.autoconnect yes
```

Repeat per interface, with the other node taking `.2` on each subnet. Confirm
jumbo frames work end to end before trusting anything. An MTU that disagrees
between the machines passes small pings and then deadlocks under a real
collective:

```sh
ping -M do -s 8972 10.0.0.2      # -M do forbids fragmentation
```

Confirm RoCE is up on both ends:

```sh
rdma link show
ibdev2netdev
```

Each ethernet interface has a matching RoCE device: `enp1s0f1np1` pairs with
`rocep1s0f1`. Both names are needed later — NCCL wants the RoCE device in one
variable and the ethernet interface in another, and they are not
interchangeable.

**One warning before cabling anything.** With no cable attached, a DGX Spark
shows **no ConnectX-7 on the PCI bus at all**, an empty
`/sys/class/infiniband/`, and `mlx5_ib` bound to zero devices. This looks
exactly like a NIC disabled in firmware. It is not. Attach the cable and the
device appears.

## Step 1: the raw link

`ib_write_bw` ships with `perftest`, already installed on DGX OS. Run a server
on one node and point the client at it from the other.

**One PCIe path on its own:**

```sh
# on the peer
ib_write_bw -d rocep1s0f1 -D 15 -s 1048576 --report_gbits

# on this node
ib_write_bw -d rocep1s0f1 -D 15 -s 1048576 --report_gbits 10.0.0.2
```

Result: **109.29 Gb/s**. On a 200 Gb/s link that is disappointing, and the
obvious suspicion is that one queue pair is not enough. It is not that. With
eight queue pairs (`-q 8`) it gives **111.86 Gb/s**, a 2.4% difference. That is
a PCIe wall, not a concurrency limit.

**Both PCIe paths to the same cage at once**, on separate ports so they do not
collide:

```sh
# peer
ib_write_bw -d rocep1s0f1   -p 18515 -D 15 -s 1048576 --report_gbits &
ib_write_bw -d roceP2p1s0f1 -p 18516 -D 15 -s 1048576 --report_gbits &

# this node
ib_write_bw -d rocep1s0f1   -p 18515 -D 15 -s 1048576 --report_gbits 10.0.0.2 &
ib_write_bw -d roceP2p1s0f1 -p 18516 -D 15 -s 1048576 --report_gbits 10.0.1.2 &
wait
```

Result: **98.04 + 98.04 = 196.08 Gb/s**, 98% of the port's rating.

**This is the most useful result here for anyone with one cable.** A single
PCIe path cannot saturate the port. Drive both, or leave 43% of the link
unused. Any NCCL or engine configuration that names one interface is running
at half speed.

## Step 2: add the second cable, and watch every path halve

This is the result that settles the question.

With both cables connected, measure each of the four paths on its own. Nothing
changes — each still gives about 109.3 Gb/s, exactly as with one cable.

Now run **all four at once**, each on its own port and subnet:

```sh
# peer: four servers
ib_write_bw -d rocep1s0f1   -p 18701 -D 20 -s 1048576 --report_gbits &
ib_write_bw -d roceP2p1s0f1 -p 18702 -D 20 -s 1048576 --report_gbits &
ib_write_bw -d rocep1s0f0   -p 18703 -D 20 -s 1048576 --report_gbits &
ib_write_bw -d roceP2p1s0f0 -p 18704 -D 20 -s 1048576 --report_gbits &

# this node: four clients
ib_write_bw -d rocep1s0f1   -p 18701 -D 20 -s 1048576 --report_gbits 10.0.0.2 &
ib_write_bw -d roceP2p1s0f1 -p 18702 -D 20 -s 1048576 --report_gbits 10.0.1.2 &
ib_write_bw -d rocep1s0f0   -p 18703 -D 20 -s 1048576 --report_gbits 10.0.2.2 &
ib_write_bw -d roceP2p1s0f0 -p 18704 -D 20 -s 1048576 --report_gbits 10.0.3.2 &
wait
```

| path | cage | alone | all four at once |
|---|---|---|---|
| `rocep1s0f1` | p1 | 109.28 Gb/s | **54.59 Gb/s** |
| `roceP2p1s0f1` | p1 | 109.27 Gb/s | **54.59 Gb/s** |
| `rocep1s0f0` | p0 | 109.29 Gb/s | **54.59 Gb/s** |
| `roceP2p1s0f0` | p0 | 109.30 Gb/s | **54.59 Gb/s** |

Every one halved. The arithmetic closes:

- 4 paths x 54.59 = **218.36 Gb/s**
- 2 PCIe devices x 109.3 = **218.6 Gb/s**

Those agree to 0.1%. If the four paths were independent, the total would be
437 Gb/s. The machine's total NIC bandwidth is the same whether one cable is
plugged in or two. The second cable splits the same PCIe budget four ways
instead of two.

196.08 to 218.36 Gb/s is **+11.4%**, and that is the best case: a synthetic
benchmark with traffic balanced perfectly across both cages.

## Step 3: measure what actually matters

`ib_write_bw` moves bytes between two host buffers. A model does not do that.
When a model is split across two machines, the thing on the critical path is a
**collective**, usually an all-reduce. A link can reach line rate on
`ib_write_bw` while a collective crawls.

The usual tool is `nccl-tests`. The serving container here had PyTorch and
NCCL but no `nccl-tests` binaries, so this is a short script that does the same
job: a ring all-reduce over a range of message sizes, reported the way
`nccl-tests` reports it.

One detail worth copying: **bus bandwidth is not algorithm bandwidth.**
`nccl-tests` reports `busbw = algbw * 2 * (n - 1) / n`. At two ranks that
factor is 1, so they are equal — but quoting one as the other overstates a
two-node result by 2x, and that mistake does get published.

```python
import torch, torch.distributed as dist, time, statistics

dist.init_process_group(backend="nccl")
world = dist.get_world_size()
torch.cuda.set_device(0)

for size in (1 << 20, 1 << 24, 1 << 28, 1 << 30):
    buf = torch.ones(size // 2, dtype=torch.bfloat16, device="cuda")
    for _ in range(5):  # warmup
        dist.all_reduce(buf)
    torch.cuda.synchronize()
    dist.barrier()

    samples = []
    for _ in range(20):
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        dist.all_reduce(buf)
        torch.cuda.synchronize()
        samples.append(time.perf_counter() - t0)

    secs = statistics.median(samples)  # median, not mean
    algbw = size * 8 / secs / 1e9
    busbw = algbw * 2 * (world - 1) / world
    if dist.get_rank() == 0:
        print(f"{size:>12,}  {secs * 1e3:>8.3f} ms  busbw {busbw:>7.2f} Gb/s")

# a collective that returns fast and WRONG is worse than one that hangs
check = torch.ones(1024, dtype=torch.bfloat16, device="cuda")
dist.all_reduce(check)
assert abs(float(check[0].item()) - float(world)) < 1e-3
```

Run one process per node. **Name every interface and every RoCE device**, or
half the fabric goes unused:

```sh
docker run --rm --network host --ipc=host --gpus all \
  --device /dev/infiniband:/dev/infiniband \
  --ulimit memlock=-1 --ulimit stack=67108864 \
  -e NCCL_SOCKET_IFNAME=enp1s0f1np1,enP2p1s0f1np1,enp1s0f0np0,enP2p1s0f0np0 \
  -e NCCL_IB_HCA=rocep1s0f1,roceP2p1s0f1,rocep1s0f0,roceP2p1s0f0 \
  -e NCCL_IB_DISABLE=0 -e NCCL_CROSS_NIC=1 \
  -v $PWD:/work --entrypoint torchrun <image> \
  --nnodes 2 --node-rank 0 --nproc-per-node 1 \
  --master-addr 10.0.0.1 --master-port 29500 /work/allreduce.py
```

The second node runs the same command with `--node-rank 1`. Start the worker
first.

At 1 GiB, median of three runs each: **187.10 Gb/s with one cable, 196.09 Gb/s
with two. +4.8%.** The collective gains less than half of what the raw link
gained, which is the normal direction — a collective has synchronisation and
reduction work that no amount of wire removes.

## Four traps worth knowing first

Each of these cost real time here, and three of them fail **silently**.

**1. Two interfaces showing a link does not mean two cables.** One cable lights
**both** PCIe paths to one cage, so a single cable makes two of the four
interfaces come up. Count *cages* with `phys_port_name`, not interfaces.

**2. A container without `/dev/infiniband` silently falls back to TCP.** NCCL
logs `NET/IB : No device found`, selects `NET/Socket`, and everything keeps
working — at 16.35 Gb/s instead of 24.20, with no error at default verbosity.
This is the most dangerous one, because the fallback produces plausible
numbers. Confirm the transport rather than inferring it from "it worked":

```sh
NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=NET ...
# want: NET/IB : Using [0]rocep1s0f1:1/RoCE [1]roceP2p1s0f1:1/RoCE
# not:  Using network Socket
```

**3. RDMA needs unlimited locked memory in the container.** Without
`--ulimit memlock=-1` you get `ibv_reg_mr_iova2 failed with error Cannot
allocate memory`, then `ibv_create_qp failed`, and the job dies at startup. The
host's own limit is already unlimited, which is why this only appears inside
Docker.

**4. Reboot after reconfiguring the fabric, before measuring anything.** This
one cost a published wrong answer. After a lot of `nmcli` churn during
bring-up, one node ended up in a state that passed *every* functional check —
link up, RoCE ACTIVE, both subnets pinging, jumbo frames clean, `ib_write_bw`
at 196 Gb/s, a numerically correct all-reduce — while running the
**collective** at 24.20 Gb/s, 13% of the link. Three separate tuning attempts
agreed within 1% of each other, which made it look like a hardware ceiling. It
was three measurements of one broken state. A reboot fixed it with no other
change, and the collective went to 187 Gb/s.

The lesson generalises: **a consistent number is not a correct number**, and
the test for this failure mode is the collective. Not latency, and not
`ib_write_bw` — both looked fine throughout.

## So, should you connect it?

**For throughput: no.** One cable is enough. A second gets 4.8% on the number
that matters, and no tuning improves on that, because the machine runs out of
PCIe lanes before it runs out of wire.

**For redundancy: maybe.** A second cable is the only protection against a dead
DAC or a dead cage, and it is cheap insurance if the cable is already bought.
That is a real reason. It is not the reason people buy the second cable.

**Do these first, in this order:**

1. **Use both PCIe paths on the one cable.** Worth **+79%** (109 to 196 Gb/s)
   and it costs nothing but naming two interfaces instead of one.
2. **Check the transport is really RDMA**, not a silent TCP fallback. Worth
   roughly 48% on the collective, and invisible unless you look.
3. **Reboot after configuring, before benchmarking.** Worth up to 7.7x if you
   are in the bad state, and nothing if you are not — and you cannot tell which
   without checking.

The second cable is what is left after all three, and it is worth 4.8%.

## What this does not claim

- **This is two nodes.** Direct cabling also covers three; four needs a switch.
  The PCIe argument still applies per machine, but the numbers do not transfer
  unchanged.
- **This is a synthetic collective, not a model.** The next honest question is
  whether 187 or 196 Gb/s is even the limiting factor for a model split across
  two Sparks. For many workloads it is not, in which case the second cable
  matters even less than 4.8% suggests.
- **Three runs is a small sample.** Enough to separate 187 from 196, not enough
  to argue about 1%.
- **GPUDirect RDMA is off** on this hardware — `GPU Direct RDMA Disabled for
  HCA` on every device. A GB10's "GPU memory" *is* host memory, and the usual
  GDR knobs (`NCCL_NET_GDR_LEVEL`, `NCCL_DMABUF_ENABLE`) changed nothing
  measurable. Worth knowing before spending an evening on them.
- **Your PCIe width could differ.** Check `LnkSta` on your own machines before
  assuming this generalises. If a later revision wires those NICs wider, the
  whole argument changes and the second cable starts earning its place.

If you get a different answer on your pair, the `lspci` width is the first
thing to compare.
