"""#116: the join is the part that goes wrong silently."""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import sensor_windows

HEADER = (
    "time_epoch_ms,sensor.temperature.gpu (°C),sensor.temperature.cpu (°C),"
    "sensor.fan.1.speed (rpm),sensor.fan.2.speed (rpm),sensor.power.input (W)\n"
)


def csv_at(tmp_path, rows):
    p = tmp_path / "s.csv"
    p.write_text(HEADER + "".join(f"{ms},{g},70.0,3455,3731,100.0\n" for ms, g in rows))
    return p


def epoch(h, m, s=0, day=dt.date(2026, 9, 5)):
    tz = dt.datetime.now().astimezone().tzinfo
    return dt.datetime.combine(day, dt.time(h, m, s), tzinfo=tz).timestamp() * 1000


def test_windows_bucket_by_local_wall_clock(tmp_path):
    """sweep-order is local time, the series is UTC+epoch. Read either
    naively and every window lands hours from its samples."""
    order = tmp_path / "o.txt"
    order.write_text("t-sweep1 09:00:00 09:10:00\n")
    sensors = csv_at(
        tmp_path, [(epoch(9, 5), 80.0), (epoch(9, 7), 82.0), (epoch(11, 0), 50.0)]
    )

    samples = sensor_windows.read_sensors(sensors)
    tz = dt.datetime.now().astimezone().tzinfo
    windows = sensor_windows.read_windows(order, dt.date(2026, 9, 5), tz)
    got = sensor_windows.summarise(samples, windows)
    assert got[0]["samples"] == 2, "the 11:00 sample is outside the window"
    assert got[0]["gpu_c_median"] == 81.0


def test_a_window_crossing_midnight_is_not_empty(tmp_path):
    """finish < start means the next day, not a zero-length window."""
    order = tmp_path / "o.txt"
    order.write_text("late 23:50:00 00:10:00\n")
    tz = dt.datetime.now().astimezone().tzinfo
    windows = sensor_windows.read_windows(order, dt.date(2026, 9, 5), tz)
    _tag, start, finish = windows[0]
    assert finish > start
    assert (finish - start) / 60000 == 20


def test_an_empty_window_is_reported_not_hidden(tmp_path):
    """A join that finds nothing must look like a failure, not a clean run."""
    order = tmp_path / "o.txt"
    order.write_text("t-sweep1 09:00:00 09:10:00\n")
    sensors = csv_at(tmp_path, [(epoch(14, 0), 80.0)])
    assert sensor_windows.main([str(order), str(sensors)]) == 1


def test_a_missing_column_does_not_discard_the_others(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text(HEADER + f"{epoch(9, 5)},,70.0,3455,3731,100.0\n")
    rows = sensor_windows.read_sensors(p)
    assert "gpu_c" not in rows[0]
    assert rows[0]["cpu_c"] == 70.0


def test_malformed_sweep_lines_are_skipped(tmp_path):
    order = tmp_path / "o.txt"
    order.write_text(
        "t-sweep1 09:00:00\nt-sweep2 09:00:00 09:10:00\nnot a line at all\n"
    )
    tz = dt.datetime.now().astimezone().tzinfo
    assert len(sensor_windows.read_windows(order, dt.date(2026, 9, 5), tz)) == 1
