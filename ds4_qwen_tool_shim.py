"""Tell Qwen3.8-Flash-Next which tool-call dialect ds4 accepts (issue #94).

ds4 renders a correct, standard Qwen tool prompt -- "output a JSON object with
name and arguments inside <tool_call></tool_call> tags" -- and the model
ignores it roughly half the time, emitting the XML dialect instead:

    <tool_call><function=read><parameter=filePath>/tmp/x.py</parameter></function></tool_call>

ds4 detects that, appends a model-visible tool error, and lets the model retry.
The model then apologises and emits correct JSON. But **ds4 retries exactly
once**: when the second attempt is also XML it gives up and returns the text as
an ordinary assistant message with finish=stop. OpenCode sees a plain reply on
turn one and ends the trial having written nothing.

That is not a capability limit and not a slow model. It cost a full 45-trial
run: 15 tasks x 3 trials, 0 passed, `num_turns=1` and `solution_empty=true` on
every row, while the engine underneath was doing 40 t/s decode and 1107 t/s
prefill.

Naming the format in the system message fixes it -- measured 10/10 against a
8/10 baseline on the same prompts and sizes.

**This is a confound and must be recorded as one.** A backend behind this shim
runs with one system line no other backend gets. It names a serialisation
format and gives no help with the task, which is why it is defensible at all --
it is closer to a chat template than to a hint. It is not free, and a row
produced through it is not directly comparable to one that was not.

Note also that `max_tokens` matters: the retry costs a ~60-token continuation
plus a fresh attempt, so a small cap starves it. At max_tokens=120 the baseline
measured 4/8; at 300, 8/10. Do not reproduce this with a small cap and conclude
the model is worse than it is.

    uv run python ds4_qwen_tool_shim.py --port 8101 --upstream http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import sys
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8101
DEFAULT_UPSTREAM = "http://127.0.0.1:8000"

# Named after what it does, not after the model: the JSON shape is ds4's
# requirement, and the XML shape is what the model reaches for unprompted.
INSTRUCTION = (
    "\n\nWhen you call a function, you MUST output a JSON object with "
    '"name" and "arguments" keys inside <tool_call></tool_call> tags, '
    "for example:\n"
    '<tool_call>{"name": "read", "arguments": {"filePath": "/tmp/a.py"}}</tool_call>\n'
    "Never use XML-style <function=...> or <parameter=...> syntax."
)

upstream = DEFAULT_UPSTREAM
stats = {"requests": 0, "instructed": 0}


def needs_instruction(payload: dict) -> bool:
    """Only requests that actually offer tools, and only once.

    A request with no `tools` cannot emit a tool call, so instructing it would
    add prompt tokens and change the KV prefix for nothing. Re-appending on a
    payload that already carries the text would grow the prompt on every turn
    of a long conversation.
    """
    if not payload.get("tools"):
        return False
    # Compare against the raw text, NOT json.dumps(messages): dumps escapes the
    # newlines in INSTRUCTION, so the "already there" check never matched and
    # the instruction was re-appended on every turn -- unbounded prompt growth
    # and a KV prefix that moves each time, which reads as the model slowing
    # down. Caught by test_it_is_idempotent.
    marker = INSTRUCTION.strip().splitlines()[0]
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and marker in content:
            return False
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and marker in str(block.get("text") or ""):
                    return False
    return True


def add_instruction(payload: dict) -> bool:
    """Append the format instruction to the system message. True if changed.

    Appends to the FIRST system message rather than inserting a new one: the
    prompt cache keys on the prefix, and a new leading message would invalidate
    it on every request.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = content + INSTRUCTION
            return True
        # Anthropic-style content blocks: append to the last text block.
        if isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    block["text"] = block["text"] + INSTRUCTION
                    return True
        return False
    # No system message at all: give it one rather than silently doing nothing.
    messages.insert(0, {"role": "system", "content": INSTRUCTION.strip()})
    return True


def rewrite(body: bytes) -> bytes:
    """Apply the rewrite. Returns the original body on any doubt."""
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict) or not needs_instruction(payload):
        return body
    if not add_instruction(payload):
        return body
    stats["instructed"] += 1
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
    global upstream
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    args = parser.parse_args()
    upstream = args.upstream.rstrip("/")
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Proxy)
    logger.info("qwen tool shim on :%d -> %s", args.port, upstream)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("stats: %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
