"""scripts/videogen/cut.py: metadata strip, the location guard, argv shape (#1404).

The fake tag is the origin (0, 0), not a real place: the repository is public.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import cut

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")

FAKE_PLACE = "+00.0000+000.0000/"


@pytest.fixture
def tagged_clip(tmp_path: pathlib.Path) -> pathlib.Path:
    """A 1 s testsrc clip with audio and two location tags, like a phone clip."""
    clip = tmp_path / "tagged.mov"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-metadata",
            f"location={FAKE_PLACE}",
            "-metadata",
            f"com.apple.quicktime.location.ISO6709={FAKE_PLACE}",
            "-movflags",
            "use_metadata_tags",
            str(clip),
        ],
        check=True,
    )
    return clip


@needs_ffmpeg
def test_the_guard_sees_the_fixture_tags(tagged_clip: pathlib.Path) -> None:
    """Without this, a fixture that lost its tags makes every strip test vacuous."""
    found = cut.check_no_location(tagged_clip)
    assert "format:location" in found
    assert "format:com.apple.quicktime.location.ISO6709" in found


@needs_ffmpeg
def test_a_cut_drops_location_audio_and_resizes(
    tagged_clip: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    out = tmp_path / "cut.mp4"
    argv = [str(tagged_clip), str(out), "--start", "0.2", "--duration", "0.5"]
    assert cut.main(argv) == 0
    assert cut.check_no_location(out) == []
    info = cut.probe(out)
    assert [s["codec_type"] for s in info["streams"]] == ["video"]
    video = info["streams"][0]
    assert (video["width"], video["height"]) == (1920, 1080)
    assert video["r_frame_rate"] == "30/1"


@needs_ffmpeg
def test_a_cut_with_a_crop(tagged_clip: pathlib.Path, tmp_path: pathlib.Path) -> None:
    out = tmp_path / "crop.mp4"
    argv = [str(tagged_clip), str(out), "--start", "0", "--duration", "0.5"]
    assert cut.main([*argv, "--crop", "320:180:0:30"]) == 0
    assert cut.check_no_location(out) == []
    video = cut.probe(out)["streams"][0]
    assert (video["width"], video["height"]) == (1920, 1080)


@needs_ffmpeg
def test_strip_only_drops_location_and_audio(
    tagged_clip: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    out = tmp_path / "strip.mp4"
    assert cut.main([str(tagged_clip), str(out), "--strip-only"]) == 0
    assert cut.check_no_location(out) == []
    info = cut.probe(out)
    assert [s["codec_type"] for s in info["streams"]] == ["video"]
    # No re-encode: the source size survives.
    assert (info["streams"][0]["width"], info["streams"][0]["height"]) == (320, 240)


@needs_ffmpeg
def test_the_guard_fails_the_run_and_deletes_a_leaky_output(
    tagged_clip: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ffmpeg ever kept a location tag, the run fails and leaves no file."""

    def keep_everything(src: pathlib.Path, out: pathlib.Path) -> list[str]:
        return ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c", "copy", str(out)]

    monkeypatch.setattr(cut, "strip_argv", keep_everything)
    out = tmp_path / "leaky.mov"
    assert cut.main([str(tagged_clip), str(out), "--strip-only"]) == 1
    assert not out.exists()


def test_location_tags_reads_keys_and_values_and_never_returns_a_value() -> None:
    info = {
        "format": {
            "tags": {"encoder": "Lavf", "com.apple.quicktime.location.ISO6709": "x"}
        },
        "streams": [
            {"index": 0, "tags": {"handler_name": "VideoHandler"}},
            {"index": 1, "tags": {"comment": f"Location {FAKE_PLACE}"}},
        ],
    }
    found = cut.location_tags(info)
    assert found == ["format:com.apple.quicktime.location.ISO6709", "stream 1:comment"]
    assert not any(FAKE_PLACE in item for item in found)


def test_location_tags_passes_a_clean_probe() -> None:
    info = {"format": {"tags": {"major_brand": "isom"}}, "streams": [{"index": 0}]}
    assert cut.location_tags(info) == []


def test_cut_argv_is_the_hand_recipe() -> None:
    src, out = pathlib.Path("in.mov"), pathlib.Path("out.mp4")
    argv = cut.cut_argv(src, out, "3", "8", "1080:608:0:656")
    assert argv == [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        "in.mov",
        "-map",
        "0:v:0",
        "-ss",
        "3",
        "-t",
        "8",
        "-vf",
        "crop=1080:608:0:656,scale=1920:1080",
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
        "out.mp4",
    ]
    assert "scale=1920:1080" in cut.cut_argv(src, out, "0", "1")


def test_a_cut_needs_start_and_duration(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit):
        cut.main([str(tmp_path / "a.mov"), str(tmp_path / "b.mp4"), "--start", "1"])


def test_a_bad_crop_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit):
        cut.main(
            [
                str(tmp_path / "a.mov"),
                str(tmp_path / "b.mp4"),
                "--start",
                "0",
                "--duration",
                "1",
                "--crop",
                "1080x608",
            ]
        )


def test_cut_refuses_an_output_inside_the_repo(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "a.mov"
    src.write_bytes(b"")
    target = ROOT / "clip-must-not-exist.mp4"
    assert cut.main([str(src), str(target), "--strip-only"]) == 2
    assert not target.exists()
