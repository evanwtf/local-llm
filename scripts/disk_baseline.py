"""Measure the internal NVMe: read bandwidth and random-read latency.

Issue #34 rests on a number nobody here has ever taken. SSD offload has appeared
three times from three directions -- ds4's `--ssd-streaming-*`, an MLX patch that
memmaps the 51B n-gram table, and AtomicChat's `-M64` GGUFs -- and every "SSD
offload is fine" claim assumes a disk speed this repo does not record.

Two access patterns matter, and they are not the same measurement.

**Sequential and large-block random reads** decide whether streaming *weights*
is viable: an expert block is hundreds of KiB to a few MiB, read on a miss.
Bandwidth is the limit there.

**Small random reads** decide whether the n-gram PLE table is viable. That is
~100 bytes per token at a random offset, and 100 bytes does not cost less than
one block -- the floor is the device's minimum transfer and its latency, not its
bandwidth. Reporting GB/s for that pattern would flatter it by orders of
magnitude, so this reports latency and IOPS instead.

Caching is defeated with `F_NOCACHE`, which is macOS's per-descriptor "do not
keep this in the unified buffer cache". Without it the second pass reads RAM and
reports the speed of memory.

    uv run python scripts/disk_baseline.py --size-gib 8

Do NOT run this while a benchmark batch is in flight. It saturates the disk, and
disk contention during a timing run is how an hour was lost on 2026-08-27.
"""
from __future__ import annotations

import argparse
import fcntl
import logging
import os
import pathlib
import random
import statistics
import sys
import time

logger = logging.getLogger(__name__)

F_NOCACHE = 48  # <sys/fcntl.h>; not exposed by the fcntl module
BYTES_PER_GIB = 1073741824

# F_NOCACHE bypasses the buffer cache only for aligned I/O. Both the offset and
# the length must be multiples of this, or the read is served from RAM and the
# measurement is of memory, not of the device.
BLOCK_ALIGN = 4096

# Below this, a "disk" read is not a disk read. NVMe latency is tens of
# microseconds; a single-digit median means the cache was hit and the run is
# void. Cheaper to refuse than to publish a number that is off by 50x.
IMPLAUSIBLE_US = 10.0


def _uncached(fd: int) -> None:
    """Ask the kernel not to cache this descriptor. macOS only."""
    try:
        fcntl.fcntl(fd, F_NOCACHE, 1)
    except OSError as exc:
        logger.warning("F_NOCACHE failed (%s) -- numbers may be cache speed", exc)


def make_file(path: pathlib.Path, size_gib: float) -> None:
    """Write the test file, uncached, so it is not already in RAM afterwards."""
    if path.exists() and path.stat().st_size >= size_gib * BYTES_PER_GIB:
        logger.info("reusing %s", path)
        return
    logger.info("writing %.1f GiB to %s", size_gib, path)
    t_write = time.monotonic()
    chunk = os.urandom(4 * 1024 * 1024)      # random: no filesystem compression
    written = 0
    target = int(size_gib * BYTES_PER_GIB)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        _uncached(fd)
        while written < target:
            written += os.write(fd, chunk)
        os.fsync(fd)
    finally:
        os.close(fd)
    logger.info("wrote %.1f GiB in %.1f s (%.2f GiB/s)", written / BYTES_PER_GIB,
                time.monotonic() - t_write,
                (written / BYTES_PER_GIB) / (time.monotonic() - t_write))


def sequential_read(path: pathlib.Path, block: int = 4 * 1024 * 1024) -> float:
    """Whole-file sequential read. Returns GiB/s."""
    fd = os.open(path, os.O_RDONLY)
    try:
        _uncached(fd)
        size = os.fstat(fd).st_size
        t0 = time.monotonic()
        read = 0
        while read < size:
            got = os.read(fd, block)
            if not got:
                break
            read += len(got)
        elapsed = time.monotonic() - t0
    finally:
        os.close(fd)
    logger.info("sequential: read %.1f GiB in %.1f s", read / BYTES_PER_GIB, elapsed)
    return (read / BYTES_PER_GIB) / elapsed


def random_read(path: pathlib.Path, block: int, count: int,
                seed: int = 20260828) -> dict[str, float]:
    """`count` random reads of `block` bytes. Returns latency stats and IOPS.

    **Offsets are aligned to `BLOCK_ALIGN`.** F_NOCACHE only bypasses the buffer
    cache for block-aligned I/O; an unaligned read silently falls back to the
    cache and reports the speed of RAM. The first run of this script did exactly
    that and produced a 1.0 us median for a random 128-byte read, with random
    1 MiB reads coming out *faster* than the sequential pass. Both are
    impossible for real disk I/O, which is what gave it away.

    Latencies are per-read, so the median and the p99 are the interesting
    numbers: a lookup table's cost is its tail, not its mean.
    """
    rng = random.Random(seed)
    fd = os.open(path, os.O_RDONLY)
    try:
        _uncached(fd)
        size = os.fstat(fd).st_size
        span = (size - block) // BLOCK_ALIGN
        offsets = [rng.randrange(0, span) * BLOCK_ALIGN for _ in range(count)]
        latencies = []
        t0 = time.monotonic()
        for off in offsets:
            start = time.perf_counter()
            os.pread(fd, block, off)
            latencies.append((time.perf_counter() - start) * 1e6)   # microseconds
        wall = time.monotonic() - t0
    finally:
        os.close(fd)
    latencies.sort()
    return {
        "median_us": statistics.median(latencies),
        "p99_us": latencies[int(len(latencies) * 0.99)],
        "iops": count / wall,
        "gib_s": (count * block / BYTES_PER_GIB) / wall,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Larger than physical RAM on purpose. F_NOCACHE stops the kernel caching
    # *new* reads; it does not evict the pages the write just put there. With an
    # 8 GiB file on a 128 GiB machine the whole thing stays resident and the
    # medians report RAM -- that happened, and the plausibility guard caught it.
    # `purge` would fix it in one line and needs sudo, which an unattended run
    # does not have. Outrunning the cache needs no privileges.
    p.add_argument("--size-gib", type=float, default=160.0)
    p.add_argument("--count", type=int, default=20000,
                   help="random reads per block size")
    p.add_argument("--path", default=None,
                   help="test file location; default is a temp file beside $TMPDIR")
    p.add_argument("--keep", action="store_true", help="do not delete the test file")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    path = pathlib.Path(args.path or (os.environ.get("TMPDIR", "/tmp") + "/nvme-baseline.bin"))

    free = os.statvfs(path.parent)
    free_gib = free.f_bavail * free.f_frsize / BYTES_PER_GIB
    if free_gib < args.size_gib * 1.2:
        raise SystemExit(f"only {free_gib:.1f} GiB free at {path.parent}; need "
                         f"~{args.size_gib * 1.2:.1f} GiB")

    physical = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / BYTES_PER_GIB
    if args.size_gib < physical:
        logger.warning(
            "test file is %.0f GiB against %.0f GiB of RAM -- it may stay resident "
            "and the medians will describe the cache", args.size_gib, physical)

    try:
        make_file(path, args.size_gib)
        seq = sequential_read(path)
        logger.info("sequential read (4 MiB blocks): %.2f GiB/s", seq)

        # 4 KiB is the floor. A 100-byte PLE lookup cannot cost less than one
        # block, so "128 B" is not a separate measurement -- it is this one.
        suspect = False
        for block, label in ((1024 * 1024, "1 MiB"), (65536, "64 KiB"), (4096, "4 KiB")):
            n = args.count if block <= 65536 else max(1000, args.count // 10)
            got = random_read(path, block, n)
            logger.info(
                "random %-6s x%-6d  median %8.1f us  p99 %8.1f us  "
                "%9.0f IOPS  %6.2f GiB/s",
                label, n, got["median_us"], got["p99_us"], got["iops"], got["gib_s"])
            suspect |= got["median_us"] < IMPLAUSIBLE_US
        if suspect or seq > 20.0:
            logger.error(
                "these numbers describe the buffer cache, not the device: a "
                "median under %.0f us or a sequential rate over 20 GiB/s is RAM. "
                "The run is void -- check F_NOCACHE and offset alignment.",
                IMPLAUSIBLE_US)
            return 1
    finally:
        if not args.keep and path.exists():
            path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
