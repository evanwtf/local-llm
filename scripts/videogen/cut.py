"""Cut a reference clip: strip metadata and audio, crop, re-encode to 1080p30 (#1404).

The recipe is the one the operator ran by hand on 2026-11-17, plus three
additions that make it safer to rerun:

    ffmpeg -v error -y -i file:SRC -map 0:v:0 -ss START -t DUR
        -vf "[crop=W:H:X:Y,]scale=1920:1080" -c:v libx264 -crf 14 -preset slow
        -threads 8 -pix_fmt yuv420p -r 30 -an
        -map_metadata -1 -map_metadata:s:v -1 -map_chapters -1
        -fflags +bitexact file:OUT

`-threads 8` is pinned because libx264's output depends on its thread count,
and the default count depends on the machine's cores. The three hand-made
v1 references used the default; `benchmarks/videogen/refs-v1.json` says so.

`--strip-only` drops audio, every tag and every chapter without a re-encode
(`-c copy`).

A phone clip carries its GPS position in a tag (`location`,
`com.apple.quicktime.location.ISO6709`). ffmpeg writes to `<stem>.partial<ext>`
first. This script then reads the tags and chapters back with ffprobe. Only a
clean file is renamed to the output path; the partial file is always removed.
The check never logs a tag's value: the value is the position.

Beside the output it writes `<out>.json`: the source file name, the window,
the crop, the ffmpeg version, the argv, the CPU architecture and the output
sha256. The output path must be outside every git work tree.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import platform
import re
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import (
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

#: A tag whose key or value holds one of these names a place. Lower case.
LOCATION_MARKERS = ("location", "iso6709")

#: libx264 threads for a new cut. Pinned: the output depends on it.
THREADS = 8

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


def partial_path(out: pathlib.Path) -> pathlib.Path:
    """`dir/name.partial.mp4` for `dir/name.mp4`: same directory, same muxer."""
    return out.with_name(f"{out.stem}.partial{out.suffix}")


def sidecar_path(out: pathlib.Path) -> pathlib.Path:
    """`dir/name.mp4.json` for `dir/name.mp4`."""
    return out.with_name(out.name + ".json")


def cut_argv(
    src: str,
    out: str,
    start: str,
    duration: str,
    crop: str | None = None,
    threads: int | None = THREADS,
) -> list[str]:
    """The ffmpeg argv for a cut, crop and re-encode to 1920x1080 at 30 fps.

    `src` and `out` go in as given: callers pass `file:` URLs. `threads=None`
    leaves libx264 on its default, which is how the v1 references were made.
    """
    vf = f"crop={crop},scale=1920:1080" if crop else "scale=1920:1080"
    thread_args = ["-threads", str(threads)] if threads is not None else []
    return [
        *("ffmpeg", "-v", "error", "-y"),
        *("-i", src),
        *("-map", "0:v:0"),
        *("-ss", start, "-t", duration),
        *("-vf", vf),
        *("-c:v", "libx264", "-crf", "14", "-preset", "slow"),
        *thread_args,
        *("-pix_fmt", "yuv420p", "-r", "30"),
        "-an",
        *("-map_metadata", "-1", "-map_metadata:s:v", "-1", "-map_chapters", "-1"),
        *("-fflags", "+bitexact"),
        out,
    ]


def strip_argv(src: str, out: str) -> list[str]:
    """The ffmpeg argv that drops audio, tags and chapters, with no re-encode."""
    return [
        *("ffmpeg", "-v", "error", "-y"),
        *("-i", src),
        *("-map", "0:v:0"),
        *("-map_metadata", "-1", "-map_metadata:s:v", "-1", "-map_chapters", "-1"),
        *("-fflags", "+bitexact"),
        *("-c", "copy"),
        out,
    ]


def probe(path: pathlib.Path) -> dict[str, Any]:
    """ffprobe's JSON for the container, streams and chapters of `path`."""
    out = run(
        [
            *("ffprobe", "-v", "error"),
            *("-show_format", "-show_streams", "-show_chapters"),
            *("-of", "json"),
            file_url(path),
        ]
    )
    try:
        data: dict[str, Any] = json.loads(out)
    except json.JSONDecodeError as exc:
        raise FfmpegError(f"ffprobe printed JSON that does not parse: {exc}") from exc
    return data


def leaks(info: dict[str, Any]) -> list[str]:
    """Name each location tag, and each chapter, in an ffprobe result.

    Returns `format:<key>`, `stream <n>:<key>` or `chapter <n>`, never a
    value: the value is the position, and a log line is not a safe place for
    it. A chapter is refused outright; a reference clip has no use for one,
    and a chapter's title is free text.
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
    for n, _chapter in enumerate(info.get("chapters", [])):
        found.append(f"chapter {n}")
    return found


def check_clean(path: pathlib.Path) -> list[str]:
    """Probe `path` and return the location tags and chapters it carries."""
    return leaks(probe(path))


def portable_argv(argv: list[str], names: dict[pathlib.Path, str]) -> list[str]:
    """`argv` with each path in `names` replaced by `file:<its name>`.

    The sidecar records the recipe, not the operator's directory layout.
    """
    urls = {file_url(path): f"file:{name}" for path, name in names.items()}
    return [urls.get(arg, arg) for arg in argv]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src", type=pathlib.Path, help="the source video")
    parser.add_argument(
        "out",
        type=pathlib.Path,
        help="the output clip, outside every git work tree "
        "(for example ~/bench-logs/videogen/ref/dog-a.mp4)",
    )
    parser.add_argument("--start", type=parse_time, help="start time in the source")
    parser.add_argument("--duration", type=parse_time, help="length of the cut")
    parser.add_argument("--crop", type=parse_crop, help="crop before scale: W:H:X:Y")
    parser.add_argument(
        "--strip-only",
        action="store_true",
        help="drop audio, tags and chapters; copy the video stream; no re-encode",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace an existing output"
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

    src: pathlib.Path = args.src.expanduser().resolve()
    out: pathlib.Path = args.out.expanduser().resolve()
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
    if out == src:
        logger.error("the output %s is the source; refusing to overwrite it", out)
        return 2
    if out.exists() and not args.overwrite:
        logger.error("%s exists; pass --overwrite to replace it", out)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)

    partial = partial_path(out)
    argv_ff = (
        strip_argv(file_url(src), file_url(partial))
        if args.strip_only
        else cut_argv(
            file_url(src), file_url(partial), args.start, args.duration, args.crop
        )
    )
    try:
        run(argv_ff)
        leaked = check_clean(partial)
        if leaked:
            logger.error(
                "the output still carries %s; nothing written to %s", leaked, out.name
            )
            return 1
        os.replace(partial, out)
        sidecar = {
            "source": src.name,
            "mode": "strip-only" if args.strip_only else "cut",
            "start": args.start,
            "duration": args.duration,
            "crop": args.crop,
            "threads": None if args.strip_only else THREADS,
            "ffmpeg_version": ffmpeg_version(),
            "arch": platform.machine(),
            "argv": portable_argv(argv_ff, {src: src.name, partial: out.name}),
            "output": out.name,
            "output_sha256": sha256(out),
        }
        sidecar_path(out).write_text(json.dumps(sidecar, indent=2) + "\n")
    except FfmpegError as exc:
        logger.error("%s", exc)
        return 1
    finally:
        partial.unlink(missing_ok=True)
    logger.info("wrote %s and %s (no location tags)", out, sidecar_path(out).name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
