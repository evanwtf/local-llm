#!/usr/bin/env python3
"""Make Claude Code cheap against a local ds4-server (issue #50).

Claude Code talks the Anthropic protocol, which ds4-server speaks natively, so
no shim is needed for *correctness*. This one exists for *cost*. Tracing a real
agent trial (`ds4-server --trace`) showed two client behaviours that together
account for most of the wall time:

1. **`thinking: {"type": "adaptive"}`.** `ds4-server --help thinking` recognises
   `{"type":"disabled"}`, `think=false` and `model=deepseek-chat`. It does not
   recognise `adaptive`, so those requests fall through to the documented
   default -- high-effort thinking -- and every tool-bearing request in the
   trace ran `think_mode: high`. On one trivial prompt that is 295 output
   tokens against 12 with thinking off.

   **Superseded by #63.** Those cheap tokens were wrong tokens: thinking off
   scores 4/8 on trivial functions against 8/8 for on. The rewrite now sends
   `enabled`, which matches ds4's own default. This shim no longer changes
   what the model does -- it only stops `adaptive` from being a silent,
   undeclared fall-through, so every run records the mode it used.

2. **A live token counter injected as a system message.** Claude Code sends

       {"role":"system","content":"<total_tokens>14969546 tokens left</total_tokens>"}

   with `cache_control: {"type":"ephemeral"}`. The number changes every turn
   (8 distinct values across 14 requests) and sits at a fixed position, so the
   KV prefix diverges there and never recovers: `live_prompt_common` pinned at
   20,321 while the prompt grew to 30,781, re-prefilling ~10k tokens per turn.

   The `ephemeral` marker means Claude Code expects Anthropic's cache
   semantics, where such a block is excluded from the cached prefix. ds4 does
   not implement that, so a block meant to be cache-friendly poisons the cache.
   Pinning the value -- rather than deleting the block -- keeps the prompt
   structurally identical every turn, which is what the KV matcher needs.

Both rewrites are opt-out so the effect of each can be measured separately.

    ./ds4_claude_shim.py --port 8100 --upstream http://127.0.0.1:8000

Responses stream through unbuffered: Claude Code sets `stream: 1` on every
agent request, and buffering an SSE body would change the timings this exists
to improve.
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import re
import sys
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8100
DEFAULT_UPSTREAM = "http://127.0.0.1:8000"

# Matches the counter Claude Code injects. The digits are what churn.
COUNTER = re.compile(r"<total_tokens>\s*[\d,]+\s*tokens left</total_tokens>")
COUNTER_PINNED = "<total_tokens>0 tokens left</total_tokens>"

upstream = DEFAULT_UPSTREAM
rewrite_thinking = True
pin_counter = True
stats = {"thinking": 0, "counter": 0, "requests": 0}


def normalise_thinking(payload: dict) -> bool:
    """Turn a thinking mode ds4 does not know into one it does.

    Returns True if the payload changed. Only `adaptive` is rewritten: an
    explicit `disabled` or `enabled` from the client is a deliberate choice and
    is left alone.

    `adaptive` becomes `enabled`, not `disabled`. This shim originally chose
    `disabled` to cut tokens (#50). #63 measured what that cost: across 8
    trivial functions, thinking off scored **4/8** against **8/8** for on, and
    failed `fib(10)` and reversing a string. It was not reliably cheaper
    either -- on one task off spent 548 tokens to on's 431 and was still
    wrong. Median saving was 30% of tokens and three seconds, for half the
    correct answers.

    ds4's own default for an unrecognised mode is high-effort thinking, so
    `enabled` is also what the request would have got with no shim at all.
    """
    thinking = payload.get("thinking")
    if not isinstance(thinking, dict) or thinking.get("type") != "adaptive":
        return False
    payload["thinking"] = {"type": "enabled"}
    return True


def _pin_in_text(text: str) -> tuple[str, int]:
    return COUNTER.subn(COUNTER_PINNED, text)


def pin_token_counter(payload: dict) -> bool:
    """Freeze the injected token counter so the KV prefix stops churning.

    The block is kept, not dropped: removing it would shift every later token
    and change the prompt the model sees more than pinning does.
    """
    changed = 0
    blocks: list[object] = []
    system = payload.get("system")
    if isinstance(system, (str, list)):
        blocks.append(("system", system))
    for message in payload.get("messages", []):
        if isinstance(message, dict):
            blocks.append(("message", message.get("content")))

    def fix(content: object) -> tuple[object, int]:
        if isinstance(content, str):
            return _pin_in_text(content)
        if isinstance(content, list):
            total = 0
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    block["text"], n = _pin_in_text(block["text"])
                    total += n
            return content, total
        return content, 0

    if isinstance(system, (str, list)):
        payload["system"], n = fix(system)
        changed += n
    for message in payload.get("messages", []):
        if isinstance(message, dict):
            message["content"], n = fix(message.get("content"))
            changed += n
    return changed > 0


def rewrite(body: bytes) -> bytes:
    """Apply the enabled rewrites. Returns the original body on any doubt."""
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict):
        return body

    touched = False
    if rewrite_thinking and normalise_thinking(payload):
        stats["thinking"] += 1
        touched = True
    if pin_counter and pin_token_counter(payload):
        stats["counter"] += 1
        touched = True
    if not touched:
        return body
    return json.dumps(payload).encode()


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return  # the access log is noise; we log what we rewrite instead

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        stats["requests"] += 1
        body = rewrite(body)
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length", "accept-encoding")
        }
        request = urllib.request.Request(
            upstream + self.path, body, headers, method="POST"
        )
        try:
            response = urllib.request.urlopen(request, timeout=1800)
        except urllib.error.HTTPError as err:
            data = err.read()
            self.send_response(err.code)
            self.send_header(
                "Content-Type", err.headers.get("Content-Type", "application/json")
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            logger.error("%s -> %s: %s", self.path, err.code, data[:200])
            return

        # Stream through. Claude Code sets stream:1 on every agent request, so
        # buffering here would distort exactly what this shim exists to fix.
        self.send_response(response.status)
        for key, value in response.headers.items():
            if key.lower() in ("content-length", "transfer-encoding", "connection"):
                continue
            self.send_header(key, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        finally:
            response.close()

    def do_GET(self) -> None:
        try:
            with urllib.request.urlopen(upstream + self.path, timeout=60) as response:
                data = response.read()
                code, ctype = (
                    response.status,
                    response.headers.get("Content-Type", "application/json"),
                )
        except urllib.error.HTTPError as err:
            data, code, ctype = err.read(), err.code, "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    global upstream, rewrite_thinking, pin_counter
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    parser.add_argument(
        "--keep-thinking",
        action="store_true",
        help="do not rewrite adaptive -> disabled",
    )
    parser.add_argument(
        "--keep-counter",
        action="store_true",
        help="do not pin the injected token counter",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    upstream = args.upstream
    rewrite_thinking = not args.keep_thinking
    pin_counter = not args.keep_counter
    logger.info(
        "ds4 shim on :%d -> %s (thinking rewrite=%s, counter pinning=%s)",
        args.port,
        upstream,
        rewrite_thinking,
        pin_counter,
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Proxy)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("stats: %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
