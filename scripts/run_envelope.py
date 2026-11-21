"""The thermal and power envelope of a two-Spark run window, one line per node.

Every two-Spark run posts this envelope over its whole window (#1456): per
node, the GPU temperature maximum, the GPU power average and peak, and the
MemAvailable minimum. It reads Prometheus through Grafana with gcx
(read-only), four instant queries evaluated at the window end:

    max_over_time(DCGM_FI_DEV_GPU_TEMP[<w>s] @ <end>)
    avg_over_time(DCGM_FI_DEV_POWER_USAGE[<w>s] @ <end>)
    max_over_time(DCGM_FI_DEV_POWER_USAGE[<w>s] @ <end>)
    min_over_time(node_memory_MemAvailable_bytes[<w>s] @ <end>)

    uv run python scripts/run_envelope.py \\
        --since 2026-11-21T22:59:48Z --until 2026-11-21T23:11:13Z [--json]

The answers also carry series from other hosts (a laptop GPU at 590 W, other
node exporters). The script keeps only the series whose `instance` label is
`<head>:9400` / `<worker>:9400` (DCGM) or `<head>:9100` / `<worker>:9100`
(node exporter). The DCGM `Hostname` label is the dcgm-exporter container id,
which changes when the container is recreated, so the script never reads it.
The node names come from `dgx_hosts` (never from the repo, #1153). The gcx
call is the only untested part; tests/test_run_envelope.py covers the rest.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import pathlib
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dgx_hosts
import dgx_metrics

import logs

logger = logging.getLogger(__name__)

DCGM_PORT = 9400
NODE_EXPORTER_PORT = 9100
GIB = 2**30

#: (name, PromQL template). `{w}` is the window in seconds, `{end}` Unix s.
QUERIES: tuple[tuple[str, str], ...] = (
    ("gpu_temp_max", "max_over_time(DCGM_FI_DEV_GPU_TEMP[{w}s] @ {end})"),
    ("power_avg", "avg_over_time(DCGM_FI_DEV_POWER_USAGE[{w}s] @ {end})"),
    ("power_max", "max_over_time(DCGM_FI_DEV_POWER_USAGE[{w}s] @ {end})"),
    ("mem_avail_min", "min_over_time(node_memory_MemAvailable_bytes[{w}s] @ {end})"),
)
#: The exporter port that each query's `instance` label carries.
PORTS = {
    "gpu_temp_max": DCGM_PORT,
    "power_avg": DCGM_PORT,
    "power_max": DCGM_PORT,
    "mem_avail_min": NODE_EXPORTER_PORT,
}


@dataclass(frozen=True)
class Node:
    """The envelope of one node. None: the series had no sample."""

    role: str
    gpu_temp_max_c: float | None
    power_avg_w: float | None
    power_max_w: float | None
    mem_avail_min_gib: float | None


def _instant(text: str) -> dt.datetime:
    when = dt.datetime.fromisoformat(text)
    if when.tzinfo is None:
        raise ValueError(f"{text!r} has no offset; add Z or +HH:MM")
    return when


def window(since: str, until: str) -> tuple[int, int]:
    """(window length in s, window end in Unix s) from two ISO 8601 instants."""
    start, end = _instant(since), _instant(until)
    seconds = round((end - start).total_seconds())
    if seconds <= 0:
        raise ValueError(f"--until {until} must be after --since {since}")
    return seconds, round(end.timestamp())


def promql(w: int, end: int) -> list[tuple[str, str]]:
    """(name, PromQL) for each query, over `w` seconds ending at `end`."""
    return [(name, t.format(w=w, end=end)) for name, t in QUERIES]


def by_instance(gcx_json: str) -> dict[str, float]:
    """The value of each series of a gcx instant query, by `instance` label."""
    try:
        result = json.loads(gcx_json)["data"]["result"]
    except (ValueError, KeyError, TypeError):
        return {}
    out: dict[str, float] = {}
    for series in result:
        instance = series.get("metric", {}).get("instance")
        try:
            value = float(series["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if instance:
            out[instance] = value
    return out


def for_role(
    values: Mapping[str, float], hosts: Mapping[str, str], port: int
) -> dict[str, float | None]:
    """The value of each node, matched on `<host>:<port>`. Others are dropped."""
    return {role: values.get(f"{hosts[role]}:{port}") for role in dgx_hosts.ROLES}


def envelope(
    since: str,
    until: str,
    prom: Callable[[str], str] | None = None,
    hosts: Mapping[str, str] | None = None,
) -> list[Node]:
    """The envelope of each node over the window, head first."""
    w, end = window(since, until)
    if prom is None:
        prom = dgx_metrics.query
    if hosts is None:
        hosts = {role: dgx_hosts.lookup(role) for role in dgx_hosts.ROLES}
    got: dict[str, dict[str, float | None]] = {}
    for name, query in promql(w, end):
        got[name] = for_role(by_instance(prom(query)), hosts, PORTS[name])
        logger.debug("%s: %s", name, got[name])
    nodes: list[Node] = []
    for role in dgx_hosts.ROLES:
        mem = got["mem_avail_min"][role]
        nodes.append(
            Node(
                role=role,
                gpu_temp_max_c=got["gpu_temp_max"][role],
                power_avg_w=got["power_avg"][role],
                power_max_w=got["power_max"][role],
                mem_avail_min_gib=None if mem is None else mem / GIB,
            )
        )
    return nodes


def _n(value: float | None, fmt: str, unit: str) -> str:
    return "n/a" if value is None else f"{value:{fmt}} {unit}"


def lines(nodes: Sequence[Node]) -> list[str]:
    """One readout line per node."""
    return [
        f"{n.role}: GPU temp max {_n(n.gpu_temp_max_c, '.0f', 'C')}, "
        f"power avg {_n(n.power_avg_w, '.1f', 'W')}, "
        f"peak {_n(n.power_max_w, '.1f', 'W')}, "
        f"MemAvailable min {_n(n.mem_avail_min_gib, '.1f', 'GiB')}"
        for n in nodes
    ]


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def as_json(nodes: Sequence[Node], since: str, until: str) -> str:
    """The envelope as JSON, rounded as `lines` rounds it."""
    w, _end = window(since, until)
    body: dict[str, object] = {
        "since": since,
        "until": until,
        "window_s": w,
        "nodes": {
            n.role: {
                k: _r(v, 0 if k == "gpu_temp_max_c" else 1)
                for k, v in dataclasses.asdict(n).items()
                if k != "role"
            }
            for n in nodes
        },
    }
    return json.dumps(body)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--since", required=True, help="window start, ISO 8601")
    p.add_argument("--until", required=True, help="window end, ISO 8601")
    p.add_argument("--json", action="store_true", help="print JSON, not lines")
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    try:
        window(args.since, args.until)
    except ValueError as err:
        p.error(str(err))
    try:
        nodes = envelope(args.since, args.until)
    except dgx_hosts.HostUnknown as err:
        logger.error("%s", err)
        return 2
    if all(n.power_max_w is None for n in nodes):
        logger.error("no GPU power data for either node: is gcx authenticated?")
        return 1
    if args.json:
        logger.info("%s", as_json(nodes, args.since, args.until))
    else:
        for line in lines(nodes):
            logger.info("%s", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
