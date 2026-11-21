"""The thermal and power envelope of a two-Spark run window, one line per node.

Every two-Spark run posts this envelope over its whole window (#1456): per
node, the GPU temperature maximum, the GPU power average and peak, and the
MemAvailable minimum. It reads Prometheus through Grafana with gcx
(read-only): seven instant queries, each evaluated at the window end.

    uv run python scripts/run_envelope.py \\
        --since 2026-11-21T22:59:48Z --until 2026-11-21T23:11:13Z [--json]

**Which node.** The answers also carry series from other hosts (a laptop GPU
at 590 W, other node exporters). The script keeps only the series whose
`instance` label is `<head>:9400` / `<worker>:9400` (DCGM) or `<head>:9100` /
`<worker>:9100` (node exporter). The DCGM `Hostname` label is the
dcgm-exporter container id, which changes when the container is recreated, so
the script never reads it. The node names come from `dgx_hosts` (never from
the repo, #1153).

**One value per node.** Each query aggregates `by (instance)`, so two series
for one node (a container recreated mid-window gives a new `Hostname`) still
give one value, and the right one:

* temperature maximum and power peak: `max by` of `max_over_time`;
* MemAvailable minimum: `min by` of `min_over_time`;
* power average: `sum by` of `sum_over_time` divided by `sum by` of
  `count_over_time`. An `avg by` of two partial averages would weight a short
  series the same as a long one;
* sample counts: `sum by` of `count_over_time`.

**What "peak" means.** DCGM is scraped every 5 s, but dcgm-exporter refreshes
its power reading only about every 30 s: 685 s on 2026-11-21 held 137
samples and 22 to 23 changes. The peak is the highest of those ~23 distinct
readings, not an instantaneous maximum.

**When it refuses.** The exit status is not 0 when the envelope is not whole:

* exit 2: a bad window (no offset, reversed, or `--until` more than
  `FUTURE_SKEW_S` past now), or a node name is not configured;
* exit 1: a node lacks a value (a down exporter, a failed gcx call, a window
  out of retention, a host name that matches no series), or a node's sample
  coverage is under `FAIL_COVERAGE`. Coverage is the sample count over the
  window divided by the scrape interval. Under `WARN_COVERAGE` it warns and
  still exits 0. The lines print first in every case, so the partial result
  stays visible.

**No `--run <name>`.** The issue asked for a run name resolved from the ledger.
Ledger rows carry `started` and `finished` but no run id, so a "run" has no
key: grouping by backend can span days of separate batches. Add it only after
the ledger gets a run id.

The gcx call is the only untested part; tests/test_run_envelope.py covers the
rest.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import sys
import time
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
#: Scrape interval of each exporter, seconds. Measured 2026-11-21: 137 DCGM and
#: 69 node-exporter samples per node in a 685 s window.
SCRAPE_S = {DCGM_PORT: 5, NODE_EXPORTER_PORT: 10}
#: Coverage under this warns: some samples are missing, the envelope may miss
#: the true extreme.
WARN_COVERAGE = 0.8
#: Coverage under this fails: most of the window has no data, so the numbers
#: describe some other, shorter window.
FAIL_COVERAGE = 0.5
#: How far past now `--until` may lie: clock skew between hosts, no more.
FUTURE_SKEW_S = 60

_TEMP = "DCGM_FI_DEV_GPU_TEMP[{w}s] @ {end}"
_POWER = "DCGM_FI_DEV_POWER_USAGE[{w}s] @ {end}"
_MEM = "node_memory_MemAvailable_bytes[{w}s] @ {end}"

#: (name, port of its `instance` label, PromQL template). `{w}` is the window
#: in seconds, `{end}` Unix seconds. See the module docstring for why each
#: aggregation.
QUERIES: tuple[tuple[str, int, str], ...] = (
    ("gpu_temp_max", DCGM_PORT, f"max by (instance) (max_over_time({_TEMP}))"),
    ("gpu_temp_count", DCGM_PORT, f"sum by (instance) (count_over_time({_TEMP}))"),
    ("power_sum", DCGM_PORT, f"sum by (instance) (sum_over_time({_POWER}))"),
    ("power_count", DCGM_PORT, f"sum by (instance) (count_over_time({_POWER}))"),
    ("power_max", DCGM_PORT, f"max by (instance) (max_over_time({_POWER}))"),
    ("mem_min", NODE_EXPORTER_PORT, f"min by (instance) (min_over_time({_MEM}))"),
    ("mem_count", NODE_EXPORTER_PORT, f"sum by (instance) (count_over_time({_MEM}))"),
)  # fmt: skip
PORT = {name: port for name, port, _t in QUERIES}

#: The queries each output field needs.
FIELD_QUERIES: dict[str, tuple[str, ...]] = {
    "gpu_temp_max_c": ("gpu_temp_max",),
    "power_avg_w": ("power_sum", "power_count"),
    "power_max_w": ("power_max",),
    "mem_avail_min_gib": ("mem_min",),
}


@dataclass(frozen=True)
class Answer:
    """One parsed gcx answer.

    ok: gcx answered with status "success". series: how many came back.
    values: each series' value by `instance` label.
    """

    ok: bool
    series: int
    values: dict[str, float]


@dataclass(frozen=True)
class Node:
    """The envelope of one node. None: the series had no sample.

    Coverage is the share of expected scrapes present (1.0 is all of them).
    """

    role: str
    gpu_temp_max_c: float | None
    power_avg_w: float | None
    power_max_w: float | None
    mem_avail_min_gib: float | None
    dcgm_coverage: float | None
    node_exporter_coverage: float | None


@dataclass(frozen=True)
class Envelope:
    """The nodes, head first, and what is wrong with them."""

    nodes: list[Node]
    errors: list[str]
    warnings: list[str]


def _instant(text: str) -> dt.datetime:
    when = dt.datetime.fromisoformat(text)
    if when.tzinfo is None:
        raise ValueError(f"{text!r} has no offset; add Z or +HH:MM")
    return when


def window(since: str, until: str, now: float | None = None) -> tuple[int, int]:
    """(window length in s, window end in Unix s) from two ISO 8601 instants.

    With `now`, refuse an end more than FUTURE_SKEW_S past it: Prometheus
    has no data there, and the window would read as longer than it is.
    """
    start, end = _instant(since), _instant(until)
    seconds = round((end - start).total_seconds())
    if seconds <= 0:
        raise ValueError(f"--until {until} must be after --since {since}")
    if now is not None and end.timestamp() > now + FUTURE_SKEW_S:
        raise ValueError(f"--until {until} is in the future")
    return seconds, round(end.timestamp())


def promql(w: int, end: int) -> list[tuple[str, str]]:
    """(name, PromQL) for each query, over `w` seconds ending at `end`."""
    return [(name, t.format(w=w, end=end)) for name, _port, t in QUERIES]


def parse(gcx_json: str) -> Answer:
    """A gcx instant-query answer. A failed call gives ok=False.

    Two series with one `instance` should not happen (every query aggregates
    `by (instance)`); if they do, the larger value stays and a warning says so.
    """
    try:
        body = json.loads(gcx_json)
        result = body["data"]["result"]
        ok = body.get("status") == "success"
    except (ValueError, KeyError, TypeError, AttributeError):
        return Answer(ok=False, series=0, values={})
    values: dict[str, float] = {}
    for series in result:
        instance = series.get("metric", {}).get("instance")
        try:
            value = float(series["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if not instance:
            continue
        if instance in values:
            logger.warning("two series for one instance; keeping the larger")
            value = max(value, values[instance])
        values[instance] = value
    return Answer(ok=ok, series=len(result), values=values)


def for_role(
    values: Mapping[str, float], hosts: Mapping[str, str], port: int
) -> dict[str, float | None]:
    """The value of each node, matched on `<host>:<port>`. Others are dropped."""
    return {role: values.get(f"{hosts[role]}:{port}") for role in dgx_hosts.ROLES}


def diagnose(answer: Answer, role: str, port: int) -> str:
    """Why a node has no value in this answer. Never names a host: public."""
    if not answer.ok:
        return "gcx returned nothing or an error status"
    if answer.series == 0:
        return "no series in the window (out of retention, or no exporter?)"
    return (
        f"{answer.series} series came back, none at the configured {role} host "
        f"on port {port} (check the host name in dgx_hosts)"
    )


def _div(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else a / b


def coverage(count: float | None, w: int, port: int) -> float | None:
    """The share of the expected scrapes over `w` seconds that are present."""
    return None if count is None else count * SCRAPE_S[port] / w


def envelope(
    since: str,
    until: str,
    prom: Callable[[str], str] | None = None,
    hosts: Mapping[str, str] | None = None,
) -> Envelope:
    """The envelope of each node over the window, with its errors."""
    w, end = window(since, until)
    if prom is None:
        prom = dgx_metrics.query
    if hosts is None:
        hosts = {role: dgx_hosts.lookup(role) for role in dgx_hosts.ROLES}
    answers: dict[str, Answer] = {}
    got: dict[str, dict[str, float | None]] = {}
    for name, query in promql(w, end):
        answers[name] = parse(prom(query))
        got[name] = for_role(answers[name].values, hosts, PORT[name])
    nodes: list[Node] = []
    errors: list[str] = []
    warnings: list[str] = []
    for role in dgx_hosts.ROLES:
        g = {name: got[name][role] for name in got}
        dcgm = [
            c
            for c in (
                coverage(g["gpu_temp_count"], w, DCGM_PORT),
                coverage(g["power_count"], w, DCGM_PORT),
            )
            if c is not None
        ]
        node = Node(
            role=role,
            gpu_temp_max_c=g["gpu_temp_max"],
            power_avg_w=_div(g["power_sum"], g["power_count"]),
            power_max_w=g["power_max"],
            mem_avail_min_gib=_div(g["mem_min"], GIB),
            dcgm_coverage=min(dcgm) if dcgm else None,
            node_exporter_coverage=coverage(g["mem_count"], w, NODE_EXPORTER_PORT),
        )
        nodes.append(node)
        for field, needs in FIELD_QUERIES.items():
            if getattr(node, field) is not None:
                continue
            missing = next((q for q in needs if g[q] is None), needs[0])
            why = diagnose(answers[missing], role, PORT[missing])
            errors.append(f"{role}: {field} missing: {why}")
        for field in ("dcgm_coverage", "node_exporter_coverage"):
            cov = getattr(node, field)
            if cov is None:
                continue
            if cov < FAIL_COVERAGE:
                errors.append(f"{role}: {field} {cov:.0%} is under {FAIL_COVERAGE:.0%}")
            elif cov < WARN_COVERAGE:
                warnings.append(
                    f"{role}: {field} {cov:.0%} is under {WARN_COVERAGE:.0%}"
                )
    return Envelope(nodes=nodes, errors=errors, warnings=warnings)


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


def as_json(env: Envelope, since: str, until: str) -> str:
    """The envelope as JSON, rounded as `lines` rounds it."""
    w, _end = window(since, until)
    body: dict[str, object] = {
        "since": since,
        "until": until,
        "window_s": w,
        "nodes": {
            n.role: {
                "gpu_temp_max_c": _r(n.gpu_temp_max_c, 0),
                "power_avg_w": _r(n.power_avg_w, 1),
                "power_max_w": _r(n.power_max_w, 1),
                "mem_avail_min_gib": _r(n.mem_avail_min_gib, 1),
                "dcgm_coverage": _r(n.dcgm_coverage, 2),
                "node_exporter_coverage": _r(n.node_exporter_coverage, 2),
            }
            for n in env.nodes
        },
        "errors": env.errors,
        "warnings": env.warnings,
    }
    return json.dumps(body)


def main(argv: Sequence[str] | None = None, now: float | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog=(
            "Power peak is the highest of the ~30 s DCGM power readings, not an "
            "instantaneous maximum. Exit 1 when a node lacks a value or its "
            f"sample coverage is under {FAIL_COVERAGE:.0%}."
        ),
    )
    p.add_argument("--since", required=True, help="window start, ISO 8601")
    p.add_argument("--until", required=True, help="window end, ISO 8601")
    p.add_argument("--json", action="store_true", help="print JSON, not lines")
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    try:
        window(args.since, args.until, time.time() if now is None else now)
    except ValueError as err:
        p.error(str(err))
    try:
        env = envelope(args.since, args.until)
    except dgx_hosts.HostUnknown as err:
        logger.error("error: %s", err)
        return 2
    if args.json:
        logger.info("%s", as_json(env, args.since, args.until))
    else:
        for line in lines(env.nodes):
            logger.info("%s", line)
    for text in env.warnings:
        logger.warning("warning: %s", text)
    for text in env.errors:
        logger.error("error: %s", text)
    return 1 if env.errors else 0


if __name__ == "__main__":
    sys.exit(main())
