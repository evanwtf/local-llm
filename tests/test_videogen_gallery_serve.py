"""scripts/videogen/gallery_serve.py: the login gate, read-only, Range, Host,
timeouts, and no escape from the root (#1413)."""

from __future__ import annotations

import contextlib
import html.parser
import http.client
import pathlib
import re
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterator
from typing import Any, NamedTuple

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import gallery, gallery_auth, gallery_serve

BODY = bytes(range(256)) * 40  # 10,240 bytes, each offset easy to check
CSP = "default-src 'none'; style-src 'sha256-abc='; base-uri 'none'"
USER, PASSWORD = "wat", "correct horse battery staple"
#: The only trusted proxy in these tests. Loopback, so a test can be it.
PROXY = "127.0.0.1"


class Site(NamedTuple):
    host: str
    port: int
    dir: pathlib.Path
    cookie: str
    auth_file: pathlib.Path


def make_auth(where: pathlib.Path) -> pathlib.Path:
    path = where / "cfg" / "auth.yaml"
    if not path.exists():
        gallery_auth.write_auth(path, USER, PASSWORD, n=gallery_auth.MIN_N)
    return path


@contextlib.contextmanager
def serving(
    gallery_dir: pathlib.Path, allow: pathlib.Path, **kw: Any
) -> Iterator[Site]:
    """A server on an ephemeral loopback port, stopped on exit, with an auth
    file outside the served tree and a valid session cookie."""
    auth_file = kw.pop("auth_file", None) or make_auth(allow.parent)
    server = gallery_serve.make_server(
        gallery_dir, allow, "127.0.0.1", 0, auth_file=auth_file, **kw
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    host, port = server.server_address[:2]
    cookie = gallery_auth.make_cookie(
        gallery_auth.load_auth(auth_file), USER, now=time.time()
    )
    try:
        yield Site(str(host), int(port), gallery_dir, cookie, auth_file)
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


#: Pass as `cookie` to send no Cookie header.
NO_COOKIE = ""


def request(
    site: Site,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    host: str | None = None,
    cookie: str | None = None,
    body: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    """One request; the path goes out verbatim. `host` replaces the Host header
    (an empty string sends none). The site's session cookie goes with it
    unless `cookie` says otherwise (NO_COOKIE sends none)."""
    conn = http.client.HTTPConnection(site.host, site.port, timeout=5)
    value = site.cookie if cookie is None else cookie
    try:
        conn.putrequest(method, path, skip_host=host is not None)
        if host:
            conn.putheader("Host", host)
        if value:
            conn.putheader("Cookie", f"{gallery_auth.COOKIE}={value}")
        for key, val in (headers or {}).items():
            conn.putheader(key, val)
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        if body:
            conn.send(body)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


def login(
    site: Site,
    username: str = USER,
    password: str = PASSWORD,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    form = urllib.parse.urlencode({"username": username, "password": password})
    return request(
        site,
        "/login",
        "POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            **(headers or {}),
        },
        cookie=NO_COOKIE,
        body=form.encode(),
    )


def session_from(headers: dict[str, str]) -> str:
    cookie = headers["set-cookie"]
    name, _, rest = cookie.partition("=")
    assert name == gallery_auth.COOKIE
    return rest.split(";", 1)[0]


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
    (site.dir / gallery.CSP_FILE).unlink()
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
    assert (site.dir / "index.html").read_text().startswith("<!doctype html>")


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
    # No LAN address in this public repo: the proxy goes in the drop-in.
    assert "--trusted-proxy" not in exec_start[1]
    assert not re.search(r"\b(192\.168|172\.(1[6-9]|2\d|3[01]))\.", unit)
    assert gallery_serve.DEFAULT_PORT not in (8000, 8020, 8030, 8040, 8041, 8042)
    runbook = (ROOT / "docs" / "dgx-spark-runbook.md").read_text()
    assert f"| {gallery_serve.DEFAULT_PORT} |" in runbook


# ----------------------------------------------------------- the login gate ----


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/", 303),
        ("GET", "/index.html", 303),
        ("HEAD", "/", 303),
        ("POST", "/logout", 303),
        ("GET", CLIP, 401),
        ("HEAD", CLIP, 401),
        ("GET", "/media/out/h3-t2v/empty.mp4", 401),
        ("GET", "/missing.mp4", 401),
        ("GET", "/../secret.mp4", 401),
        ("GET", "/.csp", 401),
        ("GET", "/%6cogin", 401),
        ("GET", "/login/x", 401),
        ("POST", "/index.html", 303),
        ("PUT", CLIP, 401),
        ("DELETE", "/", 303),
    ],
)
def test_no_cookie_gives_a_redirect_or_401(
    site: Site, method: str, path: str, status: int
) -> None:
    got, headers, body = request(site, path, method, cookie=NO_COOKIE)
    assert got == status
    assert body != BODY and b"doctype" not in body
    if status == 303:
        # Relative, so it works under a path prefix such as /videogen/.
        assert headers["location"] == "login"
    assert "set-cookie" not in headers


@pytest.mark.parametrize(
    "cookie",
    ["garbage", "a.b", "", "x" * 5000],
)
def test_a_junk_cookie_is_no_session(site: Site, cookie: str) -> None:
    status, _, _ = request(site, CLIP, cookie=cookie or "=")
    assert status == 401


def test_a_tampered_expired_or_foreign_cookie_is_refused(
    site: Site, tmp_path: pathlib.Path
) -> None:
    auth = gallery_auth.load_auth(site.auth_file)
    body, sig = site.cookie.split(".")
    flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    expired = gallery_auth.make_cookie(
        auth, USER, now=time.time() - gallery_auth.SESSION_S - 10
    )
    other = tmp_path / "other" / "auth.yaml"
    gallery_auth.write_auth(other, USER, PASSWORD, n=gallery_auth.MIN_N)
    foreign = gallery_auth.make_cookie(
        gallery_auth.load_auth(other), USER, now=time.time()
    )
    for bad in (f"{body}.{flipped}", expired, foreign):
        status, _, _ = request(site, CLIP, cookie=bad)
        assert status == 401, bad
    assert request(site, CLIP)[0] == 200


def test_the_login_page_is_served_without_a_session(site: Site) -> None:
    status, headers, body = request(site, "/login", cookie=NO_COOKIE)
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    text = body.decode()
    assert 'name="username"' in text and 'name="password"' in text
    assert 'type="password"' in text
    assert "Wrong username or password." not in text
    assert headers["cache-control"] == "no-store"


class Links(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []
        self.tags: list[str] = []
        self.style: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self._in_style = tag == "style"
        for key, value in attrs:
            if key in ("href", "src", "action", "formaction") and value is not None:
                self.urls.append(value)

    def handle_endtag(self, tag: str) -> None:
        self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.style.append(data)


def test_the_login_page_is_self_contained_and_relative(site: Site) -> None:
    _, headers, body = request(site, "/login", cookie=NO_COOKIE)
    page = Links()
    page.feed(body.decode())
    assert page.urls == ["login"]  # the form's action, and nothing else
    for url in page.urls:
        assert not url.startswith("/") and "//" not in url and ":" not in url
    assert "script" not in page.tags and "img" not in page.tags
    assert b"http://" not in body and b"https://" not in body
    policy = headers["content-security-policy"]
    assert f"style-src {gallery.csp_hash(page.style[0])}" in policy
    assert "form-action 'self'" in policy
    assert "default-src 'none'" in policy and "unsafe-inline" not in policy
    assert "script-src" not in policy


def test_a_wrong_password_fails_and_a_right_one_sets_the_cookie(site: Site) -> None:
    status, headers, body = login(site, password="wrong password!")
    assert status == 401
    assert b"Wrong username or password." in body
    assert "set-cookie" not in headers
    status, headers, body = login(site, username="nobody")
    assert status == 401 and b"Wrong username or password." in body
    status, headers, _ = login(site)
    assert status == 303
    assert headers["location"] == "./"
    cookie = headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert f"Max-Age={gallery_auth.SESSION_S}" in cookie
    # No Path=: the browser scopes the cookie to the directory of the URL it
    # posted to, which is /videogen/ behind the proxy and / on the LAN.
    assert "Path=" not in cookie and "Domain=" not in cookie
    assert "Secure" not in cookie  # plain HTTP from an untrusted peer
    status, _, got = request(site, CLIP, cookie=session_from(headers))
    assert status == 200 and got == BODY


def test_a_logged_in_visit_to_login_goes_to_the_gallery(site: Site) -> None:
    status, headers, _ = request(site, "/login")
    assert status == 303 and headers["location"] == "./"


def test_logout_clears_the_cookie(site: Site) -> None:
    status, headers, _ = request(site, "/logout", "POST")
    assert status == 303 and headers["location"] == "login"
    cookie = headers["set-cookie"]
    assert cookie.startswith(f"{gallery_auth.COOKIE}=;")
    assert "Max-Age=0" in cookie and "Path=" not in cookie
    assert request(site, "/logout")[0] == 405  # GET cannot log out


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({"Content-Type": "text/plain"}, b"username=wat", 415),
        ({"Content-Type": "application/x-www-form-urlencoded"}, b"x" * 5000, 413),
    ],
)
def test_a_bad_login_body_is_refused(
    site: Site, headers: dict[str, str], body: bytes, status: int
) -> None:
    got, _, _ = request(
        site, "/login", "POST", headers=headers, cookie=NO_COOKIE, body=body
    )
    assert got == status


def test_five_failures_lock_the_client_out(site: Site) -> None:
    for _ in range(5):
        assert login(site, password="wrong password!")[0] == 401
    status, headers, body = login(site)  # even the right password
    assert status == 429
    assert int(headers["retry-after"]) > 500
    assert b"Too many failed logins" in body
    assert "set-cookie" not in headers


def test_x_forwarded_for_is_ignored_from_an_untrusted_peer(
    tmp_path: pathlib.Path,
) -> None:
    site_dir, allow = make_tree(tmp_path)
    with serving(site_dir, allow) as s:  # no trusted proxy
        for i in range(5):
            # A new X-Forwarded-For each time must not dodge the lockout.
            fake = {"X-Forwarded-For": f"203.0.113.{i}"}
            assert login(s, password="wrong password!", headers=fake)[0] == 401
        assert login(s, headers={"X-Forwarded-For": "203.0.113.99"})[0] == 429


def test_behind_a_trusted_proxy_the_client_comes_from_x_forwarded_for(
    tmp_path: pathlib.Path,
) -> None:
    site_dir, allow = make_tree(tmp_path)
    with serving(site_dir, allow, trusted_proxies=(PROXY,)) as s:
        bad = {"X-Forwarded-For": "203.0.113.7"}
        for _ in range(5):
            assert login(s, password="wrong password!", headers=bad)[0] == 401
        assert login(s, headers=bad)[0] == 429
        # Another client behind the same proxy is not locked out, even when
        # the locked one's address is spoofed in the left part.
        good = {"X-Forwarded-For": "203.0.113.7, 198.51.100.4"}
        assert login(s, headers=good)[0] == 303


@pytest.mark.parametrize(
    ("trusted", "proto", "secure"),
    [
        ((PROXY,), "https", True),
        ((PROXY,), "http", False),
        ((PROXY,), None, False),
        ((), "https", False),  # the header from an untrusted peer is ignored
    ],
)
def test_secure_only_behind_a_trusted_proxy(
    tmp_path: pathlib.Path, trusted: tuple[str, ...], proto: str | None, secure: bool
) -> None:
    site_dir, allow = make_tree(tmp_path)
    with serving(site_dir, allow, trusted_proxies=trusted) as s:
        headers = {"X-Forwarded-Proto": proto} if proto else {}
        status, got, _ = login(s, headers=headers)
        assert status == 303
        assert ("; Secure" in got["set-cookie"]) is secure
        _, out, _ = request(s, "/logout", "POST", headers=headers)
        assert ("; Secure" in out["set-cookie"]) is secure


def test_a_new_password_ends_every_session(site: Site) -> None:
    assert request(site, CLIP)[0] == 200
    gallery_auth.write_auth(
        site.auth_file, USER, "a brand new password", n=gallery_auth.MIN_N
    )
    assert request(site, CLIP)[0] == 401


def test_a_bad_auth_file_mode_at_runtime_fails_closed(site: Site) -> None:
    site.auth_file.chmod(0o644)
    assert request(site, CLIP)[0] == 401
    assert login(site)[0] == 503


@pytest.mark.parametrize("mode", [None, 0o644])
def test_the_server_refuses_to_start_without_a_good_auth_file(
    tmp_path: pathlib.Path, mode: int | None
) -> None:
    site_dir, allow = make_tree(tmp_path)
    auth = tmp_path / "auth.yaml"
    if mode is not None:
        gallery_auth.write_auth(auth, USER, PASSWORD, n=gallery_auth.MIN_N)
        auth.chmod(mode)
    with pytest.raises(gallery_auth.AuthError):
        gallery_serve.make_server(site_dir, allow, "127.0.0.1", 0, auth_file=auth)
    rc = gallery_serve.main(
        [
            "--dir",
            str(site_dir),
            "--allow",
            str(allow),
            "--port",
            "0",
            "--host",
            "127.0.0.1",
            "--auth-file",
            str(auth),
        ]
    )
    assert rc == 2


def test_set_password_writes_a_0600_file(tmp_path: pathlib.Path) -> None:
    auth = tmp_path / "new" / "dir" / "auth.yaml"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "videogen" / "gallery_serve.py"),
            "set-password",
            "--user",
            USER,
            "--auth-file",
            str(auth),
        ],
        input=PASSWORD + "\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert stat.S_IMODE(auth.stat().st_mode) == 0o600
    assert stat.S_IMODE(auth.parent.stat().st_mode) == 0o700
    assert PASSWORD not in auth.read_text()
    assert PASSWORD not in result.stdout + result.stderr
    assert gallery_auth.verify(gallery_auth.load_auth(auth), USER, PASSWORD) == USER


def test_set_password_refuses_a_short_password(tmp_path: pathlib.Path) -> None:
    auth = tmp_path / "auth.yaml"
    rc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "videogen" / "gallery_serve.py"),
            "set-password",
            "--user",
            USER,
            "--auth-file",
            str(auth),
        ],
        input="short\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    ).returncode
    assert rc == 2 and not auth.exists()


def test_logins_are_logged_without_the_password(
    site: Site, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO"):
        login(site, password="wrong password!")
        login(site)
    text = caplog.text
    assert "login failed" in text and "login ok" in text
    assert "127.0.0.1" in text
    assert PASSWORD not in text and "wrong password!" not in text


def test_a_malformed_request_line_does_not_crash_the_handler(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    sock = socket.create_connection((site.host, site.port), timeout=5)
    try:
        sock.sendall(b"NONSENSE\r\n\r\n")
        data = b""
        while chunk := sock.recv(4096):
            data += chunk
    finally:
        sock.close()
    assert b"200" not in data
    assert request(site, "/")[0] == 200
    assert "Traceback" not in capsys.readouterr().err
