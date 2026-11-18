"""Serve the video-gen gallery read-only on the LAN, with HTTP Range for video seeking (#1413).

    gallery_serve.py [--dir ~/bench-logs/videogen/gallery] [--port 8043]
                     [--host-name NAME ...]

The server uses only the Python standard library. It:

- answers GET and HEAD only. Every other method gets 405, so nothing can
  write through it.
- answers only a request whose Host header is an IP literal, `localhost`,
  or a name given with `--host-name` (repeatable). Any other Host gets 421.
  This stops a DNS-rebinding page in a LAN browser from reading the gallery
  under a name the attacker controls.
- maps a URL path only to a file under `--dir`. It refuses a `..` segment, a
  hidden name (a leading dot, such as the builder's temp files and `.csp`),
  a directory, and a file type that is not on its list. The link name and
  the real file must have the same type.
- resolves every symlink and refuses a file whose real path is outside
  `--allow` (default `~/bench-logs/videogen/`). The gallery's media links
  point into that tree; a link that leaves it gets 403.
- supports a single `Range: bytes=` request with 206 and `Content-Range`.
  The stdlib `SimpleHTTPRequestHandler` does not, and without Range a
  browser cannot seek in a video.
- sends the Content-Security-Policy that `gallery.py` writes beside the page
  (`.csp`, with the sha256 of the page's inline CSS and JS), so a rebuilt
  page and its policy always match without a restart.
- closes a connection that is idle for 30 s, and serves at most 64
  connections at once; it closes any connection over that cap at once.

It binds 0.0.0.0 on port 8043 by default: LAN only, with no login. Never
forward the port to the internet. `systemd/local-llm-videogen-gallery.service`
runs it as a user service; `scripts/videogen/README.md` has the install
commands.
"""

from __future__ import annotations

import argparse
import http.server
import io
import ipaddress
import logging
import os
import pathlib
import re
import sys
import threading
import urllib.parse
from typing import Any, BinaryIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import DEFAULT_ROOT
from videogen.gallery import CSP_FILE

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
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

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


class Handler(http.server.BaseHTTPRequestHandler):
    """GET and HEAD for files under the root; 405 for anything else."""

    server_version = "videogen-gallery"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    #: StreamRequestHandler applies this to the socket: an idle client is
    #: closed instead of holding a thread for ever.
    timeout = IDLE_TIMEOUT_S
    root: pathlib.Path
    allow: pathlib.Path
    host_names: frozenset[str] = frozenset()

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s %s", self.address_string(), format % args)

    def host_ok(self) -> bool:
        """Refuse a Host header we do not know (DNS rebinding); 421."""
        host = self.headers.get("Host")
        if host_allowed(host, self.host_names):
            return True
        logger.warning("refused Host %r for %r", host, self.path)
        self.send_plain(421 if host else 400, "unknown host")
        return False

    def refuse_method(self) -> None:
        if not self.host_ok():
            return
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = refuse_method
    do_TRACE = do_CONNECT = do_PROPFIND = do_MKCOL = do_COPY = do_MOVE = refuse_method

    def do_HEAD(self) -> None:
        self.serve(body=False)

    def do_GET(self) -> None:
        self.serve(body=True)

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
        if not self.host_ok():
            return
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
        self.send_header("Cache-Control", "no-cache")
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
    host_names: tuple[str, ...] = (),
    timeout: float = IDLE_TIMEOUT_S,
    max_connections: int = MAX_CONNECTIONS,
) -> GalleryServer:
    """A bound (not yet serving) server. Raises ValueError for a bad layout."""
    root = directory.expanduser().resolve()
    allow_root = allow.expanduser().resolve()
    if not root.is_relative_to(allow_root):
        raise ValueError(f"{root} is not inside the allowed root {allow_root}")
    if not root.is_dir():
        logger.warning("%s does not exist yet; run gallery.py to build it", root)
    names = frozenset(n.strip().rstrip(".").lower() for n in host_names if n.strip())
    handler = type(
        "GalleryHandler",
        (Handler,),
        {"root": root, "allow": allow_root, "host_names": names, "timeout": timeout},
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
    return p


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    args = build_parser().parse_args(argv)
    try:
        server = make_server(
            args.dir, args.allow, args.host, args.port, host_names=tuple(args.host_name)
        )
    except (ValueError, OSError) as err:
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
