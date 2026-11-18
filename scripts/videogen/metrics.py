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

- Sums over the ok runs: frames, video seconds, wall seconds, server
  seconds, complete energy, denoising steps, and the number of runs that
  report each optional value. `videogen_clips` counts every run, by `status`.
- `videogen_last_*` hold the most recent ok run of each label set, by its
  end time.

Every series is a gauge, not a counter. The file is a snapshot of the
run.json files on disk, and a sum can fall: a remake deletes the old
run.json for about 40 minutes, and the other arm's batch writes the file in
that gap. A counter that falls looks like a reset to Prometheus. Units are
Prometheus base units: seconds, joules, bytes.

A `run.json` that is not a JSON object, that has no status, or that says ok
but lacks the frames, fps, wall time or end time is skipped with a warning.
Nothing in a run stops the export.

Two batches (one per node) write the same file, so the scan and the write
hold an exclusive `flock` on `<root>/.metrics.lock`.

**Images (#1424)** get their own families, `videogen_images{status}` and
`videogen_image_*`, with the labels `arm`, `model`, `node`, `mode` (t2i or
edit), `kind`, `width` and `height`; no `fps`. Their own families, not a
label on the clip families: an image has no frames, no fps and no video
seconds, and a label would put image wall seconds into
`videogen_wall_seconds`, so every existing query over it (frames per wall
second, the real-time factor) would mix the two. There is no image server
time: the image endpoints send no `x-inference-time-s`.

The file is a pure function of the run.json files, except
`videogen_metrics_build_timestamp_seconds`. The script writes it through a
temp file in the same directory and a rename, so a scrape never reads half
a file. The temp name does not end in `.prom`, because node_exporter reads
every `*.prom` file in the directory.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import fcntl
import json
import logging
import math
import os
import pathlib
import sys
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
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
#: The label names of the image series (#1424): an image has no fps.
IMAGE_LABELS = ("arm", "model", "node", "mode", "kind", "width", "height")
#: Held around the scan and the write, so two batches cannot interleave.
LOCK_FILE = ".metrics.lock"
#: How long a writer waits for the lock. A scan takes milliseconds; a holder
#: stuck longer than this must not stall a batch.
LOCK_TIMEOUT_S = 60.0
JOULES_PER_WH = 3600.0
BYTES_PER_GIB = 2.0**30

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
class ImageOk:
    """The measurements of one ok image run (#1424)."""

    wall_s: float
    end: float
    energy_wh: float | None
    steps: int | None
    peak_gib: float | None


@dataclasses.dataclass(frozen=True)
class Run:
    path: pathlib.Path
    labels: LabelSet
    status: str
    ok: Ok | None
    #: "video" (labels in LABELS, `ok`) or "image" (IMAGE_LABELS, `image`).
    media: str = "video"
    image: ImageOk | None = None


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
    """The first positive candidate as a label: 24.0 and "24" both give "24".

    Never 0: an empty or broken ffprobe reading must not make its own series.
    """
    for value in candidates:
        out = positive(value)
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

    The size is what the run asked for: `expected`, then the request fields,
    then ffprobe. An ok run's ffprobe matches `expected` (generate.py fails a
    mismatch), and a failed run's ffprobe can be empty or wrong.
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
        label_number(expected.get("width"), fields.get("width"), probe.get("width")),
        label_number(expected.get("height"), fields.get("height"), probe.get("height")),
        label_number(expected.get("fps"), fields.get("fps"), probe.get("fps")),
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
    if int(frames) < 1:
        return f"it says ok but has {frames:g} ffprobe frames"
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


def image_size(run: Mapping[str, Any]) -> tuple[Any, Any]:
    """(width, height) an image run asked for: `expected`, the request's
    `WxH` size, then the PNG it found."""
    expected = as_dict(run.get("expected"))
    size = as_dict(as_dict(run.get("request")).get("fields")).get("size")
    asked = str(size).partition("x") if isinstance(size, str) else ("", "", "")
    found = as_dict(run.get("found"))
    return (
        label_number(expected.get("width"), asked[0], found.get("width")),
        label_number(expected.get("height"), asked[2], found.get("height")),
    )


def image_labels_of(run: Mapping[str, Any], arm: str, kind: str) -> LabelSet:
    """The label values of an image run, in IMAGE_LABELS order."""
    width, height = image_size(run)
    return labels_of(run, arm, kind)[:5] + (width, height)


def image_ok_of(run: Mapping[str, Any]) -> ImageOk | str:
    """The measurements of an ok image run, or why they are incomplete."""
    wall = positive(run.get("wall_s"))
    end = timestamp(run.get("end"))
    if wall is None or end is None:
        missing = [n for n, v in (("wall_s", wall), ("end", end)) if v is None]
        return "it says ok but has no " + ", ".join(missing)
    steps = positive(
        as_dict(as_dict(run.get("request")).get("fields")).get("num_inference_steps")
    )
    return ImageOk(
        wall_s=wall,
        end=end,
        energy_wh=number(run.get("energy_wh")),
        steps=int(steps) if steps is not None else None,
        peak_gib=positive(as_dict(run.get("memory")).get("peak_used_gib")),
    )


def read_run(path: pathlib.Path, kind: str) -> Run | None:
    """One run.json as a Run, or None (with a log line) if it is bad."""
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
    arm = path.parent.parent.name
    if data.get("media") == "image":
        image: ImageOk | None = None
        if status == "ok":
            got = image_ok_of(data)
            if isinstance(got, str):
                logger.warning("skipped %s: %s", path, got)
                return None
            image = got
        return Run(
            path=path,
            labels=image_labels_of(data, arm, kind),
            status=status,
            ok=None,
            media="image",
            image=image,
        )
    ok: Ok | None = None
    if status == "ok":
        found = ok_of(data)
        if isinstance(found, str):
            # The first probes wrote schema 1, with no ffprobe block. That is
            # known and harmless, so it does not warn on every export.
            old_probe = kind == PROBE and not isinstance(data.get("ffprobe"), dict)
            logger.log(
                logging.INFO if old_probe else logging.WARNING,
                "skipped %s: %s",
                path,
                found,
            )
            return None
        ok = found
    labels = labels_of(data, arm, kind)
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
    kind: str  # gauge: see the module docstring
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
    every = list(runs)
    runs = [r for r in every if r.media == "video"]
    by_status: dict[tuple[LabelSet, str], int] = {}
    oks: dict[LabelSet, list[Ok]] = {}
    latest: dict[LabelSet, tuple[float, str, Ok]] = {}
    for run in runs:
        key = (run.labels, run.status)
        by_status[key] = by_status.get(key, 0) + 1
        if run.ok is None:
            continue
        oks.setdefault(run.labels, []).append(run.ok)
        # The latest by end time; the path breaks a tie, so the output is stable.
        mark = (run.ok.end, str(run.path), run.ok)
        if run.labels not in latest or mark[:2] > latest[run.labels][:2]:
            latest[run.labels] = mark

    def gauge(name: str, text: str) -> Family:
        return Family(name, "gauge", text)

    clips = gauge(
        "videogen_clips",
        "Video-gen runs with a run.json now, by status (ok, failed). A gauge: "
        "a remade run is absent while it runs.",
    )
    for labels, status in sorted(by_status):
        clips.add((*labels, status), by_status[(labels, status)], (*LABELS, "status"))

    frames = gauge(
        "videogen_frames",
        "Output video frames (ffprobe) summed over the ok runs. These are "
        "frames in the MP4, not the denoised latent frames.",
    )
    video = gauge(
        "videogen_video_seconds",
        "Seconds of output video summed over the ok runs: frames / output fps.",
    )
    wall = gauge(
        "videogen_wall_seconds",
        "Client wall seconds summed over the ok runs.",
    )
    server = gauge(
        "videogen_server_seconds",
        "Server inference seconds (x-inference-time-s) summed over the ok runs "
        "that report it; divide by videogen_server_clips.",
    )
    server_clips = gauge(
        "videogen_server_clips",
        "The ok runs that report the server inference time.",
    )
    energy = gauge(
        "videogen_energy_joules",
        "Outlet energy in joules summed over the ok runs with a complete "
        "reading; a partial reading is left out. Divide by videogen_energy_clips.",
    )
    energy_clips = gauge(
        "videogen_energy_clips",
        "The ok runs with a complete outlet energy reading.",
    )
    steps = gauge(
        "videogen_steps",
        "Denoising steps requested (num_inference_steps) summed over the ok runs "
        "that record it; divide by videogen_step_clips.",
    )
    step_clips = gauge(
        "videogen_step_clips",
        "The ok runs that record num_inference_steps.",
    )
    last_fps = gauge(
        "videogen_last_frames_per_second",
        "The latest ok run: output frames / wall seconds. Output frames, not "
        "the denoised latent frames.",
    )
    last_wall = gauge("videogen_last_wall_seconds", "The latest ok run: wall seconds.")
    last_rtf = gauge(
        "videogen_last_realtime_factor",
        "The latest ok run: wall seconds / seconds of output video.",
    )
    last_peak = gauge(
        "videogen_last_peak_memory_bytes",
        "The latest ok run: peak used memory of the whole node, bytes.",
    )
    last_energy = gauge(
        "videogen_last_energy_joules",
        "The latest ok run: outlet energy in joules, when its reading is complete.",
    )
    last_ts = gauge(
        "videogen_last_timestamp_seconds",
        "The latest ok run: its end time, Unix seconds.",
    )
    for labels in sorted(oks):
        group = oks[labels]
        frames.add(labels, sum(o.frames for o in group))
        video.add(labels, sum(o.video_s for o in group))
        wall.add(labels, sum(o.wall_s for o in group))
        with_server = [o.server_s for o in group if o.server_s is not None]
        if with_server:
            server.add(labels, sum(with_server))
        server_clips.add(labels, len(with_server))
        with_energy = [o.energy_wh for o in group if o.energy_wh is not None]
        energy.add(labels, sum(with_energy) * JOULES_PER_WH)
        energy_clips.add(labels, len(with_energy))
        with_steps = [o.steps for o in group if o.steps is not None]
        if with_steps:
            steps.add(labels, sum(with_steps))
        step_clips.add(labels, len(with_steps))
        last = latest[labels][2]
        last_fps.add(labels, last.frames / last.wall_s)
        last_wall.add(labels, last.wall_s)
        last_rtf.add(labels, last.wall_s / last.video_s)
        if last.peak_gib is not None:
            last_peak.add(labels, last.peak_gib * BYTES_PER_GIB)
        if last.energy_wh is not None:
            last_energy.add(labels, last.energy_wh * JOULES_PER_WH)
        last_ts.add(labels, last.end)

    # Only with an image run: a video-only file stays as it was before #1424.
    image_runs = [r for r in every if r.media == "image"]
    image_families = render_images(image_runs) if image_runs else []

    build = gauge(
        "videogen_metrics_build_timestamp_seconds",
        "When metrics.py last wrote this file, Unix seconds.",
    )
    build.add((), now, ())

    families = [
        clips,
        frames,
        video,
        wall,
        server,
        server_clips,
        energy,
        energy_clips,
        steps,
        step_clips,
        last_fps,
        last_wall,
        last_rtf,
        last_peak,
        last_energy,
        last_ts,
        *image_families,
        build,
    ]
    return "\n".join(line for family in families for line in family.lines()) + "\n"


def render_images(runs: Sequence[Run]) -> list[Family]:
    """The image families (#1424): one sample per IMAGE_LABELS set."""
    by_status: dict[tuple[LabelSet, str], int] = {}
    oks: dict[LabelSet, list[ImageOk]] = {}
    latest: dict[LabelSet, tuple[float, str, ImageOk]] = {}
    for run in runs:
        key = (run.labels, run.status)
        by_status[key] = by_status.get(key, 0) + 1
        if run.image is None:
            continue
        oks.setdefault(run.labels, []).append(run.image)
        mark = (run.image.end, str(run.path), run.image)
        if run.labels not in latest or mark[:2] > latest[run.labels][:2]:
            latest[run.labels] = mark

    def gauge(name: str, text: str) -> Family:
        return Family(name, "gauge", text)

    images = gauge(
        "videogen_images",
        "Image-gen runs with a run.json now, by status (ok, failed). A gauge: "
        "a remade run is absent while it runs.",
    )
    for labels, status in sorted(by_status):
        images.add(
            (*labels, status), by_status[(labels, status)], (*IMAGE_LABELS, "status")
        )
    wall = gauge(
        "videogen_image_wall_seconds", "Client wall seconds summed over the ok images."
    )
    energy = gauge(
        "videogen_image_energy_joules",
        "Outlet energy in joules summed over the ok images with a complete "
        "reading. Divide by videogen_image_energy_images.",
    )
    energy_n = gauge(
        "videogen_image_energy_images",
        "The ok images with a complete outlet energy reading.",
    )
    steps = gauge(
        "videogen_image_steps",
        "Denoising steps requested, summed over the ok images that record it; "
        "divide by videogen_image_step_images.",
    )
    steps_n = gauge(
        "videogen_image_step_images", "The ok images that record num_inference_steps."
    )
    last_wall = gauge(
        "videogen_image_last_wall_seconds", "The latest ok image: wall seconds."
    )
    last_peak = gauge(
        "videogen_image_last_peak_memory_bytes",
        "The latest ok image: peak used memory of the whole node, bytes.",
    )
    last_energy = gauge(
        "videogen_image_last_energy_joules",
        "The latest ok image: outlet energy in joules, when its reading is complete.",
    )
    last_ts = gauge(
        "videogen_image_last_timestamp_seconds",
        "The latest ok image: its end time, Unix seconds.",
    )
    for labels in sorted(oks):
        group = oks[labels]
        wall.add(labels, sum(o.wall_s for o in group), IMAGE_LABELS)
        with_energy = [o.energy_wh for o in group if o.energy_wh is not None]
        energy.add(labels, sum(with_energy) * JOULES_PER_WH, IMAGE_LABELS)
        energy_n.add(labels, len(with_energy), IMAGE_LABELS)
        with_steps = [o.steps for o in group if o.steps is not None]
        if with_steps:
            steps.add(labels, sum(with_steps), IMAGE_LABELS)
        steps_n.add(labels, len(with_steps), IMAGE_LABELS)
        last = latest[labels][2]
        last_wall.add(labels, last.wall_s, IMAGE_LABELS)
        if last.peak_gib is not None:
            last_peak.add(labels, last.peak_gib * BYTES_PER_GIB, IMAGE_LABELS)
        if last.energy_wh is not None:
            last_energy.add(labels, last.energy_wh * JOULES_PER_WH, IMAGE_LABELS)
        last_ts.add(labels, last.end, IMAGE_LABELS)
    return [
        images,
        wall,
        energy,
        energy_n,
        steps,
        steps_n,
        last_wall,
        last_peak,
        last_energy,
        last_ts,
    ]


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


@contextlib.contextmanager
def locked(root: pathlib.Path, timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold an exclusive flock on `<root>/.metrics.lock`.

    Two batches (head and worker arms) export after each clip. Without the
    lock, one could scan before the other's run.json lands and rename last,
    so the file would miss a clip. Raises TimeoutError after `timeout_s`.
    """
    deadline = time.monotonic() + timeout_s
    with (root / LOCK_FILE).open("a") as fh:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"{root / LOCK_FILE} is still held after {timeout_s:g} s"
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def write(
    root: pathlib.Path = DEFAULT_ROOT,
    textfile_dir: pathlib.Path = DEFAULT_TEXTFILE_DIR,
    *,
    include_probes: bool = False,
    now: float | None = None,
    lock_timeout_s: float = LOCK_TIMEOUT_S,
) -> pathlib.Path:
    """Write `<textfile_dir>/videogen.prom` from the runs under `root`.

    Raises FileNotFoundError when the directory is missing, TimeoutError when
    another writer holds the lock too long, and OSError when it cannot write.
    """
    if not textfile_dir.is_dir():
        raise FileNotFoundError(f"no textfile collector directory {textfile_dir}")
    root = root.expanduser()
    path = textfile_dir / PROM_FILE
    with locked(root, lock_timeout_s):
        runs = find_runs(root, include_probes=include_probes)
        write_atomic(path, render(runs, now=time.time() if now is None else now))
    logger.info(
        "videogen metrics: %d runs (%d ok) -> %s",
        len(runs),
        sum(1 for r in runs if r.ok is not None or r.image is not None),
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
