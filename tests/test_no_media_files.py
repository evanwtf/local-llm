"""#1404: no tracked file holds video or image bytes, except the allowlist.

This repository is public. The video-gen reference clips and their frames are
the operator's own videos, and a phone video can carry its GPS position. The
`refuse-video-files` pre-commit hook and `.gitignore` match on the file NAME;
a clip renamed to `notes.txt` passes both. This test reads the first 16 bytes
of every tracked file and refuses the container signatures, whatever the name.
It is a fast module (conftest.FAST_MODULES), so the pre-push hook and CI both
run it, and it catches a commit made with `--no-verify`.

Images are allowed only where they are listed in ALLOWED_IMAGES, with a reason.
SVG is text, so no signature matches it; the one tracked SVG
(hardware/MacBook-Pro-M5-Max-.../ds4/0731/speed_q2q4_0731_ts.svg, a speed
chart) needs no entry.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: path regex -> why images may live there.
ALLOWED_IMAGES: dict[str, str] = {
    r"docs/clock-cap-1319/(.+/)?charts/[^/]+\.png": (
        "charts rendered by scripts/clock_cap_site.py for the #1319 clock-cap "
        "report; plots of our own measurements, no camera content"
    ),
}


def media_kind(head: bytes) -> str | None:
    """Name the video or image container that `head` (the first bytes) opens.

    None for anything else. ISO-BMFF covers MP4, MOV, M4V, 3GP, HEIC, HEIF
    and AVIF: all put `ftyp` at offset 4. EBML covers MKV and WebM.
    """
    if head[4:8] == b"ftyp":
        return "iso-bmff (mp4/mov/heic/avif)"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "ebml (mkv/webm)"
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return "riff avi"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "riff webp"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return None


def is_allowed_image(path: str, kind: str) -> bool:
    """True when `path` is an image (not a video) under an allowlist entry."""
    if kind not in ("jpeg", "png", "gif", "riff webp"):
        return False
    return any(re.fullmatch(pattern, path) for pattern in ALLOWED_IMAGES)


def tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    # git ls-files reads the INDEX, so a staged file counts before its commit.
    return [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]


def offenders() -> list[str]:
    found = []
    for rel in tracked():
        path = ROOT / rel
        if not path.is_file():  # deleted in the worktree, or a submodule
            continue
        with path.open("rb") as fh:
            kind = media_kind(fh.read(16))
        if kind and not is_allowed_image(rel, kind):
            found.append(f"{rel} ({kind})")
    return found


def test_no_tracked_file_is_a_video_or_an_unlisted_image() -> None:
    bad = offenders()
    assert not bad, (
        "tracked media found. Clips and frames stay in ~/bench-logs/videogen/ "
        "and never enter this public repo (#1404). A chart that belongs here "
        f"needs an ALLOWED_IMAGES entry with its reason: {bad}"
    )


def test_the_check_is_not_vacuous() -> None:
    """The allowlisted charts are real PNGs, so the reader really reads bytes."""
    charts = [p for p in tracked() if re.fullmatch(next(iter(ALLOWED_IMAGES)), p)]
    assert charts, "no allowlisted chart is tracked; the allowlist is stale"
    with (ROOT / charts[0]).open("rb") as fh:
        assert media_kind(fh.read(16)) == "png"


@pytest.mark.parametrize(
    ("head", "kind"),
    [
        (b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00", "iso-bmff (mp4/mov/heic/avif)"),
        (b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00", "iso-bmff (mp4/mov/heic/avif)"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00", "iso-bmff (mp4/mov/heic/avif)"),
        (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01", "ebml (mkv/webm)"),
        (b"RIFF\x24\x00\x00\x00AVI LIST", "riff avi"),
        (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "riff webp"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", "jpeg"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "png"),
        (b"GIF89a\x01\x00\x01\x00", "gif"),
    ],
)
def test_each_signature_is_recognized(head: bytes, kind: str) -> None:
    assert media_kind(head) == kind


@pytest.mark.parametrize(
    "head",
    [
        b"",
        b'{"version": "v1"}',
        b"RIFF\x24\x00\x00\x00WAVEfmt ",
        b"# a markdown file",
        b'<svg xmlns="http',
    ],
)
def test_other_content_passes(head: bytes) -> None:
    assert media_kind(head) is None


def test_the_allowlist_admits_only_images_in_listed_places() -> None:
    chart = "docs/clock-cap-1319/curve/charts/curve.png"
    assert is_allowed_image(chart, "png")
    assert is_allowed_image("docs/clock-cap-1319/charts/timeline.png", "png")
    # A video renamed into the chart directory is still refused.
    assert not is_allowed_image(chart, "iso-bmff (mp4/mov/heic/avif)")
    assert not is_allowed_image("benchmarks/videogen/s01.jpg", "jpeg")
    assert not is_allowed_image("docs/clock-cap-1319/charts/x.jpg", "jpeg")
