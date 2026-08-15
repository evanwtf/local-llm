#!/usr/bin/env python3
"""Make Claude Code work against Ollama's Anthropic-compatible endpoint.

Claude Code appends a role="system" message to the END of the messages array.
It carries the agent-type listing for the Agent tool. Ollama accepts system
content only in the top-level `system` field, so it rejects the request before
the model runs:

    API Error: 500 system message must be at the beginning

This proxy hoists any stray system message into `system` and forwards the
request unchanged. Everything else passes through untouched.

Usage:

    ./ollama_claude_shim.py &
    ./claude-ollama

PRIVACY: --dump-failures keeps the body of any request Ollama still rejects.
Those dumps hold the full prompt -- your CLAUDE.md and every file the agent
read. Never commit them; .gitignore blocks fail-*.json.
"""
import argparse
import http.server
import json
import logging
import pathlib
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_PORT = 11500
DEFAULT_UPSTREAM = "http://127.0.0.1:11434"

# Set from argv in main().
upstream = DEFAULT_UPSTREAM
dump_dir: pathlib.Path | None = None
fails = 0


def hoist_system(body: bytes) -> bytes:
    """Move role="system" messages into the top-level `system` field.

    Returns the rewritten body, or the original if there is nothing to do.
    Order is preserved: hoisted blocks go after the existing system blocks, so
    the agent listing still follows the main prompt.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return body
    strays = [m for m in messages if m.get("role") == "system"]
    if not strays:
        return body

    existing = payload.get("system")
    if existing is None:
        blocks = []
    elif isinstance(existing, str):
        blocks = [{"type": "text", "text": existing}]
    else:
        blocks = list(existing)

    for message in strays:
        content = message.get("content")
        if isinstance(content, str):
            blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            # Drop cache_control. These blocks move to a new position, so a
            # cache breakpoint carried along with them would be meaningless.
            blocks += [
                {k: v for k, v in b.items() if k != "cache_control"}
                for b in content
                if b.get("type") == "text"
            ]

    payload["system"] = blocks
    payload["messages"] = [m for m in messages if m.get("role") != "system"]
    logger.info("hoisted %d system message(s) into `system`", len(strays))
    return json.dumps(payload).encode()


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        global fails
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = hoist_system(body)
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length", "accept-encoding")
        }
        request = urllib.request.Request(
            upstream + self.path, body, headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=1800) as response:
                data = response.read()
                code = response.status
                ctype = response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as err:
            data = err.read()
            code = err.code
            ctype = err.headers.get("Content-Type", "application/json")
            self.report_failure(body, code)

        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def report_failure(self, body: bytes, code: int):
        """Log the message shape, which is usually enough to spot the problem."""
        global fails
        fails += 1
        try:
            payload = json.loads(body)
        except ValueError:
            logger.error("%s -> %s (body is not JSON)", self.path, code)
            return

        logger.error("%s -> %s", self.path, code)
        system = payload.get("system")
        logger.error(
            "  system: %s", type(system).__name__ if system is not None else "absent"
        )
        for i, message in enumerate(payload.get("messages", [])):
            content = message.get("content")
            kinds = (
                [b.get("type") for b in content if isinstance(b, dict)]
                if isinstance(content, list)
                else ["<str>"]
            )
            logger.error("  [%d] role=%-9s %s", i, message.get("role"), kinds)

        if dump_dir is not None:
            path = dump_dir / f"fail-{fails}.json"
            path.write_text(json.dumps(payload, indent=2))
            logger.error("  wrote %s (contains the full prompt)", path)

    def do_GET(self):
        try:
            with urllib.request.urlopen(upstream + self.path, timeout=60) as response:
                data = response.read()
                code = response.status
        except urllib.error.HTTPError as err:
            data = err.read()
            code = err.code
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        """Silence the default per-request stderr line."""


def main():
    global upstream, dump_dir
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    parser.add_argument(
        "--dump-failures",
        metavar="DIR",
        help="write rejected request bodies here. They contain the full "
        "prompt, including your CLAUDE.md. Never commit them.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    upstream = args.upstream
    if args.dump_failures:
        dump_dir = pathlib.Path(args.dump_failures)
        dump_dir.mkdir(parents=True, exist_ok=True)
        logger.warning("dumping failed requests to %s -- these hold full prompts", dump_dir)

    logger.info("listening on 127.0.0.1:%d -> %s", args.port, upstream)
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Proxy).serve_forever()


if __name__ == "__main__":
    main()
