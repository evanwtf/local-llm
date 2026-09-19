#!/usr/bin/env python3
"""Wait until macOS's background daemons are idle before a timed run. #499, #214

After a boot or an OS upgrade, macOS re-indexes and re-analyzes for hours. On
2026-09-18, two hours after the macOS 27 upgrade, `mediaanalysisd` was at 227%
CPU and the Spotlight daemons at 45% while a benchmark ran (#499). Preflight
sees model servers and other benchmarks, not these daemons (the #214 gap).

This samples `ps` and exits 0 once every watched daemon is at or under the
limit on `--samples` consecutive samples. It exits 1 when the deadline passes
first. A lull between two busy samples does not count as settled.

    uv run python scripts/daemons_idle.py                      # one check now
    uv run python scripts/daemons_idle.py --samples 2 --interval 60 --deadline 10800
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Protocol

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import logs

logger = logging.getLogger(__name__)

# The daemons seen busy after a boot or an upgrade on the M5 Max (#499).
WATCHED = (
    "mediaanalysisd",
    "photolibraryd",
    "spotlightknowledged",
    "spotlightknowledged.updater",
    "corespotlightd",
    "hybridsearchd",
    "mds_stores",
    "backupd",
)


class Clock(Protocol):
    def time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


def parse_ps(text: str) -> list[tuple[float, str]]:
    """(cpu %, command basename) per line of `ps -Ao %cpu,comm` output."""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            cpu = float(parts[0])
        except ValueError:
            continue  # the header
        rows.append((cpu, parts[1].rsplit("/", 1)[-1]))
    return rows


def busy(rows: list[tuple[float, str]], limit: float) -> dict[str, float]:
    """Watched daemons over `limit`, with their summed CPU %."""
    total: dict[str, float] = {}
    for cpu, name in rows:
        if name in WATCHED:
            total[name] = round(total.get(name, 0.0) + cpu, 1)
    return {name: cpu for name, cpu in total.items() if cpu > limit}


def sample() -> list[tuple[float, str]]:
    out = subprocess.run(
        ["ps", "-Ao", "%cpu,comm"], capture_output=True, text=True, check=True
    ).stdout
    return parse_ps(out)


def wait_settled(
    sampler: Callable[[], list[tuple[float, str]]],
    *,
    samples: int,
    interval: float,
    deadline: float,
    limit: float,
    clock: Clock = time,
) -> bool:
    """True after `samples` consecutive idle samples; False at the deadline."""
    start = clock.time()
    streak = 0
    while True:
        over = busy(sampler(), limit)
        streak = 0 if over else streak + 1
        logger.info(
            "busy: %s (idle streak %d of %d)",
            ", ".join(f"{n} {c}%" for n, c in sorted(over.items())) or "none",
            streak,
            samples,
        )
        if streak >= samples:
            return True
        if clock.time() - start + interval > deadline:
            return False
        clock.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--limit", type=float, default=10.0, help="CPU %% per daemon")
    p.add_argument("--samples", type=int, default=1)
    p.add_argument("--interval", type=float, default=60.0, help="seconds")
    p.add_argument("--deadline", type=float, default=0.0, help="seconds")
    args = p.parse_args(argv)
    logs.configure()
    ok = wait_settled(
        sample,
        samples=args.samples,
        interval=args.interval,
        deadline=args.deadline,
        limit=args.limit,
    )
    if ok:
        logger.info("SETTLED: every watched daemon is at or under %.0f%%", args.limit)
        return 0
    logger.error("NOT SETTLED by the %.0f s deadline", args.deadline)
    return 1


if __name__ == "__main__":
    sys.exit(main())
