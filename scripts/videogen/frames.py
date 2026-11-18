"""Extract a reference clip's frames, first frame, busiest-2 s strip and meta.json (#1404).

Given a clip (the output of `cut.py`), it writes into one directory:

- `s01.jpg`, `s02.jpg`, ...: one frame a second, 1280 px wide;
- `first.png`: the first frame, at full size (the image-to-video input);
- `strip.jpg`: the busiest 2 s at 4 fps, 640 px wide, tiled 4x2, each tile
  stamped with its time in the clip;
- `meta.json`: the clip's duration and frame counts, where the busiest window
  starts, the parameters below, the ffmpeg version, the CPU architecture, the
  font, and the sha256 of every output.

**The busiest window.** ffmpeg `signalstats` YDIF on a 480 px copy gives one
value per frame. YDIF[k] is the mean luma change from frame k-1 INTO frame k
(YDIF[0] is 0). `busiest_window` picks the run of `window` values (2 s, so 60
at 30 fps) with the largest sum; call its start k. That run measures the
changes into frames k .. k+window-1, so the motion it scores starts at frame
k-1. The strip therefore starts at frame k-1 (or 0 when k is 0) and takes
`window` frames, cut with `trim=start_frame:end_frame` so the cut is exact to
the frame, however long the clip.

JPEG and PNG outputs carry `-fflags +bitexact -flags:v +bitexact`, and the
strip's text uses an explicit DejaVu Sans font file, so a rerun with the same
ffmpeg gives the same bytes.

The default output is `~/bench-logs/videogen/frames/<clip stem>/`. The clips
are private, so the script refuses an output directory inside any git work
tree.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import (
    DEFAULT_ROOT,
    FfmpegError,
    InsideRepo,
    ffmpeg_version,
    file_url,
    refuse_inside_repo,
    require,
    run,
    sha256,
)

import logs

logger = logging.getLogger(__name__)

#: Every knob that shapes an output. meta.json records this dict as is.
PARAMS: dict[str, Any] = {
    "stills_fps": 1,
    "stills_width": 1280,
    "jpeg_q": 3,
    "ydif_width": 480,
    "window_s": 2.0,
    "strip_fps": 4,
    "strip_width": 640,
    "strip_tile": "4x2",
    "font_size": 16,
    "font_color": "yellow",
    "box_color": "black@0.6",
}

#: The 1 fps stills. Only names that match this are ours to replace.
STILL = re.compile(r"^s\d+\.jpg$")

#: Every file this script writes besides the stills.
OWNED = ("first.png", "strip.jpg", "meta.json")

#: Where DejaVu Sans lives on Debian/Ubuntu, Fedora, Homebrew and MacPorts.
FONT_NAME = "DejaVuSans.ttf"
FONT_CANDIDATES = (
    pathlib.Path("/usr/share/fonts/truetype/dejavu") / FONT_NAME,
    pathlib.Path("/usr/share/fonts/dejavu-sans-fonts") / FONT_NAME,
    pathlib.Path("/usr/share/fonts/dejavu") / FONT_NAME,
    pathlib.Path("/opt/homebrew/share/fonts") / FONT_NAME,
    pathlib.Path("/usr/local/share/fonts") / FONT_NAME,
    pathlib.Path("/opt/local/share/fonts/dejavu-fonts") / FONT_NAME,
    pathlib.Path.home() / "Library" / "Fonts" / FONT_NAME,
)

#: Characters that would need escaping inside a filter-graph option value.
FILTER_SPECIAL = re.compile(r"[\\':,;\[\]=]")

#: Bitexact flags for an image output: no encoder string, no build detail.
BITEXACT = ["-fflags", "+bitexact", "-flags:v", "+bitexact"]


class WindowTooLong(Exception):
    """The clip has fewer frames than the busiest window."""


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


def strip_start(window_index: int) -> int:
    """The first frame of the strip for a busiest window that starts at `k`.

    YDIF[k] is the change from frame k-1 into frame k, so the motion the
    window scores begins at frame k-1. At k = 0 there is no frame before.
    """
    return max(window_index - 1, 0)


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


def find_font() -> pathlib.Path:
    """The DejaVu Sans font file. Raise FfmpegError with the fix if absent."""
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return candidate
    fc_match = shutil.which("fc-match")
    if fc_match:
        result = subprocess.run(
            [fc_match, "-f", "%{file}", "DejaVu Sans"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        found = pathlib.Path(result.stdout.strip())
        if result.returncode == 0 and found.name == FONT_NAME and found.is_file():
            return found
    raise FfmpegError(
        f"{FONT_NAME} not found. Install DejaVu Sans (Debian/Ubuntu: "
        "fonts-dejavu-core; macOS: brew install --cask font-dejavu)."
    )


def drawtext(font: pathlib.Path) -> str:
    """The drawtext filter: the frame's own time, top left, on a box.

    The `\\:` escapes the colon inside the filter graph; no shell is involved.
    """
    if FILTER_SPECIAL.search(str(font)):
        raise FfmpegError(f"font path {font} holds a filter-graph special character")
    return (
        f"drawtext=fontfile={font}:text='%{{pts\\:hms}}':x=8:y=8"
        f":fontsize={PARAMS['font_size']}:fontcolor={PARAMS['font_color']}"
        f":box=1:boxcolor={PARAMS['box_color']}"
    )


def strip_filter(
    start_frame: int, window: int, font: pathlib.Path, *, tile: bool = True
) -> str:
    """The strip's filter graph. `tile=False` lets a test count the frames."""
    parts = [
        f"trim=start_frame={start_frame}:end_frame={start_frame + window}",
        f"fps={PARAMS['strip_fps']}",
        f"scale={PARAMS['strip_width']}:-2",
        drawtext(font),
    ]
    if tile:
        parts.append(f"tile={PARAMS['strip_tile']}")
    return ",".join(parts)


def ydif_series(clip: pathlib.Path) -> list[tuple[float, float]]:
    """Run signalstats on a 480 px copy of `clip`; return (pts_time, YDIF)."""
    with tempfile.TemporaryDirectory() as tmp:
        # A relative file= keeps the path out of the filter graph, where a
        # colon or comma in a directory name would need escaping.
        run(
            [
                *("ffmpeg", "-v", "error"),
                *("-i", file_url(clip)),
                "-vf",
                (
                    f"scale={PARAMS['ydif_width']}:-2,signalstats,"
                    "metadata=print:key=lavfi.signalstats.YDIF:file=ydif.txt"
                ),
                *("-f", "null", "-"),
            ],
            cwd=pathlib.Path(tmp),
        )
        return parse_ydif((pathlib.Path(tmp) / "ydif.txt").read_text())


def probe_clip(clip: pathlib.Path) -> tuple[float, float]:
    """Return the clip's (duration in seconds, average frame rate)."""
    out = run(
        [
            *("ffprobe", "-v", "error", "-select_streams", "v:0"),
            *("-show_entries", "format=duration:stream=avg_frame_rate"),
            *("-of", "json"),
            file_url(clip),
        ]
    )
    try:
        data: dict[str, Any] = json.loads(out)
        num, _, den = data["streams"][0]["avg_frame_rate"].partition("/")
        fps = float(num) / float(den or 1)
        duration = float(data["format"]["duration"])
    except (
        json.JSONDecodeError,
        KeyError,
        IndexError,
        ValueError,
        ZeroDivisionError,
    ) as exc:
        raise FfmpegError(
            f"ffprobe gave no usable frame rate or duration: {exc}"
        ) from exc
    return duration, fps


def existing_outputs(out: pathlib.Path) -> list[pathlib.Path]:
    """The files in `out` that this script would write or replace."""
    if not out.is_dir():
        return []
    return sorted(p for p in out.iterdir() if STILL.match(p.name) or p.name in OWNED)


def extract(clip: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    """Write the stills, first frame, strip and meta.json; return the meta."""
    font = find_font()
    duration, fps = probe_clip(clip)
    series = ydif_series(clip)
    window = max(1, round(float(PARAMS["window_s"]) * fps))
    if len(series) < window:
        raise WindowTooLong(
            f"{clip.name} has {len(series)} frames, fewer than the "
            f"{window}-frame window ({PARAMS['window_s']} s at {fps:g} fps)"
        )
    window_index = busiest_window([v for _, v in series], window)
    start_frame = strip_start(window_index)

    out.mkdir(parents=True, exist_ok=True)
    src = file_url(clip)
    run(
        [
            *("ffmpeg", "-v", "error", "-y", "-i", src),
            "-vf",
            f"fps={PARAMS['stills_fps']},scale={PARAMS['stills_width']}:-2",
            *("-q:v", str(PARAMS["jpeg_q"])),
            *BITEXACT,
            file_url(out / "s%02d.jpg"),
        ]
    )
    run(
        [
            *("ffmpeg", "-v", "error", "-y", "-i", src),
            *("-frames:v", "1"),
            *BITEXACT,
            file_url(out / "first.png"),
        ]
    )
    run(
        [
            *("ffmpeg", "-v", "error", "-y", "-i", src),
            *("-vf", strip_filter(start_frame, window, font)),
            *("-frames:v", "1", "-q:v", str(PARAMS["jpeg_q"])),
            *BITEXACT,
            file_url(out / "strip.jpg"),
        ]
    )

    stills = sorted(p.name for p in out.iterdir() if STILL.match(p.name))
    outputs = [*stills, "first.png", "strip.jpg"]
    meta: dict[str, Any] = {
        "clip": clip.name,
        "clip_sha256": sha256(clip),
        "duration_s": duration,
        "fps": fps,
        "frame_count": len(stills),
        "video_frames": len(series),
        "window_frames": window,
        "busiest_window_index": window_index,
        "busiest_start_frame": start_frame,
        "busiest_start_s": series[start_frame][0],
        "params": PARAMS,
        "font": FONT_NAME,
        "font_sha256": sha256(font),
        "ffmpeg_version": ffmpeg_version(),
        "arch": platform.machine(),
        "outputs_sha256": {name: sha256(out / name) for name in outputs},
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
    clip: pathlib.Path = args.clip.expanduser().resolve()
    out: pathlib.Path = (
        args.out.expanduser() if args.out else DEFAULT_ROOT / "frames" / clip.stem
    ).resolve()
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
    except WindowTooLong as exc:
        logger.warning("%s", exc)
        logger.error("no busiest window to pick; nothing written")
        return 1
    except FfmpegError as exc:
        logger.error("%s", exc)
        return 1
    logger.info(
        "wrote %d stills, first.png, strip.jpg (busiest 2 s from frame %d, "
        "%.3f s) and meta.json to %s",
        meta["frame_count"],
        meta["busiest_start_frame"],
        meta["busiest_start_s"],
        out,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
