"""scripts/record_proxy.py: a byte-exact proxy that records chat exchanges (#1168)."""

from __future__ import annotations

import http.client
import json
import pathlib
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import record_proxy

SSE_FRAMES = [
    b'data: {"choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"think "}}]}\n',
    b"\n",
    b'data: {"choices":[{"index":0,"delta":{"reasoning_content":"more"}}]}\n',
    b"\n",
    b'data: {"choices":[{"index":0,"delta":{"content":"Hi"}}]}\n',
    b"\n",
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read","arguments":"{\\"pa"}}]}}]}\n',
    b"\n",
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"th\\": 1}"}}]}}]}\n',
    b"\n",
    b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"completion_tokens":162}}\n',
    b"\n",
    b"data: [DONE]\n",
    b"\n",
]


def test_assemble_joins_content_reasoning_and_tool_calls() -> None:
    got = record_proxy.assemble_sse(SSE_FRAMES)
    assert got["content"] == "Hi"
    assert got["reasoning"] == "think more"
    assert got["tool_calls"] == [
        {"id": "c1", "name": "read", "arguments": '{"path": 1}'}
    ]
    assert got["finish_reason"] == "stop"
    assert got["usage"] == {"completion_tokens": 162}
    assert got["frames"] == 6


def test_assemble_reads_the_reasoning_key_too() -> None:
    frames = [b'data: {"choices":[{"delta":{"reasoning":"r"}}]}\n', b"data: [DONE]\n"]
    assert record_proxy.assemble_sse(frames)["reasoning"] == "r"


def test_assemble_keeps_unparseable_frames_as_a_count() -> None:
    frames = [b"data: {not json\n", b'data: {"choices":[{"delta":{"content":"x"}}]}\n']
    got = record_proxy.assemble_sse(frames)
    assert got["content"] == "x"
    assert got["bad_frames"] == 1


def test_assemble_an_empty_reply() -> None:
    frames = [
        b'data: {"choices":[{"delta":{"reasoning_content":"only thinking"},"finish_reason":"stop"}]}\n',
        b"data: [DONE]\n",
    ]
    got = record_proxy.assemble_sse(frames)
    assert got["content"] == ""
    assert got["tool_calls"] == []
    assert got["reasoning"] == "only thinking"


def test_assemble_json_reply() -> None:
    body = {
        "choices": [
            {
                "message": {
                    "content": "done",
                    "reasoning_content": "why",
                    "tool_calls": [
                        {"id": "c", "function": {"name": "edit", "arguments": "{}"}}
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"completion_tokens": 5},
    }
    got = record_proxy.assemble_json(json.dumps(body).encode())
    assert got["content"] == "done"
    assert got["reasoning"] == "why"
    assert got["tool_calls"] == [{"id": "c", "name": "edit", "arguments": "{}"}]
    assert got["finish_reason"] == "tool_calls"


class _Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length))
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for frame in SSE_FRAMES:
                self.wfile.write(frame)
                self.wfile.flush()
        else:
            data = json.dumps(
                {
                    "choices": [
                        {"message": {"content": "plain"}, "finish_reason": "stop"}
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def do_GET(self) -> None:
        data = b'{"data":[{"id":"m"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _serve(server: socketserver.BaseServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _post(port: int, body: dict) -> bytes:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST",
        "/v1/chat/completions",
        body=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )
    data = conn.getresponse().read()
    conn.close()
    return data


def test_proxy_passes_bytes_through_and_records(tmp_path: pathlib.Path) -> None:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    _serve(upstream)
    record = tmp_path / "exchanges.jsonl"
    proxy = record_proxy.make_server(0, "127.0.0.1", upstream.server_address[1], record)
    _serve(proxy)
    port = proxy.server_address[1]
    try:
        streamed = _post(port, {"model": "m", "stream": True, "messages": []})
        assert streamed == b"".join(SSE_FRAMES)
        plain = _post(
            port, {"model": "m", "messages": [{"role": "user", "content": "q"}]}
        )
        assert json.loads(plain)["choices"][0]["message"]["content"] == "plain"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/v1/models")
        assert conn.getresponse().read() == b'{"data":[{"id":"m"}]}'
        conn.close()
    finally:
        proxy.shutdown()
        upstream.shutdown()
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    assert len(rows) == 2  # chat exchanges only, not /v1/models
    assert rows[0]["request"]["stream"] is True
    assert rows[0]["reply"]["content"] == "Hi"
    assert rows[0]["reply"]["reasoning"] == "think more"
    assert rows[0]["status"] == 200
    assert rows[1]["reply"]["content"] == "plain"
    assert rows[1]["request"]["messages"] == [{"role": "user", "content": "q"}]
    assert "ts" in rows[0] and rows[0]["seconds"] >= 0
