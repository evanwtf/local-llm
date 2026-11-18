"""scripts/videogen/frames.py: the busiest-window pick, the YDIF parser, a smoke run (#1404)."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import common, frames

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")


def test_one_clear_peak_is_found() -> None:
    values = [0.0] * 20
    values[12] = 50.0
    # Windows of 4 that hold index 12 start at 9..12; the earliest wins.
    assert frames.busiest_window(values, 4) == 9


def test_the_peak_window_beats_a_wider_low_plateau() -> None:
    values = [1.0] * 10 + [0.0] * 5 + [9.0, 9.0, 9.0] + [0.0] * 5
    assert frames.busiest_window(values, 3) == 15


def test_a_tie_goes_to_the_earliest_index() -> None:
    values = [0.0, 5.0, 5.0, 0.0, 0.0, 5.0, 5.0, 0.0]
    assert frames.busiest_window(values, 2) == 1


def test_a_tie_with_float_values_in_another_order_is_still_a_tie() -> None:
    # Left to right, 0.3 + 0.2 + 0.1 is 0.6 but 0.1 + 0.2 + 0.3 is
    # 0.6000000000000001, so a naive sum would pick index 4.
    values = [0.3, 0.2, 0.1, 0.0, 0.1, 0.2, 0.3]
    assert frames.busiest_window(values, 3) == 0


def test_fewer_values_than_the_window_returns_zero() -> None:
    assert frames.busiest_window([3.0, 9.0, 1.0], 60) == 0
    assert frames.busiest_window([], 60) == 0


def test_a_window_equal_to_the_length_returns_zero() -> None:
    assert frames.busiest_window([1.0, 9.0, 3.0, 7.0], 4) == 0


def test_a_window_of_zero_is_refused() -> None:
    with pytest.raises(ValueError):
        frames.busiest_window([1.0], 0)


def test_parse_ydif_pairs_each_value_with_its_time() -> None:
    text = (
        "frame:0    pts:0       pts_time:0\n"
        "lavfi.signalstats.YDIF=0.000000\n"
        "frame:1    pts:512     pts_time:0.0333333\n"
        "lavfi.signalstats.YDIF=4.250000\n"
    )
    assert frames.parse_ydif(text) == [(0.0, 0.0), (0.0333333, 4.25)]


def test_parse_ydif_refuses_a_value_with_no_frame() -> None:
    with pytest.raises(ValueError):
        frames.parse_ydif("lavfi.signalstats.YDIF=1.0\n")


def make_clip(path: pathlib.Path, *lavfi: str, lossless: bool = False) -> None:
    """Encode a synthetic clip from a lavfi graph."""
    quality = ["-qp", "0"] if lossless else ["-preset", "ultrafast"]
    subprocess.run(
        [
            *("ffmpeg", "-v", "error", "-y"),
            *lavfi,
            *("-c:v", "libx264", *quality, "-pix_fmt", "yuv420p"),
            str(path),
        ],
        check=True,
    )


def test_the_strip_starts_one_frame_before_the_window() -> None:
    """YDIF[k] is the change from frame k-1 into frame k."""
    assert frames.strip_start(0) == 0
    assert frames.strip_start(1) == 0
    assert frames.strip_start(47) == 46


def test_the_strip_filter_is_frame_exact() -> None:
    font = pathlib.Path("/fonts/DejaVuSans.ttf")
    vf = frames.strip_filter(46, 60, font)
    assert vf.startswith("trim=start_frame=46:end_frame=106,fps=4,scale=640:-2,")
    assert "fontfile=/fonts/DejaVuSans.ttf" in vf
    assert vf.endswith(",tile=4x2")
    assert not frames.strip_filter(0, 60, font, tile=False).endswith("tile=4x2")


def test_a_font_path_with_a_filter_special_character_is_refused() -> None:
    with pytest.raises(common.FfmpegError):
        frames.drawtext(pathlib.Path("/odd:dir/DejaVuSans.ttf"))


@needs_ffmpeg
def test_frames_writes_every_output(tmp_path: pathlib.Path) -> None:
    clip = tmp_path / "clip.mp4"
    make_clip(clip, "-f", "lavfi", "-i", "testsrc2=duration=3:size=320x180:rate=30")
    out = tmp_path / "frames"
    assert frames.main([str(clip), str(out)]) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "first.png",
        "meta.json",
        "s01.jpg",
        "s02.jpg",
        "s03.jpg",
        "strip.jpg",
    ]
    meta = json.loads((out / "meta.json").read_text())
    assert meta["frame_count"] == 3
    assert meta["video_frames"] == 90
    assert meta["window_frames"] == 60
    assert meta["fps"] == 30.0
    assert meta["duration_s"] == pytest.approx(3.0, abs=0.05)
    assert meta["params"] == frames.PARAMS
    assert meta["font"] == "DejaVuSans.ttf"
    assert meta["ffmpeg_version"] and meta["arch"]
    assert sorted(meta["outputs_sha256"]) == [
        "first.png",
        "s01.jpg",
        "s02.jpg",
        "s03.jpg",
        "strip.jpg",
    ]
    for name, digest in meta["outputs_sha256"].items():
        assert common.sha256(out / name) == digest

    # A second run into the same directory is refused unless asked for.
    assert frames.main([str(clip), str(out)]) == 2
    assert frames.main([str(clip), str(out), "--overwrite"]) == 0


@needs_ffmpeg
def test_a_known_motion_burst_is_picked_tiled_and_reproduced(
    tmp_path: pathlib.Path,
) -> None:
    """Flat gray for 8 s, with moving testsrc2 on frames 90..105 only.

    The clip is lossless, so every static frame decodes the same and its YDIF
    is exactly 0. Nonzero YDIF sits at 90 (gray into testsrc2) through 106
    (testsrc2 back into gray). Every 60-frame window from 47 to 90 holds all
    of it, so the sums tie and the earliest, 47, wins. The strip starts one
    frame earlier, at 46.
    """
    clip = tmp_path / "burst.mp4"
    make_clip(
        clip,
        *("-f", "lavfi", "-i", "color=c=gray:size=320x180:rate=30:duration=8"),
        *("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=8"),
        *("-filter_complex", "[0][1]overlay=enable='between(n,90,105)'"),
        lossless=True,
    )
    meta = frames.extract(clip, tmp_path / "a")
    assert meta["video_frames"] == 240
    assert meta["busiest_window_index"] == 47
    assert meta["busiest_start_frame"] == 46
    assert meta["busiest_start_s"] == pytest.approx(46 / 30, abs=1e-4)

    # Eight frames go into the strip, so the 4x2 tile holds no blank cell.
    font = frames.find_font()
    crc = subprocess.run(
        [
            *("ffmpeg", "-v", "error", "-i", common.file_url(clip)),
            *("-vf", frames.strip_filter(46, 60, font, tile=False)),
            *("-f", "framecrc", "-"),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert len([ln for ln in crc.splitlines() if not ln.startswith("#")]) == 8
    size = subprocess.run(
        [
            *("ffprobe", "-v", "error", "-show_entries", "stream=width,height"),
            *("-of", "csv=p=0", common.file_url(tmp_path / "a" / "strip.jpg")),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert size == "2560,720"

    # A rerun gives the same bytes.
    again = frames.extract(clip, tmp_path / "b")
    assert again["outputs_sha256"] == meta["outputs_sha256"]


@needs_ffmpeg
def test_a_clip_shorter_than_the_window_fails_and_writes_nothing(
    tmp_path: pathlib.Path,
) -> None:
    clip = tmp_path / "short.mp4"
    make_clip(clip, "-f", "lavfi", "-i", "testsrc2=duration=1:size=320x180:rate=30")
    out = tmp_path / "frames"
    assert frames.main([str(clip), str(out)]) == 1
    assert not out.exists()


def test_frames_refuses_an_output_inside_the_repo(tmp_path: pathlib.Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"")
    target = ROOT / "benchmarks" / "videogen" / "frames-must-not-exist"
    assert frames.main([str(clip), str(target)]) == 2
    assert not target.exists()
