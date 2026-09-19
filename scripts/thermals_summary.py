"""Summarize a `thermals.py --watch --json` log into a run's thermal envelope.

`thermals.py` samples the GPU live and keeps no history, so a finished run has
no envelope unless a sampler ran beside it. This reads that sampler's log and
reports what an issue comment needs: the sample count and window, the peak
temperature and power, and the medians over the samples where the GPU was
working. Idle samples (between trials, while weights load) would pull a median
toward the idle floor and hide the sustained load, so the medians count only
samples at or above `--busy-util`.

    uv run python scripts/thermals.py --watch 15 --json --quiet > run.thermals.log &
    uv run python scripts/thermals_summary.py run.thermals.log
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import statistics
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

#: `utilization.gpu` at or above this counts as a working sample.
BUSY_UTIL_PCT = 50


@dataclass(frozen=True)
class Envelope:
    samples: int
    busy_samples: int
    first_utc: str
    last_utc: str
    temp_max_c: float
    power_max_w: float
    temp_median_busy_c: float | None
    power_median_busy_w: float | None
    clock_min_busy_mhz: int | None
    clock_median_busy_mhz: float | None


def parse_samples(lines: Iterable[str]) -> list[dict[str, object]]:
    """The JSON readings in a thermals log, in order.

    Each line is a log prefix and then one JSON object. The prefix is not
    fixed, so this takes the text from the first `{`. A line with no object,
    or one that is not valid JSON (a sampler killed mid-write), is skipped.
    """
    out: list[dict[str, object]] = []
    for line in lines:
        start = line.find("{")
        if start < 0:
            continue
        try:
            obj = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "gpu0_power_w" in obj:
            out.append(obj)
    return out


def _parse_utc(text: str) -> datetime.datetime:
    """An ISO 8601 timestamp as an aware datetime; a bare time is UTC."""
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed


def within(
    samples: Iterable[dict[str, object]],
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, object]]:
    """The samples inside [since, until], compared as parsed times.

    A sampler usually outlives its run by a few ticks, and those ticks belong
    to whatever loaded next. A sample with no `utc` field cannot be placed,
    so a window drops it.
    """
    lo = _parse_utc(since) if since else None
    hi = _parse_utc(until) if until else None
    out: list[dict[str, object]] = []
    for s in samples:
        if lo is None and hi is None:
            out.append(s)
            continue
        stamp = s.get("utc")
        if not isinstance(stamp, str) or not stamp:
            continue
        t = _parse_utc(stamp)
        if (lo is None or t >= lo) and (hi is None or t <= hi):
            out.append(s)
    return out


def envelope(
    samples: Sequence[dict[str, object]], busy_util: int = BUSY_UTIL_PCT
) -> Envelope:
    if not samples:
        raise ValueError("no GPU samples in the log")
    temps = [float(s["gpu0_temp_c"]) for s in samples]  # type: ignore[arg-type]
    powers = [float(s["gpu0_power_w"]) for s in samples]  # type: ignore[arg-type]
    busy = [
        s
        for s in samples
        if float(s.get("gpu0_util_pct", 0)) >= busy_util  # type: ignore[arg-type]
    ]
    busy_temps = [float(s["gpu0_temp_c"]) for s in busy]  # type: ignore[arg-type]
    busy_powers = [float(s["gpu0_power_w"]) for s in busy]  # type: ignore[arg-type]
    busy_clocks = [
        int(s["gpu0_clock_mhz"])  # type: ignore[call-overload]
        for s in busy
        if "gpu0_clock_mhz" in s
    ]
    return Envelope(
        samples=len(samples),
        busy_samples=len(busy),
        first_utc=str(samples[0].get("utc", "")),
        last_utc=str(samples[-1].get("utc", "")),
        temp_max_c=max(temps),
        power_max_w=max(powers),
        temp_median_busy_c=statistics.median(busy_temps) if busy_temps else None,
        power_median_busy_w=statistics.median(busy_powers) if busy_powers else None,
        clock_min_busy_mhz=min(busy_clocks) if busy_clocks else None,
        clock_median_busy_mhz=statistics.median(busy_clocks) if busy_clocks else None,
    )


def describe(env: Envelope) -> str:
    """One line, rounded the way an issue comment quotes it."""

    def fmt(value: float | None, unit: str, digits: int = 0) -> str:
        return "n/a" if value is None else f"{value:.{digits}f} {unit}"

    return (
        f"{env.samples} samples ({env.busy_samples} busy), "
        f"{env.first_utc} to {env.last_utc}; "
        f"peak {env.power_max_w:.0f} W, {env.temp_max_c:.0f} C; "
        f"busy median {fmt(env.power_median_busy_w, 'W')}, "
        f"{fmt(env.temp_median_busy_c, 'C')}; "
        f"busy core clock median "
        f"{fmt(_ghz(env.clock_median_busy_mhz), 'GHz', 1)}, "
        f"min {fmt(_ghz(env.clock_min_busy_mhz), 'GHz', 1)}"
    )


def _ghz(mhz: float | None) -> float | None:
    return None if mhz is None else mhz / 1000.0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("log", type=pathlib.Path)
    p.add_argument("--busy-util", type=int, default=BUSY_UTIL_PCT)
    p.add_argument("--since", help="ISO 8601; keep samples at or after it")
    p.add_argument("--until", help="ISO 8601; keep samples at or before it")
    args = p.parse_args(argv)
    logs.configure()
    samples = within(
        parse_samples(args.log.read_text().splitlines()), args.since, args.until
    )
    try:
        env = envelope(samples, args.busy_util)
    except ValueError as exc:
        logger.error("%s: %s", args.log, exc)
        return 1
    logger.info("%s: %s", args.log.name, describe(env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
