"""Does the engine engage MTP on the traffic we actually send it? (#148, #151)

The MTP arm is measured through a coding agent, and a coding agent's requests
carry a tool schema. `tasks.toml` already warns that ds4's scheduler bypasses
MTP on families where drafts are not accepted, so "MTP did nothing" and "MTP
was never engaged" arrive as the same row. This tells them apart, by sending
the same prompt with and without tools and reading the server's own counters
across each request.

    uv run python scripts/mtp_engagement.py --server-log ~/bench-logs/ds4.log

**Why a script and not a look at the log.** The counters are a delta: the log
spans a whole session and a request's own cycles are only the bytes it
appended. Reading that by eye is how "the arm drafted nothing" and "I scrolled
to the wrong place" become the same conclusion. The arms alternate per
repetition for the same reason `decode_ab.sh` alternates -- a fixed order gives
whichever arm runs second a warmer or busier machine.

The output is per-request: generated tokens, speculative cycles, accepted draft
tokens. A request that generated hundreds of tokens with zero cycles is an arm
whose treatment was never applied, whatever its row says.

**`--pad-tokens` is the second axis, and it was missing.** The shape arms hold
the prompt constant, which is right for isolating `tools` and wrong for
answering #151: on 2026-09-08 a treated MTP arm produced 280 speculative
cycles across 26 short toolless requests and **zero** across 16 tool-bearing
requests whose prompts were 11,760 tokens -- same server, same loaded sidecar,
minutes apart. Tools and context length moved together, so that run cannot say
which one switched MTP off. Pad the prompt and run the shapes again at
agent-realistic length, and it can.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import time
import urllib.request

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import mtp_timing
import provenance

logger = logging.getLogger(__name__)

# A prompt long enough to generate for a while, with nothing unusual in it.
# The question is the request *shape*, so the text is held constant.
DEFAULT_PROMPT = (
    "Write a Python function that merges two sorted lists into one sorted "
    "list, with a docstring and three tests. Then explain the time complexity "
    "in two sentences."
)

# Filler for `--pad-tokens`. Numbered lines of prose are what an agent's
# context actually holds -- file contents it read -- and they tokenize at
# roughly one token per word, which is close enough for a knob whose only job
# is to put the request in the right size class.
PAD_LINE = "{n:05d}  def helper_{n}(value): return value  # padding line, no meaning\n"


def pad_prompt(prompt: str, pad_tokens: int) -> str:
    """Prepend roughly `pad_tokens` tokens of filler.

    Deterministic on purpose: two runs at the same setting send the same
    bytes, so a difference between them is the server's, not the prompt's.
    """
    if pad_tokens <= 0:
        return prompt
    # ~14 tokens per line by the tokenizer's own count on this model family.
    lines = max(1, round(pad_tokens / 14))
    filler = "".join(PAD_LINE.format(n=i) for i in range(lines))
    return (
        "Here is a file I read. Ignore it; answer the question after it.\n\n"
        + filler
        + "\n"
        + prompt
    )


# A minimal, realistic tool schema. The agent clients send one on every turn;
# what matters here is that the request carries `tools` at all.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the working directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


def one_request(
    base_url, model, token, prompt, tools, max_tokens, timeout, stream=False
):
    """Send one completion. Returns (generated_tokens, seconds).

    `stream` matters more than it looks: OpenCode streams, the tool-format shim
    turns streaming into a non-streaming upstream call, and those are different
    request shapes reaching the engine.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if tools:
        payload["tools"] = TOOLS
    if stream:
        payload["stream"] = True
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as fh:
        if not stream:
            body = json.load(fh)
            elapsed = time.monotonic() - started
            usage = body.get("usage") or {}
            return usage.get("completion_tokens"), elapsed
        # Count deltas rather than trusting a usage block that a streaming
        # response may not carry.
        generated = 0
        for raw in fh:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content") or delta.get("tool_calls"):
                    generated += 1
            if usage := chunk.get("usage"):
                generated = usage.get("completion_tokens", generated)
        elapsed = time.monotonic() - started
        return generated, elapsed


def measure(reader, log, offset):
    """Counters appended since `offset`, and the new offset."""
    reading = reader.read_since(log, offset)
    counters = reading.counters
    cycles = len(counters.cycles) if counters.cycles is not None else 0
    return {
        "cycles": cycles,
        "accepted": counters.accepted,
        "used": counters.used,
        "bytes": reading.offset - offset,
    }, reading.offset


def run(args) -> int:
    log = pathlib.Path(args.server_log).expanduser()
    if not log.exists():
        logger.error("no server log at %s", log)
        return 2
    offset = log.stat().st_size
    prompt = pad_prompt(args.prompt, args.pad_tokens)
    if args.pad_tokens > 0:
        logger.info(
            "prompt padded to ~%d tokens (%d characters)", args.pad_tokens, len(prompt)
        )
    rows = []
    for rep in range(1, args.repeats + 1):
        # Alternate, so position cannot masquerade as an effect.
        arms = list(args.arms)
        if rep % 2 == 0:
            arms.reverse()
        for arm in arms:
            gen, elapsed = one_request(
                args.base_url,
                args.model,
                args.auth_token,
                prompt,
                tools=("tools" in arm),
                max_tokens=args.max_tokens,
                timeout=args.timeout,
                stream=("stream" in arm),
            )
            # The server writes its counters as it goes; give the last lines a
            # moment to land before reading the delta.
            time.sleep(args.settle)
            counters, offset = measure(mtp_timing, log, offset)
            row = {
                "rep": rep,
                "arm": arm,
                "generated": gen,
                "seconds": round(elapsed, 2),
                **counters,
            }
            rows.append(row)
            logger.info(
                "rep %d %-5s generated=%-5s cycles=%-4d accepted=%-5s used=%s (%.1fs)",
                rep,
                arm,
                gen,
                counters["cycles"],
                counters["accepted"],
                counters["used"],
                elapsed,
            )
    logger.info("---")
    for arm in args.arms:
        arm_rows = [r for r in rows if r["arm"] == arm]
        gen = sum(r["generated"] or 0 for r in arm_rows)
        cycles = sum(r["cycles"] for r in arm_rows)
        accepted = sum(r["accepted"] or 0 for r in arm_rows)
        logger.info(
            "%-5s: %d requests, %d tokens generated, %d speculative cycles, %d accepted",
            arm,
            len(arm_rows),
            gen,
            cycles,
            accepted,
        )
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(rows, indent=2) + "\n")
        logger.info("rows written to %s", args.json)
    engaged = {
        arm: any(r["cycles"] for r in rows if r["arm"] == arm) for arm in args.arms
    }
    logger.info(
        "MTP engaged: %s", ", ".join(f"{arm}={yes}" for arm, yes in engaged.items())
    )
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--model", default="qwen3.8-flash-next-q4")
    p.add_argument("--auth-token", default="dsv4-local")
    p.add_argument("--server-log", required=True, help="the log the server is writing")
    p.add_argument("--repeats", type=int, default=3, help="rounds of requests")
    p.add_argument(
        "--arms",
        nargs="+",
        default=["plain", "tools"],
        help="request shapes to compare: any of plain, tools, stream, "
        "tools-stream. `stream` is what the agent clients actually send, and "
        "the tool-format shim converts it to a non-streaming upstream call.",
    )
    p.add_argument("--max-tokens", type=int, default=400)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument(
        "--pad-tokens",
        type=int,
        default=0,
        help="prepend roughly this many tokens of filler, so the shape arms "
        "can be compared at an agent-realistic context length (#151). The "
        "shape is held constant across the pad, and the pad across the shapes.",
    )
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument(
        "--settle", type=float, default=1.0, help="seconds to let the log flush"
    )
    p.add_argument("--json", help="write the per-request rows here")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    provenance.configure()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
