"""Serve the video-gen gallery behind a login, with HTTP Range for video seeking (#1413).

    gallery_serve.py [--dir ~/bench-logs/videogen/gallery] [--port 8043]
                     [--host-name NAME ...] [--trusted-proxy IP ...]
                     [--auth-file ~/.config/local-llm/videogen-gallery/auth.yaml]
    gallery_serve.py set-password --user NAME [--auth-file PATH]

The gallery is reachable from the internet through a reverse proxy, so every
request needs a session except the login form itself (`GET /login`,
`POST /login`). Without a session, the page gets a 303 to `login` and any
other file gets 401. Every link and redirect is relative, so the site works
under a path prefix (`/videogen/`) and at the root.

- **Credentials** live in a YAML file outside the repository (see
  `gallery_auth.py`): one user, a salted scrypt hash, and the session secret.
  The server refuses to start without it, or with a mode other than 0600.
  `set-password` writes it; a new password makes a new secret and so ends
  every session. The server reads the file again when it changes.
- **The session** is an HMAC-SHA256-signed cookie that lasts 30 days:
  `HttpOnly; SameSite=Lax`, and `Secure` when a trusted proxy says the client
  used HTTPS. It has no `Path`, so the browser scopes it to the directory of
  the login URL it saw, which the server cannot see behind the proxy.
- **Brute force**: 5 failed logins from one client in 10 minutes lock that
  client out of the form for 10 minutes (429). The client is the peer, or,
  only when the peer is a `--trusted-proxy`, the X-Forwarded-For entry that
  proxy added.

The server also:

- answers GET and HEAD for files, POST only for `login` and `logout`, and
  405 for anything else, so nothing can write through it.
- answers only a request whose Host header is an IP literal, `localhost`,
  or a name given with `--host-name` (repeatable). Any other Host gets 421.
- maps a URL path only to a file under `--dir`. It refuses a `..` segment, a
  hidden name (a leading dot, such as the builder's temp files and `.csp`),
  a directory, and a file type that is not on its list. The link name and
  the real file must have the same type.
- resolves every symlink and refuses a file whose real path is outside
  `--allow` (default `~/bench-logs/videogen/`).
- supports a single `Range: bytes=` request with 206 and `Content-Range`.
- sends the Content-Security-Policy that `gallery.py` writes beside the page
  (`.csp`, with the sha256 of the page's inline CSS and JS).
- closes a connection that is idle for 30 s, and serves at most 64
  connections at once.

It binds 0.0.0.0 on port 8043 by default. `systemd/local-llm-videogen-gallery.service`
runs it as a user service; `scripts/videogen/README.md` has the install
commands.
"""

from __future__ import annotations

import argparse
import html
import http.server
import io
import ipaddress
import logging
import os
import pathlib
import re
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable
from typing import Any, BinaryIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen import gallery_auth as auth
from videogen.common import DEFAULT_ROOT
from videogen.gallery import CSP_FILE, csp_hash

import logs

logger = logging.getLogger(__name__)

#: 8040 is H3 and 8041 is vLLM-Omni (Wan); 8042 is `dgx_server.py`'s ad-hoc
#: clip viewer. 8043 was free on the head and named nowhere in the repo.
#: `tests/test_videogen_gallery_serve.py` pins the unit file to this value.
DEFAULT_PORT = 8043
DEFAULT_DIR = DEFAULT_ROOT / "gallery"
#: Seconds a connection may sit idle before the server closes it.
IDLE_TIMEOUT_S = 30.0
#: Connections served at once; more are closed at once.
MAX_CONNECTIONS = 64

#: Only these types are served. A stray run.json or frame is not.
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mp4": "video/mp4",
    ".png": "image/png",
}

#: Sent when the gallery has no `.csp` yet: nothing inline runs and nothing
#: loads from elsewhere, so the page shows unstyled, but it shows.
FALLBACK_CSP = (
    "default-src 'none'; media-src 'self'; img-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
#: The largest login form body read.
MAX_FORM = 4096
#: Pages that get a 303 to the login form instead of a 401.
PAGES = frozenset({"/", "/index.html", "/logout"})

LOGIN_CSS = """
:root { color-scheme: light dark; --bg: #f6f6f4; --fg: #1c1c1c; --muted: #5e5e5e;
  --card: #ffffff; --line: #d9d9d4; --bad: #b3261e; --accent: #2456a6; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #141414; --fg: #e8e8e6; --muted: #a3a3a0; --card: #1f1f1f;
    --line: #333331; --bad: #f2867e; --accent: #8ab4f8; }
}
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center;
  background: var(--bg); color: var(--fg); padding: 16px;
  font: 16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
form { width: 100%; max-width: 360px; background: var(--card);
  border: 1px solid var(--line); border-radius: 10px; padding: 20px;
  display: flex; flex-direction: column; gap: 12px; }
h1 { font-size: 1.2rem; margin: 0; }
label { display: flex; flex-direction: column; gap: 4px; color: var(--muted); }
input { font: inherit; color: var(--fg); background: var(--bg);
  border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; }
button { font: inherit; color: #fff; background: var(--accent); border: 0;
  border-radius: 6px; padding: 10px; cursor: pointer; }
.error { color: var(--bad); margin: 0; }
"""


#: The login page runs no script; its one form posts back to this server.
LOGIN_CSP = (
    f"default-src 'none'; style-src {csp_hash(LOGIN_CSS)}; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def login_page(message: str | None = None) -> bytes:
    """The login form. Its one URL, the form action, is relative."""
    error = (
        f'<p class="error" role="alert">{html.escape(message)}</p>' if message else ""
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="referrer" content="same-origin">'
        "<title>Video-gen gallery: log in</title>"
        f"<style>{LOGIN_CSS}</style></head><body>"
        '<form method="post" action="login">'
        "<h1>Video-gen gallery</h1>"
        f"{error}"
        '<label>Username <input name="username" autocomplete="username" '
        'autocapitalize="none" spellcheck="false" required autofocus></label>'
        '<label>Password <input name="password" type="password" '
        'autocomplete="current-password" required></label>'
        '<button type="submit">Log in</button>'
        "</form></body></html>\n"
    ).encode()


RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
CHUNK = 1 << 16


class Refused(Exception):
    """A request path that the server will not serve."""

    def __init__(self, status: int, why: str) -> None:
        super().__init__(why)
        self.status = status


def resolve_request(
    url_path: str, root: pathlib.Path, allow: pathlib.Path
) -> pathlib.Path:
    """The real file for `url_path`, or Refused with a status.

    `root` and `allow` must already be resolved.
    """
    path = urllib.parse.unquote(urllib.parse.urlsplit(url_path).path, errors="strict")
    if "\x00" in path or "\\" in path:
        raise Refused(400, "bad characters in the path")
    parts = [p for p in path.split("/") if p]
    if any(p == ".." for p in parts):
        raise Refused(403, "a .. segment")
    if not parts:
        parts = ["index.html"]
    if any(p.startswith(".") for p in parts):
        raise Refused(404, "a hidden name")
    candidate = root.joinpath(*parts)
    try:
        real = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as err:
        raise Refused(404, "no such file") from err
    if not real.is_relative_to(allow):
        raise Refused(403, f"resolves outside {allow}")
    if not real.is_file():
        raise Refused(404, "not a file")
    # Both names must carry one served type: a `.mp4` link to a `.json`
    # file, or a `.html` link to a `.png`, is not served.
    suffix = real.suffix.lower()
    if suffix not in TYPES or suffix != candidate.suffix.lower():
        raise Refused(404, "a file type this server does not serve")
    return real


def host_allowed(header: str | None, names: frozenset[str]) -> bool:
    """True for an IP literal, `localhost`, or one of `names` (any port)."""
    if not header:
        return False
    host = header.strip()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return False
        host = host[1:end]
    elif host.count(":") == 1:
        host, _, port = host.partition(":")
        if not port.isdigit():
            return False
    host = host.rstrip(".").lower()
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or host in names
    return True


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """(first, last) inclusive for one `bytes=` range; None to send it all.

    Raises ValueError for a range that cannot be satisfied (416). On an empty
    file every byte range is unsatisfiable.
    """
    match = RANGE.match(header.strip())
    if match is None:
        # Several ranges or another unit: RFC 9110 lets a server ignore it.
        return None
    first_s, last_s = match.groups()
    if not first_s and not last_s:
        return None
    if size == 0:
        raise ValueError("a range on an empty file")
    if not first_s:
        suffix = int(last_s)
        if suffix == 0:
            raise ValueError("an empty suffix range")
        return max(0, size - suffix), size - 1
    first = int(first_s)
    last = int(last_s) if last_s else size - 1
    if first >= size or last < first:
        raise ValueError(f"bytes {first}-{last} of {size}")
    return first, min(last, size - 1)


def copy_span(src: BinaryIO, dst: io.BufferedIOBase, first: int, length: int) -> int:
    """Copy `length` bytes from `first`; returns how many were copied."""
    src.seek(first)
    left = length
    while left > 0:
        block = src.read(min(CHUNK, left))
        if not block:
            break
        dst.write(block)
        left -= len(block)
    return length - left


def cookie_values(headers: list[str]) -> list[str]:
    """Every value of the session cookie in the Cookie headers.

    A browser can send two (one per path); each is checked.
    """
    found = []
    for header in headers:
        for part in header.split(";"):
            name, eq, value = part.strip().partition("=")
            if eq and name == auth.COOKIE and value:
                found.append(value)
    return found


class Handler(http.server.BaseHTTPRequestHandler):
    """The login gate, then GET and HEAD for files under the root."""

    server_version = "videogen-gallery"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    #: StreamRequestHandler applies this to the socket: an idle client is
    #: closed instead of holding a thread for ever.
    timeout = IDLE_TIMEOUT_S
    root: pathlib.Path
    allow: pathlib.Path
    host_names: frozenset[str] = frozenset()
    trusted: frozenset[str] = frozenset()
    store: auth.AuthStore
    lockout: auth.Lockout
    now: Callable[[], float] = time.time

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s %s", self.client(), format % args)

    def host_ok(self) -> bool:
        """Refuse a Host header we do not know (DNS rebinding); 421."""
        host = self.headers.get("Host")
        if host_allowed(host, self.host_names):
            return True
        logger.warning("refused Host %r for %r", host, self.path)
        self.send_plain(421 if host else 400, "unknown host")
        return False

    # ------------------------------------------------------------ routing --

    def do_GET(self) -> None:
        self.route()

    def do_HEAD(self) -> None:
        self.route()

    def do_POST(self) -> None:
        self.route()

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST
    do_TRACE = do_CONNECT = do_PROPFIND = do_MKCOL = do_COPY = do_MOVE = do_POST

    def route(self) -> None:
        if not self.host_ok():
            return
        path = urllib.parse.urlsplit(self.path).path
        method = self.command
        if path == "/login":
            if method in ("GET", "HEAD"):
                if self.session_user() is not None:
                    self.redirect("./")
                else:
                    self.send_login(200)
            elif method == "POST":
                self.login()
            else:
                self.refuse_method("GET, HEAD, POST")
            return
        if self.session_user() is None:
            # Never say whether a file exists before a login.
            self.close_connection = True
            if path in PAGES:
                self.redirect("login")
            else:
                self.send_plain(401, "log in first")
            return
        if path == "/logout":
            if method == "POST":
                self.logout()
            else:
                self.refuse_method("POST")
        elif method in ("GET", "HEAD"):
            self.serve(body=method == "GET")
        else:
            self.refuse_method("GET, HEAD")

    def refuse_method(self, allowed: str) -> None:
        self.close_connection = True
        self.send_response(405)
        self.send_header("Allow", allowed)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def redirect(self, location: str, cookie: str | None = None) -> None:
        """A 303 to a relative URL, so it works under any path prefix."""
        self.send_response(303)
        self.send_header("Location", location)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ----------------------------------------------------------- sessions --

    def peer(self) -> str:
        return str(self.client_address[0])

    def client(self) -> str:
        # A malformed request line is logged before any header is parsed.
        headers = getattr(self, "headers", None)
        forwarded = headers.get("X-Forwarded-For") if headers is not None else None
        return auth.client_address(self.peer(), forwarded, self.trusted)

    def https(self) -> bool:
        return auth.forwarded_https(
            self.peer(), self.headers.get("X-Forwarded-Proto"), self.trusted
        )

    def session_user(self) -> str | None:
        creds = self.store.get()
        if creds is None:
            return None
        for value in cookie_values(self.headers.get_all("Cookie") or []):
            user = auth.check_cookie(creds, value, now=self.now())
            if user is not None:
                return user
        return None

    def cookie(self, value: str, max_age: int) -> str:
        # No Path: the browser then scopes the cookie to the directory of the
        # URL it posted to, /videogen/ behind the proxy and / on the LAN. The
        # server sees only the stripped path, so it cannot name that itself.
        attrs = f"{auth.COOKIE}={value}; Max-Age={max_age}; HttpOnly; SameSite=Lax"
        return attrs + ("; Secure" if self.https() else "")

    def send_login(self, status: int, message: str | None = None) -> None:
        data = login_page(message)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", LOGIN_CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def read_form(self) -> dict[str, str] | None:
        """The urlencoded form, or None after an error response."""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        length_text = self.headers.get("Content-Length")
        if ctype != "application/x-www-form-urlencoded":
            self.close_connection = True
            self.send_plain(415, "send a form")
            return None
        if length_text is None or not length_text.isdigit():
            self.close_connection = True
            self.send_plain(411, "length required")
            return None
        length = int(length_text)
        if length > MAX_FORM:
            self.close_connection = True
            self.send_plain(413, "form too large")
            return None
        raw = self.rfile.read(length)
        try:
            pairs = urllib.parse.parse_qsl(
                raw.decode("utf-8"), keep_blank_values=True, max_num_fields=8
            )
        except (UnicodeDecodeError, ValueError):
            self.send_plain(400, "bad form")
            return None
        return dict(pairs)

    def login(self) -> None:
        client = self.client()
        form = self.read_form()
        if form is None:
            return
        left = self.lockout.locked(client)
        if left > 0:
            logger.warning("login refused from %s: locked for %.0f s", client, left)
            self.send_login_locked(left)
            return
        creds = self.store.get()
        if creds is None:
            logger.error("login refused from %s: the credentials file is bad", client)
            self.send_plain(503, "login is not available")
            return
        user = auth.verify(creds, form.get("username", ""), form.get("password", ""))
        if user is None:
            locked = self.lockout.fail(client)
            logger.warning(
                "login failed from %s%s", client, "; now locked out" if locked else ""
            )
            # This attempt was checked, so it gets the plain answer; the lock
            # applies from the next one.
            self.send_login(401, "Wrong username or password.")
            return
        self.lockout.success(client)
        logger.info("login ok for %s from %s", user, client)
        value = auth.make_cookie(creds, user, now=self.now())
        self.redirect("./", self.cookie(value, auth.SESSION_S))

    def send_login_locked(self, seconds: float) -> None:
        minutes = max(1, round(seconds / 60))
        data = login_page(f"Too many failed logins. Try again in {minutes} min.")
        self.send_response(429)
        self.send_header("Retry-After", str(max(1, round(seconds))))
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", LOGIN_CSP)
        self.end_headers()
        self.wfile.write(data)

    def logout(self) -> None:
        logger.info("logout from %s", self.client())
        self.redirect("login", self.cookie("", 0))

    # -------------------------------------------------------------- files --

    def csp(self) -> str:
        try:
            text = (self.root / CSP_FILE).read_text().strip()
        except OSError:
            return FALLBACK_CSP
        return text or FALLBACK_CSP

    def send_plain(
        self, status: int, text: str, extra: dict[str, str] | None = None
    ) -> None:
        data = (text + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def serve(self, *, body: bool) -> None:
        try:
            real = resolve_request(self.path, self.root, self.allow)
        except Refused as err:
            logger.warning("refused %r: %s", self.path, err)
            reason = {403: "forbidden", 400: "bad request"}.get(err.status, "not found")
            self.send_plain(err.status, reason)
            return
        except UnicodeDecodeError:
            self.send_plain(400, "bad request")
            return
        try:
            fh = real.open("rb")
        except OSError:
            self.send_plain(404, "not found")
            return
        with fh:
            # Size and time from the open file, not the path: the builder
            # can swap the link between a stat and an open.
            st = os.fstat(fh.fileno())
            self.send_file(fh, st, TYPES[real.suffix.lower()], body=body)

    def send_file(
        self, fh: BinaryIO, st: os.stat_result, ctype: str, *, body: bool
    ) -> None:
        size = st.st_size
        first, last, status = 0, size - 1, 200
        header = self.headers.get("Range")
        if header:
            try:
                span = parse_range(header, size)
            except ValueError:
                self.send_plain(
                    416, "range not satisfiable", {"Content-Range": f"bytes */{size}"}
                )
                return
            if span is not None:
                first, last = span
                status = 206
        length = max(0, last - first + 1)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
        # private: a shared cache must not keep what a login protects.
        self.send_header("Cache-Control", "private, no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", self.csp())
        self.send_header("Last-Modified", self.date_time_string(int(st.st_mtime)))
        self.end_headers()
        if not body or length == 0:
            return
        try:
            sent = copy_span(fh, self.wfile, first, length)
        except OSError:
            # A browser drops a range request as soon as it seeks again; a
            # stalled client times out. Either way this connection is done.
            logger.debug("client closed or timed out on %s", self.path)
            self.close_connection = True
            return
        if sent < length:
            # The file shrank under us: the promised length was not sent, so
            # the connection cannot carry another response.
            logger.warning("short read on %s: %d of %d bytes", self.path, sent, length)
            self.close_connection = True


class GalleryServer(http.server.ThreadingHTTPServer):
    """A threading server with a cap on open connections."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[Handler],
        max_connections: int = MAX_CONNECTIONS,
    ) -> None:
        super().__init__(address, handler)
        self.slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            logger.warning("refused %s: too many open connections", client_address)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def make_server(
    directory: pathlib.Path,
    allow: pathlib.Path,
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
    *,
    auth_file: pathlib.Path = auth.DEFAULT_AUTH_FILE,
    host_names: tuple[str, ...] = (),
    trusted_proxies: tuple[str, ...] = (),
    timeout: float = IDLE_TIMEOUT_S,
    max_connections: int = MAX_CONNECTIONS,
    lockout: auth.Lockout | None = None,
) -> GalleryServer:
    """A bound (not yet serving) server.

    Raises ValueError for a bad layout, and AuthError for a missing or unsafe
    credentials file: there is no open mode.
    """
    root = directory.expanduser().resolve()
    allow_root = allow.expanduser().resolve()
    if not root.is_relative_to(allow_root):
        raise ValueError(f"{root} is not inside the allowed root {allow_root}")
    if not root.is_dir():
        logger.warning("%s does not exist yet; run gallery.py to build it", root)
    creds = auth.load_auth(auth_file.expanduser())
    store = auth.AuthStore(auth_file.expanduser())
    trusted = auth.normalize_proxies(trusted_proxies)
    names = frozenset(n.strip().rstrip(".").lower() for n in host_names if n.strip())
    handler = type(
        "GalleryHandler",
        (Handler,),
        {
            "root": root,
            "allow": allow_root,
            "host_names": names,
            "trusted": trusted,
            "store": store,
            "lockout": lockout or auth.Lockout(),
            "timeout": timeout,
        },
    )
    logger.info(
        "credentials from %s (%d user); trusted proxies: %s",
        auth_file,
        len(creds.users),
        ", ".join(sorted(trusted)) or "none",
    )
    return GalleryServer((host, port), handler, max_connections)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", type=pathlib.Path, default=DEFAULT_DIR)
    p.add_argument(
        "--allow",
        type=pathlib.Path,
        default=DEFAULT_ROOT,
        help="refuse any file whose real path is outside this tree",
    )
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument(
        "--host-name",
        action="append",
        default=[],
        help="a Host name to answer, besides IP literals and localhost (repeatable)",
    )
    p.add_argument(
        "--trusted-proxy",
        action="append",
        default=[],
        help="a reverse proxy's IP whose X-Forwarded-For and -Proto are believed "
        "(repeatable; default none)",
    )
    p.add_argument("--auth-file", type=pathlib.Path, default=auth.DEFAULT_AUTH_FILE)
    return p


def build_set_password_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gallery_serve.py set-password",
        description="Set the gallery's one user and password. The password "
        "comes from stdin (no echo on a terminal). A new session secret ends "
        "every open session.",
    )
    p.add_argument("--user", required=True)
    p.add_argument("--auth-file", type=pathlib.Path, default=auth.DEFAULT_AUTH_FILE)
    return p


def set_password(argv: list[str]) -> int:
    args = build_set_password_parser().parse_args(argv)
    path = args.auth_file.expanduser()
    try:
        password = auth.read_password(sys.stdin)
        auth.write_auth(path, args.user, password)
        auth.load_auth(path)  # read it back: a write that is not read is a guess
    except auth.AuthError as err:
        logger.error("%s", err)
        return 2
    logger.info(
        "wrote %s for user %s; every open session has ended. Restart is not "
        "needed: the server reads the file again.",
        path,
        args.user,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["set-password"]:
        return set_password(argv[1:])
    args = build_parser().parse_args(argv)
    try:
        server = make_server(
            args.dir,
            args.allow,
            args.host,
            args.port,
            auth_file=args.auth_file,
            host_names=tuple(args.host_name),
            trusted_proxies=tuple(args.trusted_proxy),
        )
    except (ValueError, OSError, auth.AuthError) as err:
        logger.error("%s", err)
        return 2
    host, port = server.server_address[:2]
    logger.info(
        "serving %s on http://%s:%s/ (pid %d; host names: %s)",
        args.dir,
        host,
        port,
        os.getpid(),
        ", ".join(sorted(args.host_name)) or "IP and localhost only",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
