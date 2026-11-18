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

from videogen import frames

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


@needs_ffmpeg
def test_frames_writes_every_output(tmp_path: pathlib.Path) -> None:
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=3:size=320x180:rate=30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
    )
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
    assert meta["duration_s"] == pytest.approx(3.0, abs=0.05)
    assert 0.0 <= meta["busiest_start_s"] <= 1.0 + 1e-6
    assert meta["ffmpeg_version"]

    # A second run into the same directory is refused unless asked for.
    assert frames.main([str(clip), str(out)]) == 2
    assert frames.main([str(clip), str(out), "--overwrite"]) == 0


def test_frames_refuses_an_output_inside_the_repo(tmp_path: pathlib.Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"")
    target = ROOT / "benchmarks" / "videogen" / "frames-must-not-exist"
    assert frames.main([str(clip), str(target)]) == 2
    assert not target.exists()
