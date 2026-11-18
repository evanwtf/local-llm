"""Cut a reference clip: strip metadata and audio, crop, re-encode to 1080p30 (#1404).

The recipe is the one the operator ran by hand on 2026-11-17, kept here so a
rerun gives the same clip:

    ffmpeg -v error -y -i SRC -map 0:v:0 -ss START -t DUR
        -vf "[crop=W:H:X:Y,]scale=1920:1080" -c:v libx264 -crf 14 -preset slow
        -pix_fmt yuv420p -r 30 -an -map_metadata -1 -fflags +bitexact OUT

`--strip-only` drops audio and every tag without a re-encode (`-c copy`).

A phone clip carries its GPS position in a tag (`location`,
`com.apple.quicktime.location.ISO6709`). After it writes the output, this
script reads the tags back with ffprobe. If any tag still names a location, it
deletes the output and exits nonzero. The check never logs a tag's value: the
value is the position.

The output path must be outside the repository (the repository is public).
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import FfmpegError, InsideRepo, refuse_inside_repo, require, run

import logs

logger = logging.getLogger(__name__)

#: A tag whose key or value holds one of these names a place. Lower case.
LOCATION_MARKERS = ("location", "iso6709")

#: ffmpeg time syntax: seconds (`3.5`) or `[HH:]MM:SS[.frac]`.
TIME = re.compile(r"^(\d+:){0,2}\d+(\.\d+)?$")

#: `W:H:X:Y`, four non-negative integers.
CROP = re.compile(r"^\d+:\d+:\d+:\d+$")


def parse_time(value: str) -> str:
    """Return `value` unchanged if ffmpeg accepts it as a time, else raise."""
    if not TIME.match(value):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a time: use seconds (3.5) or [HH:]MM:SS[.frac]"
        )
    return value


def parse_crop(value: str) -> str:
    """Return `value` unchanged if it is `W:H:X:Y`, else raise."""
    if not CROP.match(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a crop: use W:H:X:Y")
    return value


def cut_argv(
    src: pathlib.Path,
    out: pathlib.Path,
    start: str,
    duration: str,
    crop: str | None = None,
) -> list[str]:
    """The ffmpeg argv for a cut, crop and re-encode to 1920x1080 at 30 fps."""
    vf = f"crop={crop},scale=1920:1080" if crop else "scale=1920:1080"
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        "-map",
        "0:v:0",
        "-ss",
        start,
        "-t",
        duration,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-crf",
        "14",
        "-preset",
        "slow",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-an",
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        str(out),
    ]


def strip_argv(src: pathlib.Path, out: pathlib.Path) -> list[str]:
    """The ffmpeg argv that drops audio and every tag, with no re-encode."""
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        "-map",
        "0:v:0",
        "-map_metadata",
        "-1",
        "-map_metadata:s:v",
        "-1",
        "-fflags",
        "+bitexact",
        "-c",
        "copy",
        str(out),
    ]


def probe(path: pathlib.Path) -> dict[str, Any]:
    """ffprobe's JSON for the container and every stream of `path`."""
    out = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ]
    )
    data: dict[str, Any] = json.loads(out)
    return data


def location_tags(info: dict[str, Any]) -> list[str]:
    """Name each tag in an ffprobe result whose key or value names a location.

    Returns `format:<key>` or `stream <n>:<key>`, never the value: the value
    is the position, and a log line is not a safe place for it.
    """
    found: list[str] = []
    places: list[tuple[str, dict[str, Any]]] = [
        ("format", info.get("format", {}).get("tags", {}))
    ]
    for stream in info.get("streams", []):
        places.append((f"stream {stream.get('index', '?')}", stream.get("tags", {})))
    for where, tags in places:
        for key, value in tags.items():
            text = f"{key} {value}".lower()
            if any(marker in text for marker in LOCATION_MARKERS):
                found.append(f"{where}:{key}")
    return found


def check_no_location(path: pathlib.Path) -> list[str]:
    """Probe `path` and return the location tags it still carries."""
    return location_tags(probe(path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src", type=pathlib.Path, help="the source video")
    parser.add_argument(
        "out",
        type=pathlib.Path,
        help="the output clip, outside the repository "
        "(for example ~/bench-logs/videogen/clips/dog-a.mp4)",
    )
    parser.add_argument("--start", type=parse_time, help="start time in the source")
    parser.add_argument("--duration", type=parse_time, help="length of the cut")
    parser.add_argument("--crop", type=parse_crop, help="crop before scale: W:H:X:Y")
    parser.add_argument(
        "--strip-only",
        action="store_true",
        help="drop audio and every tag, copy the video stream, no re-encode",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.strip_only:
        if args.start or args.duration or args.crop:
            parser.error("--strip-only takes no --start, --duration or --crop")
    elif not (args.start and args.duration):
        parser.error("a cut needs --start and --duration (or use --strip-only)")

    src: pathlib.Path = args.src.expanduser()
    out: pathlib.Path = args.out.expanduser()
    try:
        refuse_inside_repo(out)
        require("ffmpeg")
        require("ffprobe")
    except (InsideRepo, FfmpegError) as exc:
        logger.error("%s", exc)
        return 2
    if not src.is_file():
        logger.error("source %s does not exist", src)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)

    argv_ff = (
        strip_argv(src, out)
        if args.strip_only
        else cut_argv(src, out, args.start, args.duration, args.crop)
    )
    try:
        run(argv_ff)
        leaked = check_no_location(out)
    except FfmpegError as exc:
        logger.error("%s", exc)
        return 1
    if leaked:
        out.unlink(missing_ok=True)
        logger.error(
            "output still carries location tags %s; deleted %s", leaked, out.name
        )
        return 1
    logger.info("wrote %s (no location tags)", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
