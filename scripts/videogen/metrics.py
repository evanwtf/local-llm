"""Export video-gen throughput to Prometheus through node_exporter's textfile collector (#1406).

    metrics.py [--root ~/bench-logs/videogen] [--textfile-dir DIR] [--include-probes]

vLLM-Omni's own /metrics has per-request latency and seconds per step, but no
counter for video frames or video seconds, and its token counters read 0 for
a diffusion model. The facts are in each clip's `run.json`, so this script
reads them and writes `<textfile dir>/videogen.prom`. node_exporter reads that
file on each scrape (`--collector.textfile.directory`), and Prometheus
already scrapes node_exporter.

Inputs, under `--root`:

- `out/<arm>/<clip>-s<seed>/run.json`: scored runs (`kind="scored"`).
- `probe/<arm>/<run dir>/run.json`: probes (`kind="probe"`), only with
  `--include-probes`.

Every series has the labels `arm`, `model` (the served model id), `node`
(head or worker), `mode` (t2v or i2v), `kind`, `width`, `height` and `fps`
(the output fps). A throughput compares only at one size, so the size is a
label. There is no clip or seed label, so the series count stays small.

- Counters sum the ok runs: frames, video seconds, wall seconds, server
  seconds, complete energy and the clips that have it, and denoising steps.
  `videogen_clips_total` counts every run, by `status`.
- Gauges hold the most recent ok run of each label set, by its end time.

A `run.json` that is not a JSON object, that has no status, or that says ok
but lacks the frames, fps, wall time or end time is skipped with a warning.
Nothing in a run stops the export.

The file is a pure function of the run.json files, except
`videogen_metrics_build_timestamp_seconds`. The script writes it through a
temp file in the same directory and a rename, so a scrape never reads half
a file. The temp name does not end in `.prom`, because node_exporter reads
every `*.prom` file in the directory.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
import math
import os
import pathlib
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import DEFAULT_ROOT

import logs

logger = logging.getLogger(__name__)

#: node_exporter's textfile collector directory on the DGX head.
DEFAULT_TEXTFILE_DIR = pathlib.Path("/var/lib/node_exporter/textfile_collector")
PROM_FILE = "videogen.prom"
SCORED, PROBE = "scored", "probe"
UNKNOWN = "unknown"
#: The label names of every series, in this order.
LABELS = ("arm", "model", "node", "mode", "kind", "width", "height", "fps")

LabelSet = tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Ok:
    """The measurements of one ok run."""

    frames: int
    fps: float
    wall_s: float
    end: float
    server_s: float | None
    energy_wh: float | None
    steps: int | None
    peak_gib: float | None

    @property
    def video_s(self) -> float:
        return self.frames / self.fps


@dataclasses.dataclass(frozen=True)
class Run:
    path: pathlib.Path
    labels: LabelSet
    status: str
    ok: Ok | None


# ---------------------------------------------------------------- reading ----


def as_dict(value: Any) -> dict[str, Any]:
    """`value` if it is a dict, else {}: run.json fields can be null or absent."""
    return value if isinstance(value, dict) else {}


def number(value: Any) -> float | None:
    """A finite float from a number or a numeric string, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str):
        try:
            out = float(value)
        except ValueError:
            return None
    else:
        return None
    return out if math.isfinite(out) else None


def positive(value: Any) -> float | None:
    out = number(value)
    return out if out is not None and out > 0 else None


def label_number(*candidates: Any) -> str:
    """The first numeric candidate as a label: 24.0 and "24" both give "24"."""
    for value in candidates:
        out = number(value)
        if out is not None:
            return f"{out:g}"
    return UNKNOWN


def text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def timestamp(value: Any) -> float | None:
    """Unix seconds from generate.py's ISO 8601 time with an offset."""
    if not isinstance(value, str):
        return None
    try:
        # generate.py's format for `start` and `end`; %z makes it aware.
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None
    return parsed.timestamp()


def labels_of(run: Mapping[str, Any], arm: str, kind: str) -> LabelSet:
    """The label values of a run, in LABELS order.

    The size comes from ffprobe; a failed run may have none, so the expected
    size and then the request stand in.
    """
    probe = as_dict(run.get("ffprobe"))
    expected = as_dict(run.get("expected"))
    fields = as_dict(as_dict(run.get("request")).get("fields"))
    model = (
        text(as_dict(run.get("server_config")).get("served_model_name"))
        or text(run.get("model"))
        or UNKNOWN
    )
    return (
        arm,
        model,
        text(as_dict(run.get("node")).get("role")) or UNKNOWN,
        text(run.get("mode")) or UNKNOWN,
        kind,
        label_number(probe.get("width"), expected.get("width"), fields.get("width")),
        label_number(probe.get("height"), expected.get("height"), fields.get("height")),
        label_number(probe.get("fps"), expected.get("fps"), fields.get("fps")),
    )


def ok_of(run: Mapping[str, Any]) -> Ok | str:
    """The measurements of an ok run, or why they are incomplete."""
    probe = as_dict(run.get("ffprobe"))
    frames = positive(probe.get("frames"))
    fps = positive(probe.get("fps"))
    wall = positive(run.get("wall_s"))
    end = timestamp(run.get("end"))
    missing = [
        name
        for name, value in (
            ("ffprobe frames", frames),
            ("ffprobe fps", fps),
            ("wall_s", wall),
            ("end", end),
        )
        if value is None
    ]
    if missing or frames is None or fps is None or wall is None or end is None:
        return "it says ok but has no " + ", ".join(missing)
    steps = positive(
        as_dict(as_dict(run.get("request")).get("fields")).get("num_inference_steps")
    )
    return Ok(
        frames=int(frames),
        fps=fps,
        wall_s=wall,
        end=end,
        server_s=positive(as_dict(run.get("server_headers")).get("x-inference-time-s")),
        # generate.py sets the top-level energy_wh only for a complete reading.
        energy_wh=number(run.get("energy_wh")),
        steps=int(steps) if steps is not None else None,
        peak_gib=positive(as_dict(run.get("memory")).get("peak_used_gib")),
    )


def read_run(path: pathlib.Path, kind: str) -> Run | None:
    """One run.json as a Run, or None (with a warning) if it is bad."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as err:
        logger.warning("skipped %s: unreadable (%s)", path, err)
        return None
    if not isinstance(data, dict):
        logger.warning("skipped %s: not a JSON object", path)
        return None
    status = text(data.get("status"))
    if status is None:
        logger.warning("skipped %s: no status", path)
        return None
    ok: Ok | None = None
    if status == "ok":
        found = ok_of(data)
        if isinstance(found, str):
            logger.warning("skipped %s: %s", path, found)
            return None
        ok = found
    labels = labels_of(data, path.parent.parent.name, kind)
    return Run(path=path, labels=labels, status=status, ok=ok)


def find_runs(root: pathlib.Path, *, include_probes: bool = False) -> list[Run]:
    """Every readable run under out/, and under probe/ if asked, in path order."""
    tops = [(SCORED, "out")] + ([(PROBE, "probe")] if include_probes else [])
    runs: list[Run] = []
    for kind, top in tops:
        for path in sorted((root / top).glob("*/*/run.json")):
            run = read_run(path, kind)
            if run is not None:
                runs.append(run)
    return runs


# -------------------------------------------------------------- rendering ----


def escape_label(value: str) -> str:
    """A label value per the text exposition format: \\, " and newline."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def escape_help(value: str) -> str:
    """HELP text per the text exposition format: \\ and newline."""
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def fmt_value(value: float) -> str:
    """A sample value: an integer as is, a float to 12 significant digits."""
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return format(value, ".12g")


def series(name: str, names: Sequence[str], values: Sequence[str], value: float) -> str:
    pairs = ",".join(
        f'{key}="{escape_label(val)}"' for key, val in zip(names, values, strict=True)
    )
    return (
        f"{name}{{{pairs}}} {fmt_value(value)}"
        if pairs
        else f"{name} {fmt_value(value)}"
    )


@dataclasses.dataclass
class Family:
    name: str
    kind: str  # counter or gauge
    help: str
    samples: list[tuple[tuple[str, ...], tuple[str, ...], float]] = dataclasses.field(
        default_factory=list
    )

    def add(
        self, values: Sequence[str], value: float, names: Sequence[str] = LABELS
    ) -> None:
        self.samples.append((tuple(names), tuple(values), value))

    def lines(self) -> Iterable[str]:
        yield f"# HELP {self.name} {escape_help(self.help)}"
        yield f"# TYPE {self.name} {self.kind}"
        for names, values, value in self.samples:
            yield series(self.name, names, values, value)


def render(runs: Iterable[Run], *, now: float) -> str:
    """The Prometheus text exposition of `runs`; `now` is the build time."""
    runs = list(runs)
    by_status: dict[tuple[LabelSet, str], int] = {}
    oks: dict[LabelSet, list[Run]] = {}
    for run in runs:
        by_status[(run.labels, run.status)] = (
            by_status.get((run.labels, run.status), 0) + 1
        )
        if run.ok is not None:
            oks.setdefault(run.labels, []).append(run)

    clips = Family(
        "videogen_clips_total",
        "counter",
        "Video-gen runs with a run.json, by status (ok, failed). It can fall when "
        "a failed run is made again.",
    )
    for labels, status in sorted(by_status):
        clips.add((*labels, status), by_status[(labels, status)], (*LABELS, "status"))

    frames = Family(
        "videogen_frames_total",
        "counter",
        "Output video frames (ffprobe) of the ok runs. These are frames in the "
        "MP4, not the denoised latent frames.",
    )
    video = Family(
        "videogen_video_seconds_total",
        "counter",
        "Seconds of output video of the ok runs: frames / output fps.",
    )
    wall = Family(
        "videogen_wall_seconds_total",
        "counter",
        "Client wall seconds of the ok runs' requests.",
    )
    server = Family(
        "videogen_server_seconds_total",
        "counter",
        "Server inference seconds (x-inference-time-s) of the ok runs that report it.",
    )
    energy = Family(
        "videogen_energy_wh_total",
        "counter",
        "Outlet energy in Wh of the ok runs with a complete reading; a partial "
        "reading is left out.",
    )
    energy_clips = Family(
        "videogen_energy_clips_total",
        "counter",
        "The ok runs with a complete outlet energy reading.",
    )
    steps = Family(
        "videogen_steps_total",
        "counter",
        "Denoising steps requested (num_inference_steps) of the ok runs.",
    )
    last_fps = Family(
        "videogen_last_frames_per_second",
        "gauge",
        "The latest ok run: output frames / wall seconds. Output frames, not "
        "the denoised latent frames.",
    )
    last_wall = Family(
        "videogen_last_wall_seconds", "gauge", "The latest ok run: wall seconds."
    )
    last_rtf = Family(
        "videogen_last_realtime_factor",
        "gauge",
        "The latest ok run: wall seconds / seconds of output video.",
    )
    last_peak = Family(
        "videogen_last_peak_memory_gib",
        "gauge",
        "The latest ok run: peak used memory of the whole node, GiB.",
    )
    last_energy = Family(
        "videogen_last_energy_wh",
        "gauge",
        "The latest ok run: outlet energy in Wh, when its reading is complete.",
    )
    last_ts = Family(
        "videogen_last_timestamp_seconds",
        "gauge",
        "The latest ok run: its end time, Unix seconds.",
    )
    for labels in sorted(oks):
        group = [r.ok for r in oks[labels] if r.ok is not None]
        frames.add(labels, sum(o.frames for o in group))
        video.add(labels, sum(o.video_s for o in group))
        wall.add(labels, sum(o.wall_s for o in group))
        if any(o.server_s is not None for o in group):
            server.add(labels, sum(o.server_s or 0.0 for o in group))
        energy.add(labels, sum(o.energy_wh or 0.0 for o in group))
        energy_clips.add(labels, sum(1 for o in group if o.energy_wh is not None))
        if any(o.steps is not None for o in group):
            steps.add(labels, sum(o.steps or 0 for o in group))
        latest = max(
            oks[labels], key=lambda r: (r.ok.end if r.ok else 0.0, str(r.path))
        )
        last = latest.ok
        assert last is not None
        last_fps.add(labels, last.frames / last.wall_s)
        last_wall.add(labels, last.wall_s)
        last_rtf.add(labels, last.wall_s / last.video_s)
        if last.peak_gib is not None:
            last_peak.add(labels, last.peak_gib)
        if last.energy_wh is not None:
            last_energy.add(labels, last.energy_wh)
        last_ts.add(labels, last.end)

    build = Family(
        "videogen_metrics_build_timestamp_seconds",
        "gauge",
        "When metrics.py last wrote this file, Unix seconds.",
    )
    build.add((), now, ())

    families = [
        clips,
        frames,
        video,
        wall,
        server,
        energy,
        energy_clips,
        steps,
        last_fps,
        last_wall,
        last_rtf,
        last_peak,
        last_energy,
        last_ts,
        build,
    ]
    return "\n".join(line for family in families for line in family.lines()) + "\n"


# ---------------------------------------------------------------- writing ----


def write_atomic(path: pathlib.Path, body: str) -> None:
    """Write `body` to `path` through a temp file in the same directory.

    The temp name does not end in `.prom`, so node_exporter never reads it.
    """
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        # node_exporter runs as another user; it must be able to read the file.
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write(
    root: pathlib.Path = DEFAULT_ROOT,
    textfile_dir: pathlib.Path = DEFAULT_TEXTFILE_DIR,
    *,
    include_probes: bool = False,
    now: float | None = None,
) -> pathlib.Path:
    """Write `<textfile_dir>/videogen.prom` from the runs under `root`.

    Raises FileNotFoundError when the directory is missing, and OSError when
    it cannot be written.
    """
    if not textfile_dir.is_dir():
        raise FileNotFoundError(f"no textfile collector directory {textfile_dir}")
    runs = find_runs(root.expanduser(), include_probes=include_probes)
    path = textfile_dir / PROM_FILE
    write_atomic(path, render(runs, now=time.time() if now is None else now))
    logger.info(
        "videogen metrics: %d runs (%d ok) -> %s",
        len(runs),
        sum(1 for r in runs if r.ok is not None),
        path,
    )
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--root",
        type=pathlib.Path,
        default=DEFAULT_ROOT,
        help="the video-gen root that holds out/ and probe/",
    )
    p.add_argument(
        "--textfile-dir",
        type=pathlib.Path,
        default=DEFAULT_TEXTFILE_DIR,
        help="node_exporter's --collector.textfile.directory",
    )
    p.add_argument(
        "--include-probes",
        action="store_true",
        help='add the runs under probe/, with kind="probe"',
    )
    return p


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    args = build_parser().parse_args(argv)
    try:
        write(args.root, args.textfile_dir, include_probes=args.include_probes)
    except OSError as err:
        logger.error("%s", err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
