"""scripts/videogen/cut.py: metadata strip, the leak guard, the sidecar, refusals (#1404).

The fake tag is the origin (0, 0), not a real place: the repository is public.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import common, cut

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")

FAKE_PLACE = "+00.0000+000.0000/"

CHAPTERS = """;FFMETADATA1
[CHAPTER]
TIMEBASE=1/1000
START=0
END=500
title=a chapter title is free text
"""


@pytest.fixture
def tagged_clip(tmp_path: pathlib.Path) -> pathlib.Path:
    """A 1 s testsrc clip with audio, two location tags and a chapter."""
    chapters = tmp_path / "chapters.txt"
    chapters.write_text(CHAPTERS)
    clip = tmp_path / "tagged.mov"
    subprocess.run(
        [
            *("ffmpeg", "-v", "error", "-y"),
            *("-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30"),
            *("-f", "lavfi", "-i", "sine=duration=1"),
            *("-i", str(chapters)),
            *("-map", "0:v", "-map", "1:a", "-map_chapters", "2"),
            *("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"),
            *("-c:a", "aac"),
            *("-metadata", f"location={FAKE_PLACE}"),
            *("-metadata", f"com.apple.quicktime.location.ISO6709={FAKE_PLACE}"),
            *("-movflags", "use_metadata_tags"),
            str(clip),
        ],
        check=True,
    )
    return clip


@needs_ffmpeg
def test_the_guard_sees_the_fixture_tags_and_chapter(tagged_clip: pathlib.Path) -> None:
    """Without this, a fixture that lost its tags makes every strip test vacuous."""
    found = cut.check_clean(tagged_clip)
    assert "format:location" in found
    assert "format:com.apple.quicktime.location.ISO6709" in found
    assert "chapter 0" in found


def run_cut(*args: str) -> int:
    return cut.main(list(args))


@needs_ffmpeg
def test_a_cut_drops_location_audio_chapters_and_resizes(
    tagged_clip: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    out = tmp_path / "cut.mp4"
    assert (
        run_cut(str(tagged_clip), str(out), "--start", "0.2", "--duration", "0.5") == 0
    )
    assert cut.check_clean(out) == []
    info = cut.probe(out)
    assert [s["codec_type"] for s in info["streams"]] == ["video"]
    video = info["streams"][0]
    assert (video["width"], video["height"]) == (1920, 1080)
    assert video["r_frame_rate"] == "30/1"
    assert not cut.partial_path(out).exists()


@needs_ffmpeg
def test_a_cut_writes_a_portable_sidecar(
    tagged_clip: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    out = tmp_path / "clips" / "crop.mp4"
    argv = [str(tagged_clip), str(out), "--start", "0", "--duration", "0.5"]
    assert cut.main([*argv, "--crop", "320:180:0:30"]) == 0
    assert (cut.probe(out)["streams"][0]["width"]) == 1920
    side = json.loads(cut.sidecar_path(out).read_text())
    assert side["source"] == "tagged.mov"
    assert (side["start"], side["duration"], side["crop"]) == (
        "0",
        "0.5",
        "320:180:0:30",
    )
    assert side["threads"] == 8
    assert side["output"] == "crop.mp4"
    assert side["output_sha256"] == common.sha256(out)
    assert side["ffmpeg_version"] and side["arch"]
    assert side["argv"][side["argv"].index("-i") + 1] == "file:tagged.mov"
    assert side["argv"][-1] == "file:crop.mp4"
    assert ["-threads", "8"] == side["argv"][side["argv"].index("-threads") :][:2]
    # The sidecar names files, never the operator's directories.
    assert str(tmp_path) not in json.dumps(side)


@needs_ffmpeg
def test_strip_only_drops_location_audio_and_chapters(
    tagged_clip: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    out = tmp_path / "strip.mp4"
    assert run_cut(str(tagged_clip), str(out), "--strip-only") == 0
    assert cut.check_clean(out) == []
    info = cut.probe(out)
    assert [s["codec_type"] for s in info["streams"]] == ["video"]
    # No re-encode: the source size survives.
    assert (info["streams"][0]["width"], info["streams"][0]["height"]) == (320, 240)


@needs_ffmpeg
def test_a_leaky_output_fails_and_nothing_is_written(
    tagged_clip: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ffmpeg ever kept a tag, the run fails and leaves no file at all."""

    def keep_everything(src: str, out: str) -> list[str]:
        return ["ffmpeg", "-v", "error", "-y", "-i", src, "-c", "copy", out]

    monkeypatch.setattr(cut, "strip_argv", keep_everything)
    out = tmp_path / "leaky.mov"
    assert run_cut(str(tagged_clip), str(out), "--strip-only") == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["chapters.txt", "tagged.mov"]


@needs_ffmpeg
def test_an_existing_output_needs_overwrite(
    tagged_clip: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    out = tmp_path / "strip.mp4"
    out.write_bytes(b"earlier run")
    assert run_cut(str(tagged_clip), str(out), "--strip-only") == 2
    assert out.read_bytes() == b"earlier run"
    assert run_cut(str(tagged_clip), str(out), "--strip-only", "--overwrite") == 0
    assert cut.check_clean(out) == []


@needs_ffmpeg
def test_the_source_is_never_the_output(tagged_clip: pathlib.Path) -> None:
    before = tagged_clip.read_bytes()
    assert (
        run_cut(str(tagged_clip), str(tagged_clip), "--strip-only", "--overwrite") == 2
    )
    assert tagged_clip.read_bytes() == before


def test_leaks_reads_keys_values_and_chapters_and_never_returns_a_value() -> None:
    info = {
        "format": {
            "tags": {"encoder": "Lavf", "com.apple.quicktime.location.ISO6709": "x"}
        },
        "streams": [
            {"index": 0, "tags": {"handler_name": "VideoHandler"}},
            {"index": 1, "tags": {"comment": f"Location {FAKE_PLACE}"}},
        ],
        "chapters": [{"id": 0, "tags": {"title": "kitchen"}}],
    }
    found = cut.leaks(info)
    assert found == [
        "format:com.apple.quicktime.location.ISO6709",
        "stream 1:comment",
        "chapter 0",
    ]
    assert not any(FAKE_PLACE in item or "kitchen" in item for item in found)


def test_leaks_passes_a_clean_probe() -> None:
    info = {"format": {"tags": {"major_brand": "isom"}}, "streams": [{"index": 0}]}
    assert cut.leaks(info) == []


def test_probe_output_that_is_not_json_is_an_ffmpeg_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cut, "run", lambda argv: "not json")
    with pytest.raises(common.FfmpegError):
        cut.probe(pathlib.Path("x.mp4"))


def test_cut_argv_is_the_hand_recipe_plus_threads_and_chapters() -> None:
    argv = cut.cut_argv("file:/in.mov", "file:/out.mp4", "3", "8", "1080:608:0:700")
    assert argv == [
        *("ffmpeg", "-v", "error", "-y", "-i", "file:/in.mov", "-map", "0:v:0"),
        *("-ss", "3", "-t", "8", "-vf", "crop=1080:608:0:700,scale=1920:1080"),
        *("-c:v", "libx264", "-crf", "14", "-preset", "slow", "-threads", "8"),
        *("-pix_fmt", "yuv420p", "-r", "30", "-an"),
        *("-map_metadata", "-1", "-map_metadata:s:v", "-1", "-map_chapters", "-1"),
        *("-fflags", "+bitexact", "file:/out.mp4"),
    ]
    # threads=None is the v1 hand recipe: libx264's default thread count.
    assert "-threads" not in cut.cut_argv("file:/a", "file:/b", "0", "1", None, None)


def test_partial_and_sidecar_paths() -> None:
    out = pathlib.Path("/x/dog-a.mp4")
    assert cut.partial_path(out) == pathlib.Path("/x/dog-a.partial.mp4")
    assert cut.sidecar_path(out) == pathlib.Path("/x/dog-a.mp4.json")


def test_a_cut_needs_start_and_duration(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit):
        run_cut(str(tmp_path / "a.mov"), str(tmp_path / "b.mp4"), "--start", "1")


def test_a_bad_crop_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit):
        run_cut(
            *(str(tmp_path / "a.mov"), str(tmp_path / "b.mp4")),
            *("--start", "0", "--duration", "1", "--crop", "1080x608"),
        )


def test_cut_refuses_an_output_inside_this_repo(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "a.mov"
    src.write_bytes(b"")
    target = ROOT / "clip-must-not-exist.mp4"
    assert run_cut(str(src), str(target), "--strip-only") == 2
    assert not target.exists()


def test_refuse_inside_repo_covers_any_git_work_tree(tmp_path: pathlib.Path) -> None:
    other = tmp_path / "another-repo"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    # A path that does not exist yet, two levels below the work tree.
    with pytest.raises(common.InsideRepo):
        common.refuse_inside_repo(other / "new" / "deeper" / "clip.mp4")
    with pytest.raises(common.InsideRepo):
        common.refuse_inside_repo(other)
    # A plain directory beside it is fine.
    plain = tmp_path / "plain" / "clip.mp4"
    common.refuse_inside_repo(plain)


def test_refuse_inside_repo_ignores_a_hooks_git_dir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under a git hook GIT_DIR is set; that must not make every path a repo."""
    monkeypatch.setenv("GIT_DIR", str(ROOT / ".git"))
    common.refuse_inside_repo(tmp_path / "clip.mp4")
