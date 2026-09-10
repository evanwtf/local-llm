"""Replay a captured agent request and bisect what switches ds4's MTP off (#151).

Eight synthetic cells engaged MTP on 2026-09-08 -- plain and tools, streaming
and not, direct and through the shim, at 290 and 11,760 prompt tokens, the
best accepting 325 of 400 tokens. Fifteen real agent trials, on the same
server with the same loaded sidecar, emitted **not one speculative cycle**.

So the discriminator is something a hand-built request does not have. Rather
than guess a ninth axis, this replays the real thing: `SHIM_DUMP` writes the
first instructed upstream payload the tool-format shim sends, and this sends
it back verbatim, then again with one property removed at a time.

    SHIM_DUMP=/tmp/payload.json uv run python ds4_qwen_tool_shim.py ...
    uv run python scripts/mtp_replay_probe.py --payload /tmp/payload.json \\
        --server-log ~/bench-logs/ds4.log

**The verbatim arm is the one that matters.** If it engages MTP, the payload
is not the cause and the difference is in the connection or the sequence, not
the request -- and every ablation below is measuring noise. Check it first;
the script says so in its output rather than leaving it to be noticed.

Each ablation removes exactly one property, always from the original payload
rather than cumulatively, so two of them cannot mask each other.
"""

from __future__ import annotations

import argparse
import copy
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)


def ablate(payload: dict, name: str) -> dict | None:
    """One property removed. None when the payload has nothing to remove.

    A `None` is reported as skipped rather than run: an ablation that changed
    nothing would produce a cell indistinguishable from the verbatim arm and
    invite reading it as a result.
    """
    out = copy.deepcopy(payload)
    messages = out.get("messages") or []
    if name == "verbatim":
        return out
    if name == "no-tools":
        if not out.get("tools"):
            return None
        out.pop("tools", None)
        out.pop("tool_choice", None)
        return out
    if name == "one-tool":
        tools = out.get("tools") or []
        if len(tools) <= 1:
            return None
        out["tools"] = tools[:1]
        return out
    if name == "no-system":
        kept = [m for m in messages if m.get("role") != "system"]
        if len(kept) == len(messages):
            return None
        out["messages"] = kept
        return out
    if name == "no-tool-results":
        # The role that only a continuing agent turn carries. A single-user-
        # turn probe can never produce one, which is why it is a candidate.
        kept = [m for m in messages if m.get("role") != "tool"]
        if len(kept) == len(messages):
            return None
        out["messages"] = kept
        return out
    # The three fields a hand-built probe sets differently without meaning
    # to. `mtp_engagement.py` pins temperature to 0 and max_tokens to 200 and
    # sends no stream_options; the captured payload does the opposite of all
    # three. Every message-shaped ablation above stayed at zero cycles, so
    # what is left is here.
    if name == "temperature-zero":
        if out.get("temperature") == 0:
            return None
        out["temperature"] = 0
        return out
    if name == "max-tokens-200":
        if out.get("max_tokens") == 200:
            return None
        out["max_tokens"] = 200
        return out
    if name == "no-stream-options":
        if "stream_options" not in out:
            return None
        out.pop("stream_options", None)
        return out
    if name == "last-message-only":
        user = [m for m in messages if m.get("role") == "user"]
        if not user or len(messages) <= 1:
            return None
        out["messages"] = user[-1:]
        return out
    raise ValueError(f"unknown ablation {name!r}")


ABLATIONS = (
    "verbatim",
    "no-tools",
    "one-tool",
    "no-system",
    "no-tool-results",
    "last-message-only",
    "temperature-zero",
    "max-tokens-200",
    "no-stream-options",
)


def describe(payload: dict) -> str:
    messages = payload.get("messages") or []
    roles: dict[str, int] = {}
    for m in messages:
        roles[m.get("role", "?")] = roles.get(m.get("role", "?"), 0) + 1
    chars = len(json.dumps(messages))
    return (
        f"{len(messages)} message(s) {roles}, {len(payload.get('tools') or [])} "
        f"tool(s), ~{chars} chars, stream={bool(payload.get('stream'))}"
    )


def send(base_url: str, token: str, payload: dict, timeout: int) -> int | None:
    """Generated tokens, or None when the engine did not report them."""
    body = dict(payload)
    # Streaming would need the delta counting `mtp_engagement` does; the
    # counters are read from the server's log either way, and #151's shim
    # cells showed streaming is not the discriminator.
    body.pop("stream", None)
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as fh:
        answer = json.load(fh)
    return (answer.get("usage") or {}).get("completion_tokens")


def run(args) -> int:
    logs.configure()
    log = pathlib.Path(args.server_log).expanduser()
    if not log.exists():
        logger.error("no server log at %s", log)
        return 2
    payload = json.loads(pathlib.Path(args.payload).expanduser().read_text())
    logger.info("captured payload: %s", describe(payload))

    offset = log.stat().st_size
    rows = []
    for name in args.ablations:
        body = ablate(payload, name)
        if body is None:
            logger.info("%-18s SKIPPED -- the payload has nothing to remove", name)
            continue
        generated = send(args.base_url, args.auth_token, body, args.timeout)
        time.sleep(args.settle)
        reading = mtp_timing.read_since(log, offset)
        counters = reading.counters
        cycles = len(counters.cycles) if counters.cycles is not None else 0
        offset = reading.offset
        rows.append(
            {
                "arm": name,
                "generated": generated,
                "cycles": cycles,
                "accepted": counters.accepted,
            }
        )
        logger.info(
            "%-18s generated=%-5s cycles=%-4d accepted=%-5s",
            name,
            generated,
            cycles,
            counters.accepted,
        )

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(rows, indent=2) + "\n")
        logger.info("rows written to %s", args.json)

    first = next((r for r in rows if r["arm"] == "verbatim"), None)
    if first is None:
        logger.warning("no verbatim arm ran; the ablations have no baseline")
        return 0
    if first["cycles"]:
        logger.warning(
            "the verbatim payload ENGAGED MTP (%d cycles, %s accepted). The "
            "request is not the cause, so every ablation below it is measuring "
            "noise -- look at the connection or the sequence instead.",
            first["cycles"],
            first["accepted"],
        )
        return 0
    logger.info(
        "the verbatim payload reproduced the zero. An ablation with cycles > 0 "
        "names the property that switches MTP off."
    )
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--payload", required=True, help="a SHIM_DUMP capture")
    p.add_argument("--server-log", required=True)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--auth-token", default="dsv4-local")
    p.add_argument("--ablations", nargs="+", default=list(ABLATIONS))
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--settle", type=float, default=1.0)
    p.add_argument("--json")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
