"""Evidence for the GB10 GPU-idle-stall floor: how each floor would judge history.

The agent harness kills a trial when the server GPU stays under an idle floor
for 600 s (#366). On the two-Spark cluster it reads the busier node. #1450
lowered the GB10 floor from 20 W because a light model decodes at 15.8 to
17.3 W per GPU. This script measures how low the floor may go: the worker
node idles with a TP2 model resident at about 12 W, so a floor at 12 W misses
most real stalls.

It reads Prometheus through Grafana with gcx (read-only), over `--days` of
history, in 10-minute windows (the watchdog's 600 s window):

* the peak GPU power of each node in the window, from DCGM; the window's
  power is the larger of the two, because the watchdog reads the busier node;
* whether a model was resident (`llme_engine_up`), and which;
* whether the engine served anything (`llme_requests_running`, and the
  increase of `llme_tokens_total` and `llme_requests_total`).

Then, for each floor:

* a **quiet** window (model resident, no running request, no token or request
  increase) whose peak is at or above the floor is a **miss**: a stalled
  trial there would ride its full timeout;
* a **generating** window (the engine produced tokens or held a request) or
  a **live** one (a request running for the whole window) whose peak stays
  under the floor is a **kill**: the watchdog would read a working stretch
  as idle. The light-model column is the generating windows of `--light`.

The **rule** line counts the watchdog's #1453 rule: a window whose resident
engine exports a running-request signal (vLLM, SGLang, llama.cpp) is judged
at the engine-rule floor (20 W), because the engine says when a request runs;
every other window is judged by power alone at the GB10 tier floor (13 W).
A generating window under the engine rule is never killed.

Windows are back to back, not sliding, so the counts are a lower bound on
what a sliding 600 s watchdog sees; the ratios between floors are the point.

    uv run python scripts/idle_floor_evidence.py --days 60

The node names come from `dgx_hosts` (never from the repo, #1153). The gcx
call is the only untested part; the parsing and the counting are tested in
tests/test_idle_floor_evidence.py.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# timeout_policy owns the floors and the engine table (#1453).
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import dgx_hosts
import dgx_metrics
import timeout_policy

import logs

logger = logging.getLogger(__name__)

#: The watchdog's window (timeout_policy.DEFAULT_IDLE_STALL_SECS), and the step.
WINDOW_S = 600
#: The longest span one range query asks for. Grafana widens the step of a
#: longer query to keep about 120 points (30 days at 600 s came back at a 6 h
#: step, 2026-11-21), so ask for 120 windows at a time. `fetch` refuses any
#: point off the 600 s grid, so a widened step cannot pass unseen.
CHUNK_S = 120 * WINDOW_S
#: The floors the #1450 review compared.
DEFAULT_FLOORS: tuple[float, ...] = (12.0, 13.0, 14.0, 20.0)

W = f"{WINDOW_S}s"
#: (name, PromQL template). `$head` and `$worker` are filled at run time.
SERIES: tuple[tuple[str, str], ...] = (
    ("head_peak_w", f'max_over_time(DCGM_FI_DEV_POWER_USAGE{{instance="$head:9400"}}[{W}])'),
    ("worker_peak_w", f'max_over_time(DCGM_FI_DEV_POWER_USAGE{{instance="$worker:9400"}}[{W}])'),
    ("running_max", f"sum(max_over_time(llme_requests_running[{W}]))"),
    ("running_min", f"sum(min_over_time(llme_requests_running[{W}]))"),
    ("tokens", f"sum(increase(llme_tokens_total[{W}]))"),
    ("requests", f"sum(increase(llme_requests_total[{W}]))"),
)  # fmt: skip
#: Per engine and model: 1 while that engine is up in the window.
RESIDENT = f"max by (engine, model) (max_over_time(llme_engine_up[{W}])) == 1"
#: The exporter's `engine` label, as tasks.toml writes the engine.
EXPORTER_ENGINE = {"llamacpp": "llama.cpp"}


@dataclass(frozen=True)
class Window:
    """One 10-minute window. None: the series had no sample there."""

    ts: int
    peak_w: float | None
    models: tuple[str, ...]
    running_max: float | None
    running_min: float | None
    tokens: float | None
    requests: float | None
    #: The exporter's engine labels up in the window. Empty in --save files
    #: written before #1453.
    engines: tuple[str, ...] = ()

    @property
    def resident(self) -> bool:
        return bool(self.models)

    @property
    def quiet(self) -> bool:
        """A model resident and the engine served nothing in the window.

        The token counter must be present: an engine that exports no counters
        cannot show it was quiet, so its windows are left out.
        """
        if not self.resident or self.tokens is None:
            return False
        return all(not v for v in (self.running_max, self.tokens, self.requests))

    @property
    def generating(self) -> bool:
        """The engine generated tokens or held a request in the window."""
        return self.resident and (
            (self.tokens or 0) > 0 or (self.running_max or 0) >= 1
        )

    @property
    def live(self) -> bool:
        """A request ran for the whole window: a trial mid-generation."""
        return self.resident and (self.running_min or 0) >= 1


def matrix_series(
    gcx_json: str,
) -> list[tuple[dict[str, str], list[tuple[int, float]]]]:
    """(labels, [(unix s, value)]) per series of a gcx range query."""
    try:
        result = json.loads(gcx_json)["data"]["result"]
    except (ValueError, KeyError, TypeError):
        return []
    out: list[tuple[dict[str, str], list[tuple[int, float]]]] = []
    for series in result:
        points: list[tuple[int, float]] = []
        for ts, value in series.get("values", []):
            try:
                points.append((int(float(ts)), float(value)))
            except (TypeError, ValueError):
                continue
        out.append((dict(series.get("metric", {})), points))
    return out


def merge_points(gcx_json: str) -> dict[int, float]:
    """The single series of a query, by timestamp (the first series wins)."""
    got: dict[int, float] = {}
    for _labels, points in matrix_series(gcx_json):
        for ts, value in points:
            got.setdefault(ts, value)
    return got


def resident_models(gcx_json: str) -> dict[int, tuple[str, ...]]:
    """The models up at each timestamp, sorted."""
    got: dict[int, set[str]] = {}
    for labels, points in matrix_series(gcx_json):
        model = labels.get("model", "unknown")
        for ts, value in points:
            if value >= 1:
                got.setdefault(ts, set()).add(model)
    return {ts: tuple(sorted(m)) for ts, m in got.items()}


def resident_engines(gcx_json: str) -> dict[int, tuple[str, ...]]:
    """The exporter engine labels up at each timestamp, sorted."""
    got: dict[int, set[str]] = {}
    for labels, points in matrix_series(gcx_json):
        engine = labels.get("engine", "unknown")
        for ts, value in points:
            if value >= 1:
                got.setdefault(ts, set()).add(engine)
    return {ts: tuple(sorted(e)) for ts, e in got.items()}


def has_engine_signal(engines: Sequence[str]) -> bool:
    """Would the watchdog read a running-request signal from these engines?"""
    return any(
        EXPORTER_ENGINE.get(e, e) in timeout_policy.ENGINE_SIGNALS for e in engines
    )


def windows(
    series: Mapping[str, Mapping[int, float]],
    models: Mapping[int, tuple[str, ...]],
    engines: Mapping[int, tuple[str, ...]] | None = None,
) -> list[Window]:
    """Join the series on timestamp into windows that have a power reading."""
    head = series.get("head_peak_w", {})
    worker = series.get("worker_peak_w", {})
    out: list[Window] = []
    for ts in sorted(set(head) | set(worker)):
        peaks = [v for v in (head.get(ts), worker.get(ts)) if v is not None]
        out.append(
            Window(
                ts=ts,
                peak_w=max(peaks) if peaks else None,
                models=models.get(ts, ()),
                running_max=series.get("running_max", {}).get(ts),
                running_min=series.get("running_min", {}).get(ts),
                tokens=series.get("tokens", {}).get(ts),
                requests=series.get("requests", {}).get(ts),
                engines=(engines or {}).get(ts, ()),
            )
        )
    return out


@dataclass(frozen=True)
class FloorRow:
    """One floor's verdict on the history. Each pair is (hits, of windows)."""

    floor_w: float
    quiet: int
    missed: int
    generating: int
    gen_killed: int
    live: int
    live_killed: int
    light: int
    light_killed: int


def count(
    wins: Sequence[Window], floors: Sequence[float], light: str = "inkling"
) -> list[FloorRow]:
    """Per floor: quiet windows it misses, working windows it would kill.

    A quiet window is missed when its peak reaches the floor: the watchdog
    resets on any reading at or above it, so a stall there is never seen. A
    working window (generating, live, or a generating window of the light
    model named by the case-insensitive substring `light`) is killed when its
    peak stays under the floor: the whole 600 s reads as idle.
    """
    have = [w for w in wins if w.peak_w is not None]
    quiet = [w for w in have if w.quiet]
    gen = [w for w in have if w.generating]
    live = [w for w in have if w.live]
    lite = [w for w in gen if any(light in m.lower() for m in w.models)]

    def under(ws: Sequence[Window], f: float) -> int:
        return sum(1 for w in ws if w.peak_w is not None and w.peak_w < f)

    return [
        FloorRow(
            floor_w=f,
            quiet=len(quiet),
            missed=len(quiet) - under(quiet, f),
            generating=len(gen),
            gen_killed=under(gen, f),
            live=len(live),
            live_killed=under(live, f),
            light=len(lite),
            light_killed=under(lite, f),
        )
        for f in floors
    ]


def count_rule(
    wins: Sequence[Window],
    power_floor: float,
    engine_floor: float,
    light: str = "inkling",
) -> FloorRow:
    """The #1453 rule's verdict on the history, as one row.

    A window whose engine has a running-request signal is judged at
    `engine_floor`, and only a quiet one can read as idle there: a working
    engine is never killed. Every other window is judged by power alone at
    `power_floor`. `floor_w` holds the engine floor.
    """
    have = [w for w in wins if w.peak_w is not None]
    quiet = [w for w in have if w.quiet]
    gen = [w for w in have if w.generating]
    live = [w for w in have if w.live]
    lite = [w for w in gen if any(light in m.lower() for m in w.models)]

    def floor(w: Window) -> float:
        return engine_floor if has_engine_signal(w.engines) else power_floor

    def missed(ws: Sequence[Window]) -> int:
        return sum(1 for w in ws if w.peak_w is not None and w.peak_w >= floor(w))

    def killed(ws: Sequence[Window]) -> int:
        return sum(
            1
            for w in ws
            if not has_engine_signal(w.engines)
            and w.peak_w is not None
            and w.peak_w < power_floor
        )

    return FloorRow(
        floor_w=engine_floor,
        quiet=len(quiet),
        missed=missed(quiet),
        generating=len(gen),
        gen_killed=killed(gen),
        live=len(live),
        live_killed=killed(live),
        light=len(lite),
        light_killed=killed(lite),
    )


def missed_by_engine(
    wins: Sequence[Window], power_floor: float, engine_floor: float
) -> dict[str, tuple[int, int, int]]:
    """Per resident engine set: (quiet windows, missed by power, missed by rule).

    Shows where the rule's gain comes from, and what stays on the power rule.
    """
    out: dict[str, tuple[int, int, int]] = {}
    for w in wins:
        if w.peak_w is None or not w.quiet:
            continue
        key = "+".join(w.engines) or "unknown"
        floor = engine_floor if has_engine_signal(w.engines) else power_floor
        q, old, new = out.get(key, (0, 0, 0))
        out[key] = (
            q + 1,
            old + (w.peak_w >= power_floor),
            new + (w.peak_w >= floor),
        )
    return dict(sorted(out.items()))


def _cell(n: int, d: int) -> str:
    return f"{n} of {d} ({100 * n / d:.0f}%)" if d else f"{n} of 0"


def table(rows: Sequence[FloorRow], light: str = "inkling") -> list[str]:
    """The report lines, one per floor, under a header."""
    lines = [
        (
            "floor | quiet: missed | generating: killed | live: killed"
            f" | {light} generating: killed"
        ),
        "---|---|---|---|---",
    ]
    for r in rows:
        lines.append(
            f"{r.floor_w:g} W | {_cell(r.missed, r.quiet)}"
            f" | {_cell(r.gen_killed, r.generating)}"
            f" | {_cell(r.live_killed, r.live)}"
            f" | {_cell(r.light_killed, r.light)}"
        )
    return lines


# ------------------------------------------------------------------ gcx ----


def range_query(promql: str, start: int, end: int, step: int) -> str:
    """Raw gcx JSON for one range query, or "" on failure. Read-only."""
    env = dict(os.environ)
    try:
        if dgx_metrics.TOKEN_PATH.exists():
            env["GRAFANA_TOKEN"] = dgx_metrics.TOKEN_PATH.read_text().strip()
    except OSError:
        pass
    argv = ["gcx", "metrics", "query", "-d", dgx_metrics.DATASOURCE, promql]
    argv += ["--from", str(start), "--to", str(end), "--step", f"{step}s"]
    try:
        out = subprocess.run(
            [*argv, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout


def spans(start: int, end: int, size: int) -> list[tuple[int, int]]:
    """[start, end] cut into back-to-back spans of at most `size` seconds."""
    out: list[tuple[int, int]] = []
    s = start
    while s < end:
        out.append((s, min(s + size, end)))
        s += size
    return out


def off_grid(timestamps: Sequence[int], step: int) -> list[int]:
    """Timestamps not on the step grid: a sign the server widened the step."""
    return [ts for ts in timestamps if ts % step]


def fetch(
    start: int,
    end: int,
    prom: Callable[[str, int, int, int], str] = range_query,
) -> list[Window]:
    """Every window from start to end, from Prometheus. Start is on the grid."""
    cuts = spans(start, end, CHUNK_S)
    series: dict[str, dict[int, float]] = {}
    for name, template in SERIES:
        promql = dgx_hosts.fill(template)
        got: dict[int, float] = {}
        for a, b in cuts:
            got.update(merge_points(prom(promql, a, b, WINDOW_S)))
        if bad := off_grid(list(got), WINDOW_S):
            raise SystemExit(f"{name}: {len(bad)} points off the {W} grid")
        series[name] = got
        logger.info("%s: %d windows", name, len(got))
    models: dict[int, tuple[str, ...]] = {}
    engines: dict[int, tuple[str, ...]] = {}
    for a, b in cuts:
        raw = prom(RESIDENT, a, b, WINDOW_S)
        models.update(resident_models(raw))
        engines.update(resident_engines(raw))
    return windows(series, models, engines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--days", type=float, default=60.0)
    p.add_argument(
        "--floors",
        type=float,
        nargs="+",
        default=list(DEFAULT_FLOORS),
        help="floors to compare, in W",
    )
    p.add_argument("--light", default="inkling", help="light-model name substring")
    p.add_argument(
        "--save", type=pathlib.Path, help="write the fetched windows as JSON"
    )
    p.add_argument(
        "--load",
        type=pathlib.Path,
        help="read windows from a --save file instead of Prometheus",
    )
    args = p.parse_args(argv)
    logs.configure()
    if args.load:
        wins = [
            Window(
                **{
                    **w,
                    "models": tuple(w["models"]),
                    "engines": tuple(w.get("engines", ())),
                }
            )
            for w in json.loads(args.load.read_text())
        ]
    else:
        end = int(time.time())
        end -= end % WINDOW_S
        start = end - int(args.days * 86400)
        wins = fetch(start, end)
    if args.save:
        args.save.write_text(json.dumps([dataclasses.asdict(w) for w in wins]))
        logger.info("wrote %d windows to %s", len(wins), args.save)
    if not any(w.peak_w is not None for w in wins):
        logger.error("no GPU power data: is gcx authenticated?")
        return 1
    for line in table(count(wins, args.floors, args.light), args.light):
        logger.info("%s", line)
    power = timeout_policy.TIER_IDLE_FLOOR_WATTS["gb10-spark-x2"]
    engine = timeout_policy.ENGINE_IDLE_FLOOR_WATTS
    rule = count_rule(wins, power, engine, args.light)
    engine_wins = sum(1 for w in wins if w.quiet and has_engine_signal(w.engines))
    logger.info(
        "#1453 rule: engine windows at %g W, the rest by power at %g W"
        " (%d of %d quiet windows have an engine signal)",
        engine,
        power,
        engine_wins,
        rule.quiet,
    )
    for line in table([rule], args.light)[2:]:
        logger.info("rule %s", line)
    for key, (q, old, new) in missed_by_engine(wins, power, engine).items():
        logger.info(
            "quiet windows of %s: %d; missed at %g W by power %d, by the rule %d",
            key,
            q,
            power,
            old,
            new,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
