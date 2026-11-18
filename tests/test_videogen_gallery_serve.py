"""scripts/videogen/gallery_serve.py: read-only, Range, and no escape from the root (#1413)."""

from __future__ import annotations

import http.client
import pathlib
import re
import sys
import threading
from collections.abc import Iterator

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import gallery_serve

BODY = bytes(range(256)) * 40  # 10,240 bytes, each offset easy to check


@pytest.fixture
def site(tmp_path: pathlib.Path) -> Iterator[tuple[str, int, pathlib.Path]]:
    """A served gallery under an allowed root, on an ephemeral port."""
    allow = tmp_path / "videogen"
    clip_dir = allow / "out" / "h3-t2v" / "dog-a-s1"
    clip_dir.mkdir(parents=True)
    (clip_dir / "clip.mp4").write_bytes(BODY)
    (clip_dir / "run.json").write_text("{}")
    gallery = allow / "gallery"
    media = gallery / "media" / "out" / "h3-t2v"
    media.mkdir(parents=True)
    (gallery / "index.html").write_text("<!doctype html><title>g</title>")
    (gallery / ".index.html.tmp").write_text("half")
    (media / "dog-a-s1.mp4").symlink_to("../../../../out/h3-t2v/dog-a-s1/clip.mp4")
    # Outside the allowed root: a link to it must be refused.
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"private")
    (media / "leak.mp4").symlink_to(secret)
    (gallery / "run.json").symlink_to(clip_dir / "run.json")
    server = gallery_serve.make_server(gallery, allow, "127.0.0.1", 0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield str(host), int(port), gallery
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(
    site: tuple[str, int, pathlib.Path],
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection(site[0], site[1], timeout=5)
    try:
        # skip_accept_encoding etc. are irrelevant; the path goes out verbatim.
        conn.putrequest(method, path, skip_host=False)
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.putheader("Content-Length", "0")
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
    finally:
        conn.close()


CLIP = "/media/out/h3-t2v/dog-a-s1.mp4"


def test_index_and_a_whole_clip(site: tuple[str, int, pathlib.Path]) -> None:
    status, headers, body = request(site, "/")
    assert status == 200 and body.startswith(b"<!doctype html>")
    assert headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in headers["content-security-policy"]
    status, headers, body = request(site, CLIP + "?v=099907a89d5b")
    assert status == 200 and body == BODY
    assert headers["accept-ranges"] == "bytes"
    assert headers["content-type"] == "video/mp4"


@pytest.mark.parametrize(
    ("header", "first", "last"),
    [
        ("bytes=0-99", 0, 99),
        ("bytes=1000-", 1000, len(BODY) - 1),
        ("bytes=-500", len(BODY) - 500, len(BODY) - 1),
        ("bytes=10000-99999", 10000, len(BODY) - 1),
    ],
)
def test_range_gives_206_with_the_right_bytes(
    site: tuple[str, int, pathlib.Path], header: str, first: int, last: int
) -> None:
    status, headers, body = request(site, CLIP, headers={"Range": header})
    assert status == 206
    assert body == BODY[first : last + 1]
    assert headers["content-range"] == f"bytes {first}-{last}/{len(BODY)}"
    assert headers["content-length"] == str(last - first + 1)


def test_an_unsatisfiable_range_gives_416(site: tuple[str, int, pathlib.Path]) -> None:
    status, headers, _ = request(site, CLIP, headers={"Range": "bytes=20000-"})
    assert status == 416
    assert headers["content-range"] == f"bytes */{len(BODY)}"


def test_head_sends_no_body(site: tuple[str, int, pathlib.Path]) -> None:
    status, headers, body = request(site, CLIP, method="HEAD")
    assert status == 200 and body == b""
    assert headers["content-length"] == str(len(BODY))


@pytest.mark.parametrize(
    "path",
    [
        "/../out/h3-t2v/dog-a-s1/clip.mp4",
        "/%2e%2e/out/h3-t2v/dog-a-s1/clip.mp4",
        "/media/..%2f..%2fout/h3-t2v/dog-a-s1/clip.mp4",
        "/media/../../secret.mp4",
    ],
)
def test_a_path_escape_is_refused(
    site: tuple[str, int, pathlib.Path], path: str
) -> None:
    status, _, body = request(site, path)
    assert status == 403
    assert body != BODY


def test_a_symlink_out_of_the_allowed_root_is_refused(
    site: tuple[str, int, pathlib.Path],
) -> None:
    status, _, body = request(site, "/media/out/h3-t2v/leak.mp4")
    assert status == 403
    assert b"private" not in body


@pytest.mark.parametrize(
    "path",
    ["/media/", "/media/out", "/.index.html.tmp", "/run.json", "/missing.mp4"],
)
def test_dirs_hidden_files_and_other_types_are_not_served(
    site: tuple[str, int, pathlib.Path], path: str
) -> None:
    status, _, _ = request(site, path)
    assert status == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_writes_are_refused(site: tuple[str, int, pathlib.Path], method: str) -> None:
    status, headers, _ = request(site, "/index.html", method=method)
    assert status == 405
    assert headers["allow"] == "GET, HEAD"
    assert (site[2] / "index.html").read_text().startswith("<!doctype html>")


def test_a_dir_outside_the_allowed_root_is_refused(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with pytest.raises(ValueError, match="not inside"):
        gallery_serve.make_server(tmp_path / "a", tmp_path / "b", "127.0.0.1", 0)


@pytest.mark.parametrize(
    ("header", "want"),
    [
        ("bytes=0-0", (0, 0)),
        ("bytes=5-", (5, 99)),
        ("bytes=-1", (99, 99)),
        ("bytes=-1000", (0, 99)),
        ("bytes=0-5,10-20", None),
        ("items=0-5", None),
    ],
)
def test_parse_range(header: str, want: tuple[int, int] | None) -> None:
    assert gallery_serve.parse_range(header, 100) == want


@pytest.mark.parametrize("header", ["bytes=100-", "bytes=5-2", "bytes=-0"])
def test_parse_range_refuses(header: str) -> None:
    with pytest.raises(ValueError):
        gallery_serve.parse_range(header, 100)


def test_the_unit_runs_the_server_on_the_default_port() -> None:
    """The unit file and the code name one port; a change to one fails here."""
    unit = (ROOT / "systemd" / "local-llm-videogen-gallery.service").read_text()
    exec_start = re.search(r"^ExecStart=(.+)$", unit, re.MULTILINE)
    assert exec_start is not None
    assert "scripts/videogen/gallery_serve.py" in exec_start[1]
    assert f"--port {gallery_serve.DEFAULT_PORT}" in exec_start[1]
    assert gallery_serve.DEFAULT_PORT not in (8000, 8020, 8030, 8040, 8041, 8042)
    runbook = (ROOT / "docs" / "dgx-spark-runbook.md").read_text()
    assert f"| {gallery_serve.DEFAULT_PORT} |" in runbook
