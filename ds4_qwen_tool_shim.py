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

Naming the format in the system message fixes it **on synthetic prompts** --
measured 12/12 through this shim against 9/12 direct, and 5/5 against 2/5 with
ten tools declared.

**It does NOT fix it under OpenCode, and that is the open problem.** Against
OpenCode's real 26 KB system prompt, measured on the captured payload:

    no instruction                     0/6
    instruction (naming the XML too)   1/6
    instruction (positive shape only)  1/6

So the client's prompt, not prompt size and not tool count, is what drives this
model to the XML dialect -- and a system line barely dents it. OpenCode's prompt
contains no XML tool syntax of its own, so this is not imitation of an example.
The negation theory ("never use <function=...>" showing the model the very
syntax) is also dead: both variants measure the same.

**The real cause turned out to be the streaming path, not the dialect.**
Measured 2026-09-03 on one identical request, interleaved so session drift hit
both arms equally:

    stream:true    tool_calls  1/12    nothing at all  11/12
    stream:false   tool_calls  7/12    XML as text      5/12

ds4's own log says it is handing the failed call back as assistant text --
`invalid tool call returned as assistant text finish=stop [text_len=231 ...]`
-- and off-stream that text does arrive. On-stream it does not: the client gets
no content, no tool_calls, and finish=stop. OpenCode sets stream:true, which is
why the 45-trial run scored 0/45 while the same prompts answered off-stream.
The dialect coin-flip is real and underlies both arms, but it is not what made
the agent runs unrecoverable, and no amount of prompting could have fixed it.

So this shim does three things, and each is a confound to record:

1. appends the format instruction (helps the model emit JSON at all);
2. asks upstream for a **non-streaming** completion whenever the request
   carries tools, because that path does not drop the fallback;
3. **translates** the XML dialect into real tool_calls if it still appears,
   then synthesises the SSE stream the client asked for.

Step 2 is the one that matters. Step 3 makes step 2's remaining 5/12 usable.

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
import dataclasses
import http.server
import json
import logging
import os
import pathlib
import re
import secrets
import sys
import time
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
stats = {"requests": 0, "instructed": 0, "unstreamed": 0, "translated": 0}


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
        logger.warning(
            "could NOT add instruction; message roles=%s",
            [
                m.get("role")
                for m in payload.get("messages") or []
                if isinstance(m, dict)
            ],
        )
        return body
    stats["instructed"] += 1
    dump = os.environ.get("SHIM_DUMP")
    if dump and stats["instructed"] == 1:
        pathlib.Path(dump).write_text(json.dumps(payload, indent=1))
        logger.info("dumped first instructed payload to %s", dump)
    logger.info(
        "instructed request %d (tools=%d, messages=%d)",
        stats["instructed"],
        len(payload.get("tools") or []),
        len(payload.get("messages") or []),
    )
    return json.dumps(payload).encode()


# --- the XML dialect ------------------------------------------------------
#
# What the model actually emits when it loses the coin flip, copied off a ds4
# server log:
#
#     <tool_call>
#     <function=read>
#     <parameter=filePath>
#     /tmp/x.py
#     </parameter>
#     </function>
#     </tool_call>
#
# Note the values sit on their own lines. The surrounding whitespace is layout,
# not data, so it is stripped -- an unstripped "\n/tmp/x.py\n" is a path that
# does not exist, and the tool would fail for a second, unrelated reason.
XML_CALL = re.compile(
    r"<tool_call>\s*<function=(?P<name>[^>\s]+)\s*>(?P<body>.*?)</function>\s*</tool_call>",
    re.DOTALL,
)
XML_PARAM = re.compile(
    r"<parameter=(?P<key>[^>\s]+)\s*>(?P<value>.*?)</parameter>", re.DOTALL
)


def parse_xml_tool_calls(text: str) -> list[dict] | None:
    """Convert the XML dialect into OpenAI tool_calls. None if there is none.

    Returns None rather than an empty list so a caller cannot accidentally
    treat "no tool call here" as "a message with zero tool calls", which would
    set finish_reason=tool_calls on ordinary prose.

    The JSON dialect is deliberately NOT handled: ds4 parses that itself, and a
    message carrying it has already been dealt with upstream. Translating it
    again would risk emitting the same call twice.
    """
    if not text or "<function=" not in text:
        return None
    calls: list[dict] = []
    for index, match in enumerate(XML_CALL.finditer(text)):
        arguments = {
            param.group("key"): param.group("value").strip()
            for param in XML_PARAM.finditer(match.group("body"))
        }
        calls.append(
            {
                # A distinct id per call: OpenCode keys tool results by id, and
                # two calls sharing one would silently lose a result.
                "id": "call_" + secrets.token_hex(16),
                "index": index,
                "type": "function",
                "function": {
                    "name": match.group("name").strip(),
                    "arguments": json.dumps(arguments),
                },
            }
        )
    return calls or None


def translate_response(payload: dict) -> bool:
    """Rewrite an XML-dialect fallback into a real tool call. True if changed."""
    changed = False
    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("tool_calls"):
            continue
        calls = parse_xml_tool_calls(message.get("content") or "")
        if not calls:
            continue
        # Keep any prose the model wrote around the call, but remove the call
        # itself -- leaving it in content would show the user raw XML next to a
        # tool result, and some clients echo content back into the next prompt.
        message["content"] = XML_CALL.sub("", message["content"]).strip()
        message["tool_calls"] = calls
        choice["finish_reason"] = "tool_calls"
        changed = True
        stats["translated"] += 1
    return changed


# --- SSE synthesis --------------------------------------------------------


def _event(payload: dict) -> bytes:
    return b"data: " + json.dumps(payload).encode() + b"\n\n"


def synthesise_sse(body: dict) -> list[bytes]:
    """Turn a completed chat response into the SSE stream the client expected.

    The client asked for `stream: true` and we asked upstream for `false`, so
    the stream is reconstructed here. It is not incremental -- every delta is
    emitted once the full answer is known -- which costs the client its
    token-by-token display but nothing in wall time, since a tool call cannot
    be acted on before it is complete anyway.
    """
    base = {
        "id": body.get("id", "chatcmpl-shim"),
        "object": "chat.completion.chunk",
        "created": body.get("created") or int(time.time()),
        "model": body.get("model", ""),
    }

    def chunk(index: int, delta: dict, finish: object = None) -> bytes:
        payload = dict(base)
        payload["choices"] = [
            {"index": index, "delta": delta, "finish_reason": finish}
        ]
        return _event(payload)

    out: list[bytes] = []
    for choice in body.get("choices") or []:
        index = choice.get("index", 0)
        message = choice.get("message") or {}
        out.append(chunk(index, {"role": message.get("role") or "assistant"}))
        if message.get("reasoning_content"):
            out.append(chunk(index, {"reasoning_content": message["reasoning_content"]}))
        if message.get("content"):
            out.append(chunk(index, {"content": message["content"]}))
        for call in message.get("tool_calls") or []:
            out.append(chunk(index, {"tool_calls": [call]}))
        out.append(chunk(index, {}, choice.get("finish_reason")))
    if body.get("usage"):
        payload = dict(base)
        payload["choices"] = []
        payload["usage"] = body["usage"]
        out.append(_event(payload))
    out.append(b"data: [DONE]\n\n")
    return out


# --- request preparation --------------------------------------------------


@dataclasses.dataclass
class Prepared:
    """A request as it should go upstream, plus what the client is owed back."""

    body: bytes
    client_wants_stream: bool


def prepare(body: bytes) -> Prepared:
    """Add the instruction, and take a tool request off the streaming path.

    `client_wants_stream` is True only when we have *changed* the request --
    i.e. the client asked to stream and we must synthesise that stream back.
    A request we pass through untouched reports False whatever it asked for,
    because the upstream response can simply be relayed.
    """
    instructed = rewrite(body)
    try:
        payload = json.loads(instructed)
    except ValueError:
        return Prepared(instructed, False)
    if not isinstance(payload, dict):
        return Prepared(instructed, False)
    # Only tool requests are diverted. A request with no tools cannot lose a
    # tool call, and real streaming is worth keeping where it costs nothing.
    if not payload.get("tools") or not payload.get("stream"):
        return Prepared(instructed, False)
    payload["stream"] = False
    stats["unstreamed"] += 1
    return Prepared(json.dumps(payload).encode(), True)


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return  # the access log is noise; we log what we rewrite instead

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        stats["requests"] += 1
        prepared = prepare(body)
        body = prepared.body
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

        if prepared.client_wants_stream:
            self._synthesise(response)
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

    def _synthesise(self, response: object) -> None:
        """Relay a non-streaming upstream answer as the SSE stream we promised."""
        try:
            raw = response.read()
        finally:
            response.close()
        try:
            payload = json.loads(raw)
        except ValueError:
            # Nothing to synthesise from. Send it as-is rather than inventing a
            # stream: a malformed body is a real failure and must stay visible.
            logger.error("upstream body was not JSON (%d bytes)", len(raw))
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if translate_response(payload):
            logger.info(
                "translated an XML tool call (%d so far of %d unstreamed)",
                stats["translated"],
                stats["unstreamed"],
            )

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for event in synthesise_sse(payload):
            self.wfile.write(b"%X\r\n%s\r\n" % (len(event), event))
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

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
