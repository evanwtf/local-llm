"""Per-rep aggregates from the monitord 1 Hz sensor CSV. #276

Used by `scripts/fan_ab_collate.py`. Kept separate because the join is
reusable: any driver that records ISO 8601 rep boundaries can ask this what
the machine was doing during them.

The benchmark records a start and end ISO 8601 stamp for every rep. monitord
samples the machine once a second. So each rep has ~55 samples, and joining
them turns three assertions into measurements:

- **which arm this rep actually ran** -- mean fan rpm, rather than trusting
  that `fancontrol` reporting `forced` means the fans reached maximum.
- **whether the GPU was busy** -- mean `gpu.utilization` across the rep.
- **why a result came out the way it did** -- SoC power. The causal chain a
  positive result needs is: max fans -> cooler die -> less throttling -> higher
  sustained power -> more tok/s. Without power a null cannot be told apart from
  a machine that was never thermally limited, which on a 2.5-minute phase is
  the likelier explanation.

Enclosure temperature comes along as a second reference for the room: it is
not ambient, but it moves with it and costs nothing to carry.
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib
import statistics as st

MONITORD_DIR = pathlib.Path.home() / "Library/Logs/monitor"

COLUMNS = {
    "die_c": "sensor.temperature.gpu (°C)",
    "cpu_c": "sensor.temperature.cpu (°C)",
    "enclosure_c": "sensor.temperature.enclosure (°C)",
    "power_soc_w": "sensor.power.soc (W)",
    "power_input_w": "sensor.power.input (W)",
    "fan1_rpm": "sensor.fan.1.speed (rpm)",
    "fan2_rpm": "sensor.fan.2.speed (rpm)",
    "gpu_util": "gpu.utilization (fraction)",
    "vram_b": "gpu.vram.used (B)",
}
TIME = "time_epoch_ms"


def latest_csv() -> pathlib.Path | None:
    got = sorted(MONITORD_DIR.glob("sensors.*.csv"))
    return got[-1] if got else None


def load(path: pathlib.Path, since: dt.datetime, until: dt.datetime):
    """Rows in [since, until], as (datetime, {name: float}). One pass.

    Bounded by the run window so the whole day is not held in memory.
    """
    lo, hi = since.timestamp() * 1000, until.timestamp() * 1000
    out = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                stamp = float(row[TIME])
            except (KeyError, TypeError, ValueError):
                continue
            if stamp < lo or stamp > hi:
                continue
            values = {}
            for name, column in COLUMNS.items():
                try:
                    values[name] = float(row[column])
                except (KeyError, TypeError, ValueError):
                    pass
            out.append((dt.datetime.fromtimestamp(stamp / 1000).astimezone(), values))
    return out


def aggregate(rows, began: dt.datetime, ended: dt.datetime) -> dict[str, float | int]:
    """mean/max over one rep's window, plus the sample count.

    The count is not decoration: a rep with four samples behind it is a
    different claim from one with fifty-five, and an aggregate alone cannot
    say which it is.
    """
    inside = [values for when, values in rows if began <= when <= ended]
    out: dict[str, float | int] = {"monitord_samples": len(inside)}
    if not inside:
        return out
    for name in COLUMNS:
        series = [v[name] for v in inside if name in v]
        if not series:
            continue
        out[f"{name}_mean"] = round(st.fmean(series), 3)
        out[f"{name}_max"] = round(max(series), 3)
        if name == "die_c":
            out["die_c_min"] = round(min(series), 3)
    # The arm, measured rather than asserted.
    fans = [
        v
        for values in inside
        for key in ("fan1_rpm", "fan2_rpm")
        if (v := values.get(key)) is not None
    ]
    if fans:
        out["fan_rpm_mean_both"] = round(st.fmean(fans), 1)
    return out
