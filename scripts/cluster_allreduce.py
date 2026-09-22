"""Measure NCCL all-reduce bandwidth across the DGX Spark cluster's nodes.

The fabric can reach line rate on `ib_write_bw` and still stall the moment a
collective runs -- an unpinned interface makes `ncclCommInitRank` wait forever,
and an MTU that disagrees between the nodes passes every ping and deadlocks
under a real all-reduce. This is the test that catches both, and it is the last
gate before a model is allowed onto the cluster (#646).

The image that serves the model carries torch and NCCL but no `nccl-tests`
binaries, so this reproduces the part of `all_reduce_perf` that matters: a
ring all-reduce over a range of sizes, reported as algorithm and bus bandwidth
the same way, so the numbers are comparable to published ones.

Run one process per node, under torchrun, inside the serving image:

    torchrun --nnodes 2 --node-rank <0|1> --nproc-per-node 1 \
        --master-addr <head fabric ip> --master-port 29500 \
        scripts/cluster_allreduce.py

`NCCL_SOCKET_IFNAME` and `NCCL_IB_HCA` must name **every** fabric interface and
HCA. On a DGX Spark each QSFP cage is reached over two PCIe Gen5 x4 paths, and
one path walls at about 112 Gb/s -- naming a single interface silently costs
43% of a 200 Gb/s link.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

#: Sizes to sweep, in bytes. The small end shows latency, the large end shows
#: bandwidth; a link that is fine at 1 MiB and collapses at 1 GiB is the
#: interesting failure, so the sweep has to span both.
DEFAULT_SIZES = (
    1 << 20,
    1 << 22,
    1 << 24,
    1 << 26,
    1 << 28,
    1 << 30,
)


def bus_bandwidth_gbps(size_bytes: int, seconds: float, world: int) -> float:
    """Bus bandwidth in Gbit/s, by nccl-tests' definition for all-reduce.

    nccl-tests reports `busbw = algbw * 2 * (n - 1) / n`, which is the traffic
    each link actually carries rather than the payload the caller sees. Quoting
    algbw as though it were busbw overstates a two-node result by 2x, so both
    are returned and the caller prints both.
    """
    algbw = size_bytes * 8 / seconds / 1e9
    return algbw * 2 * (world - 1) / world


def run(sizes: tuple[int, ...], iters: int, warmup: int) -> list[dict[str, float]]:
    import torch
    import torch.distributed as dist

    dist.init_process_group(backend="nccl")
    rank = dist.get_rank()
    world = dist.get_world_size()
    torch.cuda.set_device(0)

    rows: list[dict[str, float]] = []
    for size in sizes:
        buf = torch.ones(size // 2, dtype=torch.bfloat16, device="cuda")

        for _ in range(warmup):
            dist.all_reduce(buf)
        torch.cuda.synchronize()
        dist.barrier()

        samples: list[float] = []
        for _ in range(iters):
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            dist.all_reduce(buf)
            torch.cuda.synchronize()
            samples.append(time.perf_counter() - t0)

        # The median, not the mean: one scheduling hiccup in a short timing
        # loop drags a mean far enough to change the conclusion.
        secs = statistics.median(samples)
        algbw = size * 8 / secs / 1e9
        row = {
            "bytes": size,
            "seconds_median": secs,
            "algbw_gbps": algbw,
            "busbw_gbps": bus_bandwidth_gbps(size, secs, world),
        }
        rows.append(row)
        if rank == 0:
            print(
                f"{size:>12,}  {secs * 1e3:>9.3f} ms  "
                f"algbw {algbw:>7.2f} Gb/s  busbw {row['busbw_gbps']:>7.2f} Gb/s",
                flush=True,
            )

    # Correctness, not just speed: every rank contributed ones, so every
    # element must equal the world size. A collective that returns fast and
    # wrong is worse than one that hangs, because nothing downstream notices.
    check = torch.ones(1024, dtype=torch.bfloat16, device="cuda")
    dist.all_reduce(check)
    expected = float(world)
    got = float(check[0].item())
    if abs(got - expected) > 1e-3:
        raise SystemExit(f"all_reduce returned {got}, expected {expected}")
    if rank == 0:
        print(f"correctness: all_reduce of ones == {got} across {world} ranks")

    dist.destroy_process_group()
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iters", type=int, default=20)
    p.add_argument("--warmup", type=int, default=5)
    p.add_argument(
        "--json",
        metavar="PATH",
        help="also write the rows here, for the issue record",
    )
    args = p.parse_args(argv)

    rows = run(DEFAULT_SIZES, args.iters, args.warmup)

    if args.json and os.environ.get("RANK", "0") == "0":
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
