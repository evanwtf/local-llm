"""scripts/videogen/gallery_serve.py: read-only, Range, Host, timeouts, and no
escape from the root (#1413)."""

from __future__ import annotations

import contextlib
import http.client
import pathlib
import re
import socket
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import gallery, gallery_serve

BODY = bytes(range(256)) * 40  # 10,240 bytes, each offset easy to check
CSP = "default-src 'none'; style-src 'sha256-abc='; base-uri 'none'"

Site = tuple[str, int, pathlib.Path]


@contextlib.contextmanager
def serving(
    gallery_dir: pathlib.Path, allow: pathlib.Path, **kw: Any
) -> Iterator[Site]:
    """A server on an ephemeral loopback port, stopped on exit."""
    server = gallery_serve.make_server(gallery_dir, allow, "127.0.0.1", 0, **kw)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield str(host), int(port), gallery_dir
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def make_tree(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """(gallery dir, allowed root) with a clip, its link and some traps."""
    allow = tmp_path / "videogen"
    clip_dir = allow / "out" / "h3-t2v" / "dog-a-s1"
    clip_dir.mkdir(parents=True)
    (clip_dir / "clip.mp4").write_bytes(BODY)
    (clip_dir / "run.json").write_text("{}")
    (clip_dir / "empty.mp4").write_bytes(b"")
    site = allow / "gallery"
    media = site / "media" / "out" / "h3-t2v"
    media.mkdir(parents=True)
    (site / "index.html").write_text("<!doctype html><title>g</title>")
    (site / ".index.html.tmp").write_text("half")
    (site / gallery.CSP_FILE).write_text(CSP + "\n")
    (media / "dog-a-s1.mp4").symlink_to("../../../../out/h3-t2v/dog-a-s1/clip.mp4")
    (media / "empty.mp4").symlink_to(clip_dir / "empty.mp4")
    # Outside the allowed root: a link to it must be refused.
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"private")
    (media / "leak.mp4").symlink_to(secret)
    (site / "run.json").symlink_to(clip_dir / "run.json")
    # The link name and the real file disagree on the type, both ways.
    (media / "json-as.mp4").symlink_to(clip_dir / "run.json")
    (site / "clip-as.html").symlink_to(clip_dir / "clip.mp4")
    return site, allow


@pytest.fixture
def site(tmp_path: pathlib.Path) -> Iterator[Site]:
    site_dir, allow = make_tree(tmp_path)
    with serving(site_dir, allow, host_names=("gallery.test",)) as s:
        yield s


def request(
    site: Site,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    host: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """One request; the path goes out verbatim. `host` replaces the Host header
    (an empty string sends none)."""
    conn = http.client.HTTPConnection(site[0], site[1], timeout=5)
    try:
        conn.putrequest(method, path, skip_host=host is not None)
        if host:
            conn.putheader("Host", host)
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


def test_index_and_a_whole_clip(site: Site) -> None:
    status, headers, body = request(site, "/")
    assert status == 200 and body.startswith(b"<!doctype html>")
    assert headers["content-type"].startswith("text/html")
    status, headers, body = request(site, CLIP + "?v=099907a89d5b")
    assert status == 200 and body == BODY
    assert headers["accept-ranges"] == "bytes"
    assert headers["content-type"] == "video/mp4"


def test_the_csp_is_the_one_gallery_py_wrote(site: Site) -> None:
    _, headers, _ = request(site, "/")
    assert headers["content-security-policy"] == CSP
    (site[2] / gallery.CSP_FILE).unlink()
    _, headers, _ = request(site, "/")
    assert headers["content-security-policy"] == gallery_serve.FALLBACK_CSP
    assert "'unsafe-inline'" not in gallery_serve.FALLBACK_CSP


def test_a_built_gallery_is_served_with_its_hashes(tmp_path: pathlib.Path) -> None:
    allow = tmp_path / "videogen"
    allow.mkdir()
    result = gallery.build(allow, allow / "gallery")
    with serving(result.index.parent, allow) as s:
        status, headers, body = request(s, "/")
    assert status == 200 and body == result.index.read_bytes()
    assert headers["content-security-policy"] == gallery.csp()


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
    site: Site, header: str, first: int, last: int
) -> None:
    status, headers, body = request(site, CLIP, headers={"Range": header})
    assert status == 206
    assert body == BODY[first : last + 1]
    assert headers["content-range"] == f"bytes {first}-{last}/{len(BODY)}"
    assert headers["content-length"] == str(last - first + 1)


@pytest.mark.parametrize(
    ("path", "header", "size"),
    [
        (CLIP, "bytes=20000-", len(BODY)),
        ("/media/out/h3-t2v/empty.mp4", "bytes=-5", 0),
        ("/media/out/h3-t2v/empty.mp4", "bytes=0-", 0),
    ],
)
def test_an_unsatisfiable_range_gives_416(
    site: Site, path: str, header: str, size: int
) -> None:
    status, headers, _ = request(site, path, headers={"Range": header})
    assert status == 416
    assert headers["content-range"] == f"bytes */{size}"


def test_head_sends_no_body(site: Site) -> None:
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
def test_a_path_escape_is_refused(site: Site, path: str) -> None:
    status, _, body = request(site, path)
    assert status == 403
    assert body != BODY


def test_a_symlink_out_of_the_allowed_root_is_refused(site: Site) -> None:
    status, _, body = request(site, "/media/out/h3-t2v/leak.mp4")
    assert status == 403
    assert b"private" not in body


@pytest.mark.parametrize(
    "path",
    [
        "/media/",
        "/media/out",
        "/.index.html.tmp",
        "/.csp",
        "/run.json",
        "/missing.mp4",
        # The link's type and the real file's type must agree, both ways.
        "/media/out/h3-t2v/json-as.mp4",
        "/clip-as.html",
    ],
)
def test_dirs_hidden_files_and_other_types_are_not_served(
    site: Site, path: str
) -> None:
    status, _, body = request(site, path)
    assert status == 404
    assert body != BODY and b"{}" not in body


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_writes_are_refused(site: Site, method: str) -> None:
    status, headers, _ = request(site, "/index.html", method=method)
    assert status == 405
    assert headers["allow"] == "GET, HEAD"
    assert (site[2] / "index.html").read_text().startswith("<!doctype html>")


@pytest.mark.parametrize(
    ("host", "status"),
    [
        ("127.0.0.1:8043", 200),
        ("10.0.0.9", 200),
        ("[::1]:8043", 200),
        ("localhost:8043", 200),
        ("LOCALHOST.", 200),
        ("gallery.test:8043", 200),
        ("Gallery.Test", 200),
        ("evil.example", 421),
        ("evil.example:8043", 421),
        ("gallery.test.evil.example", 421),
        ("127.0.0.1:x", 421),
        ("", 400),
    ],
)
def test_the_host_header_is_checked(site: Site, host: str, status: int) -> None:
    got, _, body = request(site, "/", host=host)
    assert got == status
    if status != 200:
        assert b"doctype" not in body


def test_an_unknown_host_cannot_write_either(site: Site) -> None:
    status, _, _ = request(site, "/index.html", method="POST", host="evil.example")
    assert status == 421


def test_an_idle_connection_is_closed_after_the_timeout(
    tmp_path: pathlib.Path,
) -> None:
    site_dir, allow = make_tree(tmp_path)
    with serving(site_dir, allow, timeout=0.3) as s:
        sock = socket.create_connection((s[0], s[1]), timeout=5)
        try:
            t0 = time.monotonic()
            # Send nothing: the server must hang up on its own.
            assert sock.recv(1024) == b""
            assert time.monotonic() - t0 < 4
        finally:
            sock.close()


def test_connections_over_the_cap_are_closed(tmp_path: pathlib.Path) -> None:
    site_dir, allow = make_tree(tmp_path)
    with serving(site_dir, allow, timeout=3, max_connections=1) as s:
        held = socket.create_connection((s[0], s[1]), timeout=5)
        try:
            time.sleep(0.3)  # let the server take the only slot
            extra = socket.create_connection((s[0], s[1]), timeout=2)
            try:
                extra.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                try:
                    got = extra.recv(1024)
                except ConnectionResetError:
                    got = b""
                assert got == b""
            finally:
                extra.close()
        finally:
            held.close()
        # The slot comes back when the first connection ends.
        time.sleep(0.3)
        status, _, _ = request(s, "/")
        assert status == 200


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


@pytest.mark.parametrize(
    ("header", "size"),
    [("bytes=100-", 100), ("bytes=5-2", 100), ("bytes=-0", 100), ("bytes=-5", 0)],
)
def test_parse_range_refuses(header: str, size: int) -> None:
    with pytest.raises(ValueError):
        gallery_serve.parse_range(header, size)


def test_the_unit_runs_the_server_on_the_default_port() -> None:
    """The unit file and the code name one port; a change to one fails here."""
    unit = (ROOT / "systemd" / "local-llm-videogen-gallery.service").read_text()
    exec_start = re.search(r"^ExecStart=(.+)$", unit, re.MULTILINE)
    assert exec_start is not None
    assert "scripts/videogen/gallery_serve.py" in exec_start[1]
    assert f"--port {gallery_serve.DEFAULT_PORT}" in exec_start[1]
    # Host names come from a drop-in, never from the repo.
    assert "--host-name" not in exec_start[1]
    assert exec_start[1].endswith("$GALLERY_HOST_ARGS")
    assert "network-online" not in unit
    assert gallery_serve.DEFAULT_PORT not in (8000, 8020, 8030, 8040, 8041, 8042)
    runbook = (ROOT / "docs" / "dgx-spark-runbook.md").read_text()
    assert f"| {gallery_serve.DEFAULT_PORT} |" in runbook
