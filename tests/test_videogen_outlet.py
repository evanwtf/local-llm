"""scripts/videogen/outlet.py: chunked plug reads, gcx errors, coverage, energy (#1406)."""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import outlet

T0 = datetime.datetime(2026, 11, 17, 15, 0, 0, tzinfo=datetime.UTC)
GCX_TRUNCATED = json.dumps(
    {
        "type": "gcx.error",
        "schema_version": "1",
        "error": {
            "summary": "Invalid Influxdb query",
            "exitCode": 1,
            "details": "A query returned too many datapoints and the results "
            "have been truncated at 1001 points to prevent memory issues.",
        },
    }
)


def at(seconds: float) -> datetime.datetime:
    return T0 + datetime.timedelta(seconds=seconds)


def table(points: list[tuple[datetime.datetime, float]]) -> str:
    return json.dumps(
        {
            "columns": ["Time", "Value"],
            "rows": [[t.strftime("%Y-%m-%dT%H:%M:%SZ"), v] for t, v in points],
        }
    )


class Influx:
    """A fake InfluxDB behind gcx: answers a range query from `points`, and
    fails the way Grafana does above 1,000 rows."""

    LIMIT = 1000

    def __init__(self, points: list[tuple[datetime.datetime, float]]) -> None:
        self.points = points
        self.queries = 0

    def __call__(self, query: str) -> str:
        self.queries += 1
        m = re.search(r"range\(start: (\S+), stop: (\S+)\)", query)
        assert m, query
        a, b = (
            datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.UTC
            )
            for s in m.groups()
        )
        rows = [(t, v) for t, v in self.points if a <= t < b]
        if len(rows) > self.LIMIT:
            raise outlet.OutletError(
                "gcx exited 1: " + outlet.error_text(GCX_TRUNCATED)
            )
        return table(rows)


def every_5s(start: float, end: float, watts: float = 100.0) -> list[Any]:
    return [(at(s), watts) for s in range(int(start), int(end) + 1, 5)]


# ------------------------------------------------------------- chunking ----


def test_three_hours_are_read_in_chunks_under_the_point_cap() -> None:
    influx = Influx(every_5s(-300, 3 * 3600 + 300))
    got = outlet.energy(
        "dgx_current_consumption",
        at(0),
        at(3 * 3600),
        flux=influx,
        sleep=lambda s: None,
    )
    assert got["complete"] is True and got["error"] is None
    assert got["energy_wh"] == pytest.approx(300.0)  # 100 W for 3 h
    assert got["readings"] == 3 * 3600 // 5 + 1
    assert influx.queries == 4  # 3 h plus the pads, in 60-min chunks


def test_one_query_over_the_cap_would_have_failed() -> None:
    influx = Influx(every_5s(0, 3 * 3600))
    query = "range(start: 2026-11-17T15:00:00Z, stop: 2026-11-17T18:00:00Z)"
    with pytest.raises(outlet.OutletError, match="truncated at 1001 points"):
        influx(query)


def test_chunks_are_merged_and_deduplicated() -> None:
    points = every_5s(-120, 7200)

    def overlapping(query: str) -> str:
        return table(points)  # every chunk answers with every point

    got = outlet.read_points("e", at(-120), at(7200), overlapping)
    assert got == sorted(points)


# ------------------------------------------------------------ gcx errors ----


def test_a_gcx_error_answer_is_an_error_not_an_empty_table() -> None:
    with pytest.raises(outlet.OutletError, match="truncated at 1001 points"):
        outlet.parse_table(GCX_TRUNCATED)


def test_gcx_flux_raises_on_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args[0], 1, stdout=GCX_TRUNCATED, stderr="")

    monkeypatch.setattr(outlet.subprocess, "run", fake_run)
    with pytest.raises(outlet.OutletError, match="gcx exited 1: Invalid Influxdb"):
        outlet.gcx_flux("from(bucket: x)")


def test_energy_reports_a_gcx_failure_as_incomplete() -> None:
    def broken(query: str) -> str:
        raise outlet.OutletError("gcx exited 1: Unauthorized")

    got = outlet.energy("e", at(0), at(360), flux=broken, sleep=lambda s: None, tries=2)
    assert got["complete"] is False
    assert got["energy_wh"] is None
    assert got["error"] == "gcx exited 1: Unauthorized"


# -------------------------------------------------------------- coverage ----


def test_readings_every_5s_cover_the_window() -> None:
    assert outlet.coverage(every_5s(-5, 365), at(0), at(360)) == (True, 5.0)


def test_a_gap_over_30s_is_incomplete() -> None:
    points = [*every_5s(-5, 100), *every_5s(140, 365)]
    complete, gap = outlet.coverage(points, at(0), at(360))
    assert (complete, gap) == (False, 40.0)


def test_a_gap_of_exactly_30s_is_complete() -> None:
    points = [*every_5s(-5, 100), *every_5s(130, 365)]
    assert outlet.coverage(points, at(0), at(360)) == (True, 30.0)


def test_no_reading_after_the_end_is_incomplete() -> None:
    complete, _ = outlet.coverage(every_5s(-5, 355), at(0), at(360))
    assert complete is False


def test_no_reading_before_the_start_is_incomplete() -> None:
    complete, _ = outlet.coverage(every_5s(5, 365), at(0), at(360))
    assert complete is False


# --------------------------------------------------------------- energy ----


def test_constant_power_for_an_hour_is_its_watts_in_wh() -> None:
    points = [(at(s), 100.0) for s in range(0, 3601, 5)]
    assert outlet.integrate(points, at(0), at(3600)) == pytest.approx(100.0)


def test_a_linear_ramp_integrates_to_its_mean() -> None:
    points = [(at(s), s / 3600 * 200.0) for s in range(0, 3601, 10)]
    assert outlet.integrate(points, at(0), at(3600)) == pytest.approx(100.0)


def test_the_reading_in_force_at_the_start_covers_the_first_seconds() -> None:
    # 200 W from before the start until a 100 W reading at 1800 s. Without the
    # held edge, the first 1800 s would read as 100 W (the first inside value).
    points = [(at(-30), 200.0), (at(1800), 100.0), (at(3600), 100.0)]
    wh = outlet.integrate(points, at(0), at(3600))
    assert wh == pytest.approx((1800 * 150 + 1800 * 100) / 3600)


def test_energy_waits_for_a_reading_after_the_end() -> None:
    late = every_5s(0, 355)
    full = every_5s(-5, 365)
    answers = iter([table(late), table(full)])  # one query per try

    def flux(query: str) -> str:
        return next(answers)

    slept: list[float] = []
    got = outlet.energy(
        "e", at(0), at(360), flux=flux, sleep=slept.append, settle_s=7.0
    )
    assert slept == [7.0, 7.0]
    assert got["complete"] is True
    assert got["energy_wh"] == pytest.approx(10.0)
    assert got["peak_w"] == 100.0


def test_plug_entities() -> None:
    assert outlet.entity("head") == "dgx_current_consumption"
    assert outlet.entity("worker") == "dgx_2_current_consumption"
