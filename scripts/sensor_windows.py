"""Join a monitord sensor series to benchmark sweep windows.

#116 asked for a thermal series over a real run and the harness could not
produce one: it samples fans and die temperature at launch and at finish, so
it cannot show a time-to-plateau curve or whether a machine drifted between
arms. monitord samples every second from outside any process, and
`time_epoch_ms` joins straight to a sweep window.

This reports, per window: die temperature, fan RPM and package power, as
median and max, plus the drift from the first window to the last. It is
**context, never a screen.** A pre-registered read-out is not reopened
because a temperature moved; a difference here is a hypothesis for #116 to
test deliberately, with the fans as the controlled variable.

The join is the part worth testing. `sweep-order.txt` records local wall
clock as HH:MM:SS with no date, and the sensor CSV records UTC plus epoch
millis. Reading either naively puts every window in the wrong place -- which
is exactly how #138's read-out once bucketed 45 of 60 rows into nothing.

    uv run python scripts/sensor_windows.py sweep-order.txt sensors.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import pathlib
import statistics
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import provenance

logger = logging.getLogger(__name__)

COLUMNS = {
    "gpu_c": "sensor.temperature.gpu (°C)",
    "cpu_c": "sensor.temperature.cpu (°C)",
    "fan1": "sensor.fan.1.speed (rpm)",
    "fan2": "sensor.fan.2.speed (rpm)",
    "power_w": "sensor.power.input (W)",
}


def read_sensors(path: pathlib.Path) -> list[dict[str, float]]:
    """Rows as {epoch_ms, gpu_c, ...}. Missing values are dropped per column,
    not per row: a sensor that reads empty once must not discard the others."""
    out = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                sample: dict[str, float] = {"epoch_ms": float(row["time_epoch_ms"])}
            except (KeyError, ValueError):
                continue
            for name, column in COLUMNS.items():
                raw = row.get(column, "")
                if raw not in ("", None):
                    try:
                        sample[name] = float(raw)
                    except ValueError:
                        pass
            out.append(sample)
    return out


def read_windows(path: pathlib.Path, day: dt.date, tz: dt.tzinfo | None = None):
    """Parse `tag HH:MM:SS HH:MM:SS` lines into epoch-ms windows.

    The file carries local wall clock and no date, so the date comes from the
    sensor series. A window whose finish is before its start has crossed
    midnight and takes the next day -- otherwise it silently becomes an empty
    window and its rows vanish from the report.
    """
    windows = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) != 3:
            if line.strip():
                logger.warning("ignoring malformed sweep-order line: %r", line)
            continue
        tag, start_s, finish_s = parts
        try:
            start_t = dt.time.fromisoformat(start_s)
            finish_t = dt.time.fromisoformat(finish_s)
        except ValueError:
            logger.warning("ignoring unparseable times on line: %r", line)
            continue
        start = dt.datetime.combine(day, start_t, tzinfo=tz)
        finish = dt.datetime.combine(day, finish_t, tzinfo=tz)
        if finish < start:
            finish += dt.timedelta(days=1)
        windows.append((tag, start.timestamp() * 1000, finish.timestamp() * 1000))
    return windows


def summarise(samples, windows):
    """Per-window medians and maxima. A window with no samples is reported as
    such rather than omitted -- an empty window is a join failure, and hiding
    it is how a bad join looks like a clean one."""
    report = []
    for tag, start, finish in windows:
        inside = [s for s in samples if start <= s["epoch_ms"] <= finish]
        entry: dict[str, object] = {
            "tag": tag,
            "samples": len(inside),
            "minutes": (finish - start) / 60000.0,
        }
        for name in COLUMNS:
            values = [s[name] for s in inside if name in s]
            if values:
                entry[f"{name}_median"] = statistics.median(values)
                entry[f"{name}_max"] = max(values)
        report.append(entry)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sweep_order", type=pathlib.Path)
    parser.add_argument("sensors", type=pathlib.Path)
    args = parser.parse_args(argv)

    samples = read_sensors(args.sensors)
    if not samples:
        logger.error("no usable samples in %s", args.sensors)
        return 2
    # The sweep file records local wall clock, so the series has to be read
    # in the same zone or every window lands hours away from its samples.
    tz = dt.datetime.now().astimezone().tzinfo
    day = dt.datetime.fromtimestamp(samples[0]["epoch_ms"] / 1000, tz=tz).date()
    windows = read_windows(args.sweep_order, day, tz)
    if not windows:
        logger.error("no windows in %s", args.sweep_order)
        return 2

    logger.info(
        "%d samples, %d windows, local date %s", len(samples), len(windows), day
    )
    empty = 0
    rows = summarise(samples, windows)
    for entry in rows:
        if not entry["samples"]:
            empty += 1
            logger.warning(
                "%s: NO SAMPLES in window -- the join found nothing", entry["tag"]
            )
            continue
        logger.info(
            "%-12s %5.1f min n=%-5d gpu %.1f/%.1f C  cpu %.1f/%.1f C  "
            "fans %.0f/%.0f rpm  power %.0f/%.0f W",
            entry["tag"],
            entry["minutes"],
            entry["samples"],
            entry.get("gpu_c_median", float("nan")),
            entry.get("gpu_c_max", float("nan")),
            entry.get("cpu_c_median", float("nan")),
            entry.get("cpu_c_max", float("nan")),
            entry.get("fan1_median", float("nan")),
            entry.get("fan2_median", float("nan")),
            entry.get("power_w_median", float("nan")),
            entry.get("power_w_max", float("nan")),
        )
    if empty:
        logger.error(
            "%d of %d windows matched no samples -- suspect the join, not the machine",
            empty,
            len(windows),
        )
    filled = [e for e in rows if e["samples"] and "gpu_c_median" in e]
    if len(filled) >= 2:
        drift = filled[-1]["gpu_c_median"] - filled[0]["gpu_c_median"]
        logger.info(
            "GPU median drift first->last window: %+.1f C (%s -> %s). Context, not a screen.",
            drift,
            filled[0]["tag"],
            filled[-1]["tag"],
        )
    return 1 if empty else 0


if __name__ == "__main__":
    provenance.configure(show_name=True)
    raise SystemExit(main())
