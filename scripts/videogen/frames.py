"""Extract a reference clip's frames, first frame, busiest-2 s strip and meta.json (#1404).

Given a clip (the output of `cut.py`), it writes into one directory:

- `s01.jpg`, `s02.jpg`, ...: one frame a second, 1280 px wide;
- `first.png`: the first frame, at full size (the image-to-video input);
- `strip.jpg`: the busiest 2 s at 4 fps, 640 px wide, tiled 4x2, each tile
  stamped with its time;
- `meta.json`: the clip's duration, the frame counts, where the busiest window
  starts, and the ffmpeg version that made them.

The busiest window is the 2 s window (60 frames at 30 fps) with the largest
sum of per-frame luma change, read from ffmpeg `signalstats` YDIF on a 480 px
copy. `busiest_window` is the pick, pure, so a test can pin it.

The default output is `~/bench-logs/videogen/frames/<clip stem>/`, outside the
repository. The repository is public and the clips are private, so the script
refuses an output directory inside it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import pathlib
import re
import sys
import tempfile
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import (
    DEFAULT_ROOT,
    FfmpegError,
    InsideRepo,
    refuse_inside_repo,
    require,
    run,
)

import logs

logger = logging.getLogger(__name__)

#: Length of the busiest window, in seconds, and the strip's sample rate.
WINDOW_S = 2.0
STRIP_FPS = 4

#: The 1 fps stills. Only names that match this are ours to replace.
STILL = re.compile(r"^s\d+\.jpg$")

#: Every file this script writes besides the stills.
OWNED = ("first.png", "strip.jpg", "meta.json")

#: drawtext: the frame's own time, yellow on a 60% black box, top left. The
#: `\:` escapes the colon inside the filter graph; no shell is involved.
DRAWTEXT = (
    "drawtext=text='%{pts\\:hms}':x=8:y=8:fontsize=16:fontcolor=yellow"
    ":box=1:boxcolor=black@0.6"
)


def busiest_window(values: list[float], window: int) -> int:
    """Return the start index of the `window`-long run with the largest sum.

    Ties go to the earliest index. With fewer values than `window`, return 0.
    Each sum is `math.fsum` of its own slice, so two windows that hold the
    same values sum to the same float and a tie is a real tie.
    """
    if window <= 0:
        raise ValueError(f"window must be positive, got {window}")
    if len(values) < window:
        return 0
    best_index = 0
    best_sum = math.fsum(values[0:window])
    for start in range(1, len(values) - window + 1):
        total = math.fsum(values[start : start + window])
        if total > best_sum:
            best_index, best_sum = start, total
    return best_index


def parse_ydif(text: str) -> list[tuple[float, float]]:
    """Parse ffmpeg `metadata=print` output into (pts_time, YDIF) per frame.

    The file holds a `frame:N pts:P pts_time:T` line, then a
    `lavfi.signalstats.YDIF=V` line, for each frame.
    """
    rows: list[tuple[float, float]] = []
    pts_time: float | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("frame:"):
            match = re.search(r"pts_time:(\S+)", line)
            pts_time = float(match.group(1)) if match else None
        elif line.startswith("lavfi.signalstats.YDIF="):
            if pts_time is None:
                raise ValueError(f"YDIF line with no frame header: {line!r}")
            rows.append((pts_time, float(line.split("=", 1)[1])))
            pts_time = None
    return rows


def ydif_series(clip: pathlib.Path) -> list[tuple[float, float]]:
    """Run signalstats on a 480 px copy of `clip`; return (pts_time, YDIF)."""
    with tempfile.TemporaryDirectory() as tmp:
        # A relative file= keeps the path out of the filter graph, where a
        # colon or comma in a directory name would need escaping.
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(clip.resolve()),
                "-vf",
                (
                    "scale=480:-2,signalstats,"
                    "metadata=print:key=lavfi.signalstats.YDIF:file=ydif.txt"
                ),
                "-f",
                "null",
                "-",
            ],
            cwd=pathlib.Path(tmp),
        )
        return parse_ydif((pathlib.Path(tmp) / "ydif.txt").read_text())


def probe_clip(clip: pathlib.Path) -> tuple[float, float]:
    """Return the clip's (duration in seconds, frame rate)."""
    out = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration:stream=r_frame_rate",
            "-of",
            "json",
            str(clip),
        ]
    )
    data: dict[str, Any] = json.loads(out)
    num, _, den = data["streams"][0]["r_frame_rate"].partition("/")
    fps = float(num) / float(den or 1)
    return float(data["format"]["duration"]), fps


def ffmpeg_version() -> str:
    """The version token from `ffmpeg -version`, e.g. `6.1.1-3ubuntu5`."""
    first = run(["ffmpeg", "-version"]).splitlines()[0]
    parts = first.split()
    return parts[2] if len(parts) > 2 else first


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def existing_outputs(out: pathlib.Path) -> list[pathlib.Path]:
    """The files in `out` that this script would write or replace."""
    if not out.is_dir():
        return []
    return sorted(p for p in out.iterdir() if STILL.match(p.name) or p.name in OWNED)


def extract(clip: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    """Write the stills, first frame, strip and meta.json; return the meta."""
    out.mkdir(parents=True, exist_ok=True)
    src = str(clip)

    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            src,
            "-vf",
            "fps=1,scale=1280:-2",
            "-q:v",
            "3",
            str(out / "s%02d.jpg"),
        ]
    )
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            src,
            "-frames:v",
            "1",
            str(out / "first.png"),
        ]
    )

    duration, fps = probe_clip(clip)
    series = ydif_series(clip)
    window = max(1, round(WINDOW_S * fps))
    start_index = busiest_window([v for _, v in series], window)
    start_s = series[start_index][0] if series else 0.0

    # trim keeps each frame's own timestamp, so the burned-in time is the
    # time in the clip, not the time since the cut began.
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            src,
            "-vf",
            (
                f"trim=start={start_s:.6f}:duration={WINDOW_S},"
                f"fps={STRIP_FPS},scale=640:-2,{DRAWTEXT},tile=4x2"
            ),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(out / "strip.jpg"),
        ]
    )

    stills = sorted(p.name for p in out.iterdir() if STILL.match(p.name))
    meta: dict[str, Any] = {
        "clip": clip.name,
        "clip_sha256": sha256(clip),
        "duration_s": duration,
        "fps": fps,
        "frame_count": len(stills),
        "video_frames": len(series),
        "window_frames": window,
        "busiest_start_frame": start_index,
        "busiest_start_s": start_s,
        "ffmpeg_version": ffmpeg_version(),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clip", type=pathlib.Path, help="the reference clip")
    parser.add_argument(
        "out",
        type=pathlib.Path,
        nargs="?",
        help=f"output directory (default: {DEFAULT_ROOT}/frames/<clip stem>/)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace the outputs of an earlier run in the same directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    args = build_parser().parse_args(argv)
    clip: pathlib.Path = args.clip.expanduser()
    out: pathlib.Path = (
        args.out.expanduser() if args.out else DEFAULT_ROOT / "frames" / clip.stem
    )
    try:
        refuse_inside_repo(out)
        require("ffmpeg")
        require("ffprobe")
    except (InsideRepo, FfmpegError) as exc:
        logger.error("%s", exc)
        return 2
    if not clip.is_file():
        logger.error("clip %s does not exist", clip)
        return 2

    # A rerun into the same directory from a shorter clip would leave the old
    # run's last stills beside the new ones. Refuse, or clear them on request.
    stale = existing_outputs(out)
    if stale and not args.overwrite:
        logger.error(
            "%s already holds %d output file(s) from an earlier run; "
            "pass --overwrite to replace them",
            out,
            len(stale),
        )
        return 2
    for path in stale:
        path.unlink()

    try:
        meta = extract(clip, out)
    except FfmpegError as exc:
        logger.error("%s", exc)
        return 1
    logger.info(
        "wrote %d stills, first.png, strip.jpg (busiest 2 s from %.3f s) "
        "and meta.json to %s",
        meta["frame_count"],
        meta["busiest_start_s"],
        out,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
