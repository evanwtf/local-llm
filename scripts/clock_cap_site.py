"""Build the data file behind the #1319 clock-cap web page.

Reads each session's committed data directory (the CSVs that
`clock_cap_evidence.py export` and `summary` wrote) and writes one JSON file
for the page at site/clock-cap-1319/: every run tagged with its session, the
15 s sensor series, both outlet plugs, the inlet air and each run's phases.
The page computes its tables and charts from this file in the browser.

    uv run python scripts/clock_cap_site.py \\
        --session "2026-10-30=docs/clock-cap-1319/data" \\
        --session "2026-10-31=docs/clock-cap-1319/curve/data" \\
        --out _site/clock-cap-1319/data.json

The Pages workflow runs this at deploy time, so the JSON is never committed:
the CSVs stay the one source.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import pathlib
import sys
from collections.abc import Sequence
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

#: Columns of runs.csv kept as text; every other non-empty value is a number.
TEXT_COLUMNS = frozenset(
    ("run_id", "round", "arm", "start_utc", "end_utc", "start_new_york", "end_new_york")
)
SERIES_COLUMNS = (
    "head_sm_clock_mhz", "worker_sm_clock_mhz", "head_gpu_temp_c",
    "worker_gpu_temp_c", "head_gpu_power_w", "worker_gpu_power_w",
    "head_fan1_rpm", "head_fan2_rpm",
)  # fmt: skip


def read_csv(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def value(column: str, raw: str) -> str | float | None:
    if raw == "":
        return None
    return raw if column in TEXT_COLUMNS else float(raw)


def parse_session(spec: str) -> tuple[str, pathlib.Path]:
    """'LABEL=DIR' -> (label, dir). The label names the session on the page."""
    label, sep, path = spec.partition("=")
    if not sep or not label or not path:
        raise SystemExit(f"--session wants LABEL=DIR, got {spec!r}")
    return label, pathlib.Path(path)


def session_data(label: str, d: pathlib.Path) -> dict[str, Any]:
    """One session's files as columns: compact JSON, and Plotly reads columns."""
    runs = [
        {k: value(k, v) for k, v in r.items()} | {"session": label}
        for r in read_csv(d / "runs.csv")
    ]
    ts = read_csv(d / "timeseries.csv")
    series: dict[str, list[Any]] = {"time_utc": [r["time_utc"] for r in ts]}
    for col in SERIES_COLUMNS:
        series[col] = [value(col, r[col]) for r in ts]
    outlets: dict[str, dict[str, list[Any]]] = {}
    for r in read_csv(d / "outlets.csv"):
        plug = outlets.setdefault(r["outlet"], {"time_utc": [], "watts": []})
        plug["time_utc"].append(r["time_utc"])
        plug["watts"].append(float(r["watts"]))
    inlet = read_csv(d / "inlet.csv")
    phases = [
        {k: r[k] for k in ("run_id", "arm", "phase", "start_utc", "end_utc")}
        for r in read_csv(d / "phases.csv")
    ]
    return {
        "label": label,
        "runs": runs,
        "series": series,
        "outlets": outlets,
        "inlet": {
            "time_utc": [r["time_utc"] for r in inlet],
            "inlet_f": [float(r["inlet_f"]) for r in inlet],
        },
        "phases": phases,
    }


def build(sessions: Sequence[tuple[str, pathlib.Path]]) -> dict[str, Any]:
    labels = [label for label, _ in sessions]
    if len(set(labels)) != len(labels):
        raise SystemExit(f"session labels repeat: {labels}")
    return {"sessions": [session_data(label, d) for label, d in sessions]}


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--session", action="append", required=True, help="LABEL=DIR")
    p.add_argument("--out", type=pathlib.Path, required=True)
    args = p.parse_args(argv)
    logs.configure()
    data = build([parse_session(s) for s in args.session])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, separators=(",", ":")))
    n = sum(len(s["runs"]) for s in data["sessions"])
    logger.info("wrote %s: %d sessions, %d runs", args.out, len(data["sessions"]), n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
