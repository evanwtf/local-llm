"""Strip non-standard fields from a vLLM fork's chat responses so a strict
OpenAI-compatible client (OpenCode's `@ai-sdk/openai-compatible`) accepts them.

The styles01 Qwen3.8-Flash-Next fork (#331) returns valid HTTP 200 chat
completions, but decorates them with fields the AI-SDK's strict schema rejects,
so OpenCode throws "Unexpected server error" before the agent does any work:

  * message / delta: ``reasoning`` (the thinking text; present even thinking-off)
  * choice:          ``token_ids``
  * top level:       ``prompt_token_ids``, ``prompt_text``

This proxy sits in front of the server and removes exactly those keys from
`/v1/chat/completions` responses -- streaming and not -- and passes everything
else through untouched (``/v1/models``, ``/metrics``, request bodies). It runs
on the DGX in front of the containerised engine (default :8031 -> :8030); point
only OpenCode's provider at it, so run.py's own smoke and spec-metrics probes
still hit the engine directly.

    uv run python scripts/vllm_compat_proxy.py --listen 8031 --upstream 8030

Standard library only (no new dependency, #235/#368). The field list is the
whole contract; keep it in sync with what the fork actually emits.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import socketserver
from http.server import BaseHTTPRequestHandler

logger = logging.getLogger("vllm_compat_proxy")

# The non-standard keys, by where they appear in a chat.completion or
# chat.completion.chunk object. Everything not named here is passed through.
_TOP_LEVEL = ("prompt_token_ids", "prompt_text")
_CHOICE = ("token_ids",)
_MESSAGE = ("reasoning",)  # applies to both `message` (non-stream) and `delta`


def strip_completion(obj: dict) -> dict:
    """Remove the fork's non-standard fields from one parsed response object.

    Pure and in-place-safe: mutates and returns the same dict. Works for both
    `chat.completion` (a `message` per choice) and `chat.completion.chunk` (a
    `delta` per choice). Unknown shapes are left alone -- this must never turn a
    valid body into an invalid one.
    """
    if not isinstance(obj, dict):
        return obj
    for key in _TOP_LEVEL:
        obj.pop(key, None)
    choices = obj.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            for key in _CHOICE:
                choice.pop(key, None)
            for slot in ("message", "delta"):
                part = choice.get(slot)
                if isinstance(part, dict):
                    for key in _MESSAGE:
                        part.pop(key, None)
    return obj


def strip_sse_data(line: bytes) -> bytes:
    """Strip one Server-Sent-Events `data:` line; pass through anything else.

    `data: [DONE]`, blank lines and event/id lines are returned unchanged. A
    `data:` payload that is not JSON is returned unchanged (never break the
    stream over one odd frame).
    """
    if not line.startswith(b"data:"):
        return line
    payload = line[5:].strip()
    if not payload or payload == b"[DONE]":
        return line
    try:
        obj = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return line
    return b"data: " + json.dumps(strip_completion(obj)).encode() + b"\n"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_host = "127.0.0.1"
    upstream_port = 8030

    def log_message(self, *args):  # quiet; the engine already logs requests
        pass

    def _proxy(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        conn = http.client.HTTPConnection(
            self.upstream_host, self.upstream_port, timeout=600
        )
        # Forward headers except hop-by-hop / length (reset below).
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length", "connection")
        }
        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            self.send_error(502, f"upstream unreachable: {exc}")
            return

        ctype = resp.getheader("Content-Type") or ""
        rewrite = self.path.startswith("/v1/chat/completions") or self.path.startswith(
            "/v1/completions"
        )
        is_sse = "text/event-stream" in ctype

        if not rewrite:
            self._passthrough(resp)
        elif is_sse:
            self._stream_stripped(resp)
        else:
            self._buffer_stripped(resp)
        conn.close()

    def _passthrough(self, resp) -> None:
        data = resp.read()
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in ("transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _buffer_stripped(self, resp) -> None:
        raw = resp.read()
        try:
            out = json.dumps(strip_completion(json.loads(raw))).encode()
        except (ValueError, UnicodeDecodeError):
            out = raw  # not JSON after all; pass the bytes through
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in (
                "transfer-encoding",
                "connection",
                "content-length",
                "content-type",
            ):
                continue
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _stream_stripped(self, resp) -> None:
        self.send_response(resp.status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        # SSE frames are newline-delimited; rewrite each `data:` line as it
        # arrives and flush, so the client streams at engine speed.
        for line in resp:
            out = strip_sse_data(line if line.endswith(b"\n") else line + b"\n")
            self.wfile.write(out)
            self.wfile.flush()

    def do_POST(self):
        self._proxy("POST")

    def do_GET(self):
        self._proxy("GET")


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--listen", type=int, default=8031, help="local port to serve on")
    p.add_argument("--upstream", type=int, default=8030, help="engine port to proxy")
    p.add_argument("--upstream-host", default="127.0.0.1")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    _Handler.upstream_host = args.upstream_host
    _Handler.upstream_port = args.upstream
    logger.info(
        "vllm compat proxy: :%d -> %s:%d (stripping %s)",
        args.listen,
        args.upstream_host,
        args.upstream,
        list(_TOP_LEVEL + _CHOICE + _MESSAGE),
    )
    with _Server(("0.0.0.0", args.listen), _Handler) as srv:
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
