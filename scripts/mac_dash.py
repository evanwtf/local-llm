"""M5 Max GPU / thermal / power snapshot from the local exporter; history via gcx.

The `mac-overview` Grafana dashboard draws `macos_*` (macmonitor_exporter) and
`node_*` (node_exporter) series for the M5 Max. This reads the same series from
the command line so a heartbeat or an issue comment can carry the numbers
without opening the dashboard, and so a benchmark window can be stamped with its
own thermal envelope (peak temp / power / fan, GPU-util floor).

Why gcx and not a direct Prometheus client: the reachable, authenticated door to
these series is Grafana, and `gcx metrics query` is the repo's settled client
for it (see `scripts/gpu_utilization.py`, `scripts/fan_ab_collate.py`). A second,
direct-HTTP path would fork that convention for no gain. The Python here is the
parse-and-format layer -- tested below against captured gcx JSON -- and gcx is
the transport.

Two modes:

* **snapshot** (default) -- the current values, as a compact heartbeat line and
  an issue-ready block. Read from the exporter on this machine
  (`http://127.0.0.1:9650/metrics`), not through Grafana: a current value needs
  no history, and the gcx path failed with "Network error" for every heartbeat
  on 2026-09-25 while the exporter itself answered.
* **envelope** (`--window 210m`) -- the peaks over a past window: max GPU and CPU
  temperature, max and p90 input power, max fan, and the GPU-utilization floor.
  A single instantaneous sample misses a brief throttle-zone excursion; the
  windowed max does not (a spot heartbeat read 73-78 C through a run whose true
  GPU peak was 98.5 C).

The instance label carries the `:9650` port, so a bare IP matches nothing. The
repo is PUBLIC, so the instance comes from `MAC_DASH_INSTANCE` in the
environment, never from this file.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

#: The M5 Max under `job="macmonitor_exporter"`, e.g. `<lan-ip>:9650`. The port
#: is part of the label; several hosts share these series, so the filter must be
#: exact. Envelope mode only.
INSTANCE_ENV = "MAC_DASH_INSTANCE"
INSTANCE = os.environ.get(INSTANCE_ENV, "")

#: The exporter on this machine. Snapshot mode reads it directly.
EXPORTER_URL = os.environ.get("MAC_DASH_EXPORTER", "http://127.0.0.1:9650/metrics")

#: The Prometheus datasource behind Grafana (shared with the DGX; see
#: `scripts/gpu_utilization.py` and `docs/dgx-spark-runbook.md`).
DATASOURCE = "uMatQbvMk"

#: gcx reads its token from the environment; a non-interactive shell never
#: sources the `.bashrc` that exports it, so it is read explicitly here.
TOKEN_PATH = pathlib.Path.home() / ".config/gcx/token"


def _gcx_env() -> dict[str, str]:
    env = dict(os.environ)
    try:
        if TOKEN_PATH.exists():
            env["GRAFANA_TOKEN"] = TOKEN_PATH.read_text().strip()
    except OSError:
        pass
    return env


def query(promql: str) -> str:
    """Raw gcx JSON for one instant query, or `""` if the call fails.

    The subprocess boundary -- the only part that is not unit-tested. Parsing
    lives in `scalar` and `labeled`, which the tests exercise against captured
    output.
    """
    try:
        out = subprocess.run(
            ["gcx", "metrics", "query", "-d", DATASOURCE, promql, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=_gcx_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout


def scalar(gcx_json: str) -> float | None:
    """The single value out of a gcx instant-query result, or None if empty."""
    try:
        result = json.loads(gcx_json).get("data", {}).get("result", [])
    except (ValueError, AttributeError):
        return None
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def labeled(gcx_json: str, key: str) -> dict[str, float]:
    """Map one label's value to the sample, for a metric with several series.

    e.g. `labeled(..., "sensor")` -> `{"gpu": 74.7, "cpu": 64.7, ...}`.
    """
    out: dict[str, float] = {}
    try:
        result = json.loads(gcx_json).get("data", {}).get("result", [])
    except (ValueError, AttributeError):
        return out
    for row in result:
        try:
            out[row["metric"][key]] = float(row["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


def _sel(metric: str) -> str:
    return f'{metric}{{instance="{INSTANCE}"}}'


_SAMPLE = re.compile(r"^([A-Za-z_:][\w:]*)(?:\{([^}]*)\})?\s+(\S+)")
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')

Series = dict[tuple[tuple[str, str], ...], float]


def parse_exposition(text: str) -> dict[str, Series]:
    """Prometheus text format -> {metric: {sorted label pairs: value}}.

    Comments and lines that do not parse are skipped, never read as 0.
    """
    out: dict[str, Series] = {}
    for line in text.splitlines():
        m = _SAMPLE.match(line)
        if not m:
            continue
        try:
            value = float(m.group(3))
        except ValueError:
            continue
        labels = tuple(sorted(_LABEL.findall(m.group(2) or "")))
        out.setdefault(m.group(1), {})[labels] = value
    return out


def snapshot_from_exposition(text: str) -> dict[str, object]:
    """The same shape as `snapshot_via_gcx`, from the exporter's own text."""
    got = parse_exposition(text)

    def one(metric: str) -> float | None:
        return got.get(metric, {}).get(())

    def by(metric: str, key: str) -> dict[str, float]:
        return {
            dict(labels)[key]: v
            for labels, v in got.get(metric, {}).items()
            if key in dict(labels)
        }

    return {
        "gpu_util": one("macos_gpu_utilization_ratio"),
        "vram_bytes": one("macos_gpu_vram_used_bytes"),
        "temp": {
            k: v
            for k, v in by("macos_smc_temperature_celsius", "sensor").items()
            if k in {"gpu", "cpu", "enclosure"}
        },
        "fan": by("macos_smc_fan_rpm", "fan"),
        "power": by("macos_smc_power_watts", "rail"),
    }


def fetch_exposition(url: str = EXPORTER_URL) -> str:
    """The exporter's text, or `""` if it does not answer."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return str(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return ""


def snapshot() -> dict[str, object]:
    """The current values, read from the exporter on this machine."""
    return snapshot_from_exposition(fetch_exposition())


def snapshot_via_gcx() -> dict[str, object]:
    """The current values through Grafana. Needs `MAC_DASH_INSTANCE`."""
    return {
        "gpu_util": scalar(query(_sel("macos_gpu_utilization_ratio"))),
        "vram_bytes": scalar(query(_sel("macos_gpu_vram_used_bytes"))),
        "temp": labeled(query(_sel("macos_smc_temperature_celsius")), "sensor"),
        "fan": labeled(query(_sel("macos_smc_fan_rpm")), "fan"),
        "power": labeled(query(_sel("macos_smc_power_watts")), "rail"),
    }


def envelope(window: str) -> dict[str, float | None]:
    """Peaks over a past `window` (e.g. "210m"): the run's thermal envelope."""

    def sel(metric: str, sensor: str = "") -> str:
        label = f'instance="{INSTANCE}"' + (f',sensor="{sensor}"' if sensor else "")
        return f"{metric}{{{label}}}"

    return {
        "gpu_temp_max": scalar(
            query(
                f"max_over_time({sel('macos_smc_temperature_celsius', 'gpu')}[{window}])"
            )
        ),
        "cpu_temp_max": scalar(
            query(
                f"max_over_time({sel('macos_smc_temperature_celsius', 'cpu')}[{window}])"
            )
        ),
        "power_max": scalar(
            query(f"max_over_time({sel('macos_smc_power_watts')}[{window}])")
        ),
        "power_p90": scalar(
            query(f"quantile_over_time(0.9, {sel('macos_smc_power_watts')}[{window}])")
        ),
        "gpu_util_min": scalar(
            query(f"min_over_time({sel('macos_gpu_utilization_ratio')}[{window}])")
        ),
    }


def _fmt(value: float | None, suffix: str = "", scale: float = 1.0) -> str:
    return "n/a" if value is None else f"{value * scale:.1f}{suffix}"


def render_snapshot(snap: dict[str, object]) -> list[str]:
    """A compact heartbeat line plus an issue-ready block."""
    gpu = snap["gpu_util"]
    vram = snap["vram_bytes"]
    temp: dict[str, float] = snap["temp"]  # type: ignore[assignment]
    fan: dict[str, float] = snap["fan"]  # type: ignore[assignment]
    power: dict[str, float] = snap["power"]  # type: ignore[assignment]
    gpu_pct = "n/a" if gpu is None else f"{gpu * 100:.0f}%"
    vram_gib = "n/a" if vram is None else f"{vram / 1024**3:.1f} GiB"
    return [
        (
            f"GPU {gpu_pct} util, VRAM {vram_gib} | "
            f"gpu {_fmt(temp.get('gpu'))} C, cpu {_fmt(temp.get('cpu'))} C | "
            f"fans {_fmt(fan.get('1'))}/{_fmt(fan.get('2'))} rpm | "
            f"input {_fmt(power.get('input'))} W, soc {_fmt(power.get('soc'))} W"
        ),
        f"- GPU utilization: {gpu_pct}   VRAM in use: {vram_gib}",
        (
            f"- SMC temps: gpu {_fmt(temp.get('gpu'))} C, "
            f"cpu {_fmt(temp.get('cpu'))} C, "
            f"enclosure {_fmt(temp.get('enclosure'))} C"
        ),
        f"- Fans: {_fmt(fan.get('1'))} / {_fmt(fan.get('2'))} rpm",
        f"- Power: input {_fmt(power.get('input'))} W, soc {_fmt(power.get('soc'))} W",
    ]


def render_envelope(env: dict[str, float | None], window: str) -> list[str]:
    util = env["gpu_util_min"]
    floor = "n/a" if util is None else f"{util * 100:.0f}%"
    return [
        f"envelope over the last {window}:",
        (
            f"- GPU temp peak {_fmt(env['gpu_temp_max'])} C, "
            f"CPU temp peak {_fmt(env['cpu_temp_max'])} C"
        ),
        f"- Power peak {_fmt(env['power_max'])} W, p90 {_fmt(env['power_p90'])} W",
        f"- GPU-util floor: {floor}",
    ]


def main(argv: list[str] | None = None) -> int:
    logs.configure(fmt=logs.PLAIN)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--window",
        help="envelope mode: peaks over this past window, e.g. 210m or 3h",
    )
    args = p.parse_args(argv)
    if args.window:
        if not INSTANCE:
            logger.error(
                "envelope mode needs %s=<ip>:9650 (the Prometheus instance label)",
                INSTANCE_ENV,
            )
            return 2
        lines = render_envelope(envelope(args.window), args.window)
    else:
        lines = render_snapshot(snapshot())
    for line in lines:
        logger.info("%s", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
