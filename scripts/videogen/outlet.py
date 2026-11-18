"""The node's outlet energy over a request window, from Home Assistant's InfluxDB (#1406).

Each Spark sits on a smart plug that Home Assistant records into InfluxDB
about every 5 s. Grafana's InfluxDB datasource refuses an answer over 1,000
points: gcx exits 1 and prints a `gcx.error` ("truncated at 1001 points").
At 5 s a reading, 1,000 points is about 83 minutes, and one 50-step H3 clip
can run longer. So the window is read in chunks of at most
`clock_cap_evidence.FLUX_CHUNK_S` (60 min), merged and deduplicated. A gcx
error is an error (`OutletError`), never an empty answer: an empty answer
integrates to nothing and reads like a missing plug.

The reading is complete only if a reading at or before the start and one at
or after the end exist, and no gap between readings in the window is over
`MAX_GAP_S`. An incomplete reading keeps its partial Wh under `outlet`, but
the run's top-level `energy_wh` is None, so it cannot pass as a measurement.
"""

from __future__ import annotations

import datetime
import itertools
import json
import logging
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import clock_cap_evidence
import dgx_metrics

logger = logging.getLogger(__name__)

#: A gap between plug readings longer than this makes the reading incomplete.
#: The plug reports about every 5 s; 30 s is six missed readings.
MAX_GAP_S = 30.0
#: How long to wait for the plug's readings to reach InfluxDB after the
#: request ends, per try, and how many tries.
SETTLE_S = 15.0
TRIES = 4
#: Readings this far outside the window are read too, so the reading in
#: force at the start, and the first one after the end, are known.
PAD_BEFORE = datetime.timedelta(seconds=120)
PAD_AFTER = datetime.timedelta(seconds=60)
#: Seconds per query. Grafana caps an Influx answer at 1,000 points.
CHUNK_S = clock_cap_evidence.FLUX_CHUNK_S

Point = tuple[datetime.datetime, float]
#: One Flux query -> gcx's JSON table. Raises OutletError on a gcx error.
Flux = Callable[[str], str]


class OutletError(Exception):
    """gcx failed, or answered with something that is not a table."""


def entity(role: str) -> str:
    """The Home Assistant entity of the node's smart plug."""
    return {
        "head": dgx_metrics.OUTLET_ENTITY,
        "worker": dgx_metrics.WORKER_OUTLET_ENTITY,
    }[role]


def gcx_flux(query: str) -> str:
    """One Flux query through gcx. Raises OutletError when gcx fails."""
    env = dict(os.environ)
    try:
        if dgx_metrics.TOKEN_PATH.exists():
            env["GRAFANA_TOKEN"] = dgx_metrics.TOKEN_PATH.read_text().strip()
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["gcx", "datasources", "query", dgx_metrics.INFLUX_DATASOURCE, query]
            + ["-o", "json"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=300,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise OutletError(f"gcx did not run: {err}") from err
    if out.returncode != 0:
        raise OutletError(f"gcx exited {out.returncode}: {error_text(out.stdout)}")
    return out.stdout


def error_text(stdout: str) -> str:
    """The summary and details of a `gcx.error` answer, or the raw start."""
    try:
        err = json.loads(stdout)["error"]
        return f"{err.get('summary', '')}: {err.get('details', '')}"[:500]
    except (ValueError, KeyError, TypeError):
        return stdout.strip()[:500]


def parse_table(text: str) -> list[Point]:
    """(UTC time, watts) from a gcx Influx table. Raises OutletError when
    the answer is not a table (a `gcx.error` included)."""
    try:
        table = json.loads(text)
    except ValueError as err:
        raise OutletError(f"gcx answer is not JSON: {text[:200]!r}") from err
    if not isinstance(table, dict) or "columns" not in table:
        raise OutletError(f"gcx answer is not a table: {error_text(text)}")
    return clock_cap_evidence.flux_points(text)


def read_points(
    plug: str, start: datetime.datetime, end: datetime.datetime, flux: Flux
) -> list[Point]:
    """Every reading from start to end, in chunks of at most CHUNK_S,
    merged, deduplicated by time, and sorted."""
    t0 = int(start.timestamp())
    t1 = int(end.timestamp()) + 1
    merged: dict[datetime.datetime, float] = {}
    for a, b in clock_cap_evidence.chunks(t0, t1, CHUNK_S):
        query = clock_cap_evidence.raw_flux(
            plug,
            "W",
            clock_cap_evidence.utc_text(
                datetime.datetime.fromtimestamp(a, datetime.UTC)
            ),
            clock_cap_evidence.utc_text(
                datetime.datetime.fromtimestamp(b, datetime.UTC)
            ),
        )
        for t, v in parse_table(flux(query)):
            merged[t] = v
    return sorted(merged.items())


def coverage(
    points: Sequence[Point], start: datetime.datetime, end: datetime.datetime
) -> tuple[bool, float | None]:
    """(complete, max gap in seconds) of the readings over [start, end].

    The readings that matter are the last one at or before the start, every
    one inside, and the first one at or after the end. Complete means both
    edges are covered and no gap is over MAX_GAP_S.
    """
    before = [t for t, _ in points if t <= start]
    inside = [t for t, _ in points if start < t < end]
    after = [t for t, _ in points if t >= end]
    times = ([before[-1]] if before else [start]) + inside
    times.append(after[0] if after else end)
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times)]
    max_gap = max(gaps) if gaps else None
    complete = bool(before and after and max_gap is not None and max_gap <= MAX_GAP_S)
    return complete, max_gap


def integrate(
    points: Sequence[Point], start: datetime.datetime, end: datetime.datetime
) -> float | None:
    """Wh from start to end by the trapezoid rule. The reading in force at
    the start (the plug records on its own clock) is added at the start."""
    pts = list(points)
    edge = clock_cap_evidence.held_value(pts, start)
    if edge is not None:
        pts.append((start, edge))
    pts.sort(key=lambda p: p[0])
    return clock_cap_evidence.energy_wh(pts, start, end)


def energy(
    plug: str,
    start: datetime.datetime,
    end: datetime.datetime,
    *,
    flux: Flux,
    sleep: Callable[[float], None] = time.sleep,
    settle_s: float = SETTLE_S,
    tries: int = TRIES,
) -> dict[str, Any]:
    """Read and integrate the plug over [start, end]; never raises.

    Waits for a reading at or after `end`, so InfluxDB lag does not cut off
    the last seconds. `complete` is False, with the reason in `error` when
    gcx failed, if the readings do not cover the window.
    """
    points: list[Point] = []
    error: str | None = None
    for attempt in range(tries):
        sleep(settle_s)
        try:
            points = read_points(plug, start - PAD_BEFORE, end + PAD_AFTER, flux)
        except OutletError as err:
            error = str(err)
            logger.warning("outlet: %s (try %d)", err, attempt + 1)
            continue
        error = None
        if any(t >= end for t, _ in points):
            break
        logger.info("outlet: no reading after the end yet (try %d)", attempt + 1)
    complete, max_gap = coverage(points, start, end)
    inside = [v for t, v in points if start <= t <= end]
    wh = integrate(points, start, end)
    return {
        "entity": plug,
        "energy_wh": None if wh is None else round(wh, 3),
        "complete": complete and error is None,
        "max_gap_s": None if max_gap is None else round(max_gap, 1),
        "readings": len(inside),
        "mean_w": round(sum(inside) / len(inside), 1) if inside else None,
        "peak_w": round(max(inside), 1) if inside else None,
        "error": error,
    }
