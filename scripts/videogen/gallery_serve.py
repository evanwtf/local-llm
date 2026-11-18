"""Serve the video-gen gallery read-only on the LAN, with HTTP Range for video seeking (#1413).

    gallery_serve.py [--dir ~/bench-logs/videogen/gallery] [--port 8043]

The server uses only the Python standard library. It:

- answers GET and HEAD only. Every other method gets 405, so nothing can
  write through it.
- maps a URL path only to a file under `--dir`. It refuses a `..` segment, a
  hidden name (a leading dot, such as the builder's temp files), a directory
  and a file type that is not on its list.
- resolves every symlink and refuses a file whose real path is outside
  `--allow` (default `~/bench-logs/videogen/`). The gallery's media links
  point into that tree; a link that leaves it gets 403.
- supports a single `Range: bytes=` request with 206 and `Content-Range`.
  The stdlib `SimpleHTTPRequestHandler` does not, and without Range a
  browser cannot seek in a video.

It binds 0.0.0.0 on port 8043 by default: LAN only, with no login. Never
forward the port to the internet. `systemd/local-llm-videogen-gallery.service`
runs it as a user service; `scripts/videogen/README.md` has the install
commands.
"""

from __future__ import annotations

import argparse
import http.server
import io
import logging
import os
import pathlib
import posixpath
import re
import sys
import urllib.parse
from typing import BinaryIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import DEFAULT_ROOT

import logs

logger = logging.getLogger(__name__)

#: 8040 is H3 and 8041 is vLLM-Omni (Wan); 8042 is `dgx_server.py`'s ad-hoc
#: clip viewer. 8043 was free on the head and named nowhere in the repo.
#: `tests/test_videogen_gallery_serve.py` pins the unit file to this value.
DEFAULT_PORT = 8043
DEFAULT_DIR = DEFAULT_ROOT / "gallery"

#: Only these types are served. A stray run.json or frame is not.
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mp4": "video/mp4",
}

#: No external request can load, even if a prompt held markup.
CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "media-src 'self'; img-src 'self'"
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
    rel = posixpath.normpath("/".join(parts))
    candidate = root.joinpath(*rel.split("/"))
    try:
        real = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as err:
        raise Refused(404, "no such file") from err
    if not real.is_relative_to(allow):
        raise Refused(403, f"resolves outside {allow}")
    if not real.is_file():
        raise Refused(404, "not a file")
    if candidate.suffix.lower() not in TYPES:
        raise Refused(404, "a file type this server does not serve")
    return real


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """(first, last) inclusive for one `bytes=` range; None to send it all.

    Raises ValueError for a range that cannot be satisfied (416).
    """
    match = RANGE.match(header.strip())
    if match is None:
        # Several ranges or another unit: RFC 9110 lets a server ignore it.
        return None
    first_s, last_s = match.groups()
    if not first_s and not last_s:
        return None
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


class Handler(http.server.BaseHTTPRequestHandler):
    """GET and HEAD for files under the root; 405 for anything else."""

    server_version = "videogen-gallery"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    root: pathlib.Path
    allow: pathlib.Path

    def log_message(self, format: str, *args: object) -> None:
        logger.info("%s %s", self.address_string(), format % args)

    def refuse_method(self) -> None:
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

    def send_plain(
        self, status: int, text: str, extra: dict[str, str] | None = None
    ) -> None:
        data = (text + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
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
            self.send_plain(
                err.status,
                {403: "forbidden", 400: "bad request"}.get(err.status, "not found"),
            )
            return
        except UnicodeDecodeError:
            self.send_plain(400, "bad request")
            return
        size = real.stat().st_size
        ctype = TYPES[real.suffix.lower()]
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
        self.send_header("Content-Security-Policy", CSP)
        self.send_header(
            "Last-Modified", self.date_time_string(int(real.stat().st_mtime))
        )
        self.end_headers()
        if not body or length == 0:
            return
        try:
            with real.open("rb") as fh:
                copy_span(fh, self.wfile, first, length)
        except (BrokenPipeError, ConnectionResetError):
            # A browser drops a range request as soon as it seeks again.
            logger.debug("client closed %s", self.path)


def copy_span(src: BinaryIO, dst: io.BufferedIOBase, first: int, length: int) -> None:
    src.seek(first)
    left = length
    while left > 0:
        block = src.read(min(CHUNK, left))
        if not block:
            break
        dst.write(block)
        left -= len(block)


def make_server(
    directory: pathlib.Path,
    allow: pathlib.Path,
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
) -> http.server.ThreadingHTTPServer:
    """A bound (not yet serving) server. Raises ValueError for a bad layout."""
    root = directory.expanduser().resolve()
    allow_root = allow.expanduser().resolve()
    if not root.is_relative_to(allow_root):
        raise ValueError(f"{root} is not inside the allowed root {allow_root}")
    if not root.is_dir():
        logger.warning("%s does not exist yet; run gallery.py to build it", root)
    handler = type("GalleryHandler", (Handler,), {"root": root, "allow": allow_root})
    server = http.server.ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


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
    return p


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    args = build_parser().parse_args(argv)
    try:
        server = make_server(args.dir, args.allow, args.host, args.port)
    except (ValueError, OSError) as err:
        logger.error("%s", err)
        return 2
    host, port = server.server_address[:2]
    logger.info(
        "serving %s on http://%s:%s/ (pid %d)", args.dir, host, port, os.getpid()
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
