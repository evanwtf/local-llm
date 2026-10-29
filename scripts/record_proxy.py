"""Record every chat exchange between an agent client and a server, byte for byte.

#1168: some trials end in under 10 s with no tool call. The client stream kept
only a `step_start` event, and TensorFold logs only a hash of the reply, so
nothing showed what the model wrote. This proxy sits between the client and the
server and changes nothing: every byte of every response is forwarded as it
arrives. For each `/v1/chat/completions` request it appends one JSON line with
the request body, the status, the seconds taken, and the reply reassembled from
the stream (content, reasoning, tool calls, finish reason, usage).

    PORT=8889 ./start.sh                               # the server moves aside
    uv run python scripts/record_proxy.py --listen 8888 --upstream 8889 \
        --record ~/bench-logs/<run>/exchanges.jsonl

The record holds whole prompts: the client's system prompt and every file the
agent read. Keep it outside the repo, like any prompt capture (CONVENTIONS.md).

Standard library only (#235, #368). Unlike vllm_compat_proxy.py, it rewrites
nothing: a diagnostic that changed the bytes would change what it diagnoses.
"""

from __future__ import annotations

import argparse
import datetime
import http.client
import json
import logging
import pathlib
import socketserver
import sys
import threading
import time
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger("record_proxy")

#: Response headers the proxy sets itself.
_HOP = ("transfer-encoding", "connection", "content-length")


def _reasoning(part: dict[str, Any]) -> str:
    """Engines name the thinking text differently; read both keys."""
    value = part.get("reasoning_content") or part.get("reasoning") or ""
    return value if isinstance(value, str) else ""


def assemble_sse(frames: Iterable[bytes]) -> dict[str, Any]:
    """The reply a stream of `data:` frames adds up to, for the first choice."""
    content: list[str] = []
    reasoning: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    finish: str | None = None
    usage: Any = None
    count = bad = 0
    for frame in frames:
        if not frame.startswith(b"data:"):
            continue
        payload = frame[5:].strip()
        if not payload or payload == b"[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            bad += 1
            continue
        count += 1
        if obj.get("usage"):
            usage = obj["usage"]
        for choice in obj.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                content.append(delta["content"])
            reasoning.append(_reasoning(delta))
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(
                    int(tc.get("index", 0)), {"id": None, "name": "", "arguments": ""}
                )
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    out: dict[str, Any] = {
        "content": "".join(content),
        "reasoning": "".join(reasoning),
        "tool_calls": [calls[i] for i in sorted(calls)],
        "finish_reason": finish,
        "usage": usage,
        "frames": count,
    }
    if bad:
        out["bad_frames"] = bad
    return out


def assemble_json(raw: bytes) -> dict[str, Any]:
    """The same summary for a non-streamed `chat.completion` body."""
    try:
        obj = json.loads(raw)
        choice = obj["choices"][0]
        message = choice.get("message") or {}
    except (ValueError, UnicodeDecodeError, KeyError, IndexError, TypeError):
        return {"unparsed": raw[:2000].decode("utf-8", "replace")}
    calls = [
        {
            "id": tc.get("id"),
            "name": (tc.get("function") or {}).get("name") or "",
            "arguments": (tc.get("function") or {}).get("arguments") or "",
        }
        for tc in message.get("tool_calls") or []
    ]
    return {
        "content": message.get("content") or "",
        "reasoning": _reasoning(message),
        "tool_calls": calls,
        "finish_reason": choice.get("finish_reason"),
        "usage": obj.get("usage"),
    }


class _Recorder:
    """Appends one JSON line per exchange; one lock, so lines never interleave."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self.lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_host = "127.0.0.1"
    upstream_port = 8889
    recorder: _Recorder | None = None

    def log_message(self, *args: object) -> None:  # the engine logs requests
        pass

    def _proxy(self, method: str) -> None:
        start = time.monotonic()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length", "connection")
        }
        conn = http.client.HTTPConnection(
            self.upstream_host, self.upstream_port, timeout=1800
        )
        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            self.send_error(502, f"upstream unreachable: {exc}")
            return
        chat = method == "POST" and self.path.startswith("/v1/chat/completions")
        is_sse = "text/event-stream" in (resp.getheader("Content-Type") or "")
        if is_sse:
            frames = self._stream(resp)
            reply = assemble_sse(frames)
        else:
            raw = self._buffered(resp)
            reply = assemble_json(raw)
        conn.close()
        if chat and self.recorder is not None:
            try:
                request: Any = json.loads(body or b"{}")
            except (ValueError, UnicodeDecodeError):
                request = {"unparsed": (body or b"")[:2000].decode("utf-8", "replace")}
            self.recorder.write(
                {
                    "ts": datetime.datetime.now()
                    .astimezone()
                    .isoformat(timespec="seconds"),
                    "seconds": round(time.monotonic() - start, 3),
                    "status": resp.status,
                    "request": request,
                    "reply": reply,
                }
            )

    def _buffered(self, resp: http.client.HTTPResponse) -> bytes:
        data = resp.read()
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in _HOP:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return data

    def _stream(self, resp: http.client.HTTPResponse) -> list[bytes]:
        """Forward each line as it arrives, unchanged; keep a copy."""
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in _HOP:
                self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        kept: list[bytes] = []
        try:
            for line in resp:
                kept.append(line)
                self.wfile.write(line)
                self.wfile.flush()
        except OSError as exc:  # the client left mid-stream; keep what came
            logger.info("client left mid-stream after %d lines: %s", len(kept), exc)
        return kept

    def do_POST(self) -> None:
        self._proxy("POST")

    def do_GET(self) -> None:
        self._proxy("GET")


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(
    listen: int, upstream_host: str, upstream_port: int, record: pathlib.Path
) -> _Server:
    """A server on 0.0.0.0:`listen`, not yet serving. Port 0 picks one."""
    handler = type(
        "Handler",
        (_Handler,),
        {
            "upstream_host": upstream_host,
            "upstream_port": upstream_port,
            "recorder": _Recorder(record),
        },
    )
    return _Server(("0.0.0.0", listen), handler)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--listen", type=int, default=8888, help="port to serve on")
    p.add_argument("--upstream", type=int, default=8889, help="the server's port")
    p.add_argument("--upstream-host", default="127.0.0.1")
    p.add_argument(
        "--record",
        type=pathlib.Path,
        required=True,
        help="JSONL file to append each chat exchange to (keep it out of git)",
    )
    args = p.parse_args(argv)
    logs.configure()
    logger.info(
        "record proxy: :%d -> %s:%d, recording to %s",
        args.listen,
        args.upstream_host,
        args.upstream,
        args.record,
    )
    with make_server(
        args.listen, args.upstream_host, args.upstream, args.record.expanduser()
    ) as srv:
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
