"""Find which cached prefix block changes between two requests (#50, #64).

`ds4-server` reports `reason=token-mismatch` when the live KV prefix stops
matching, and [#64] measured that costing ~21 minutes of re-prefill across the
logs we hold. The server can say *that* the prefix broke; only the request
bodies say *where*.

A prompt prefix is cacheable up to the last block marked `cache_control`.
If any content at or before that mark differs from the previous request, the
whole prefix is invalidated -- and #50's hypothesis is that Claude Code
injects a live token counter as a system message, with `cache_control`
attached, whose number changes every turn:

    {"role":"system","content":"<total_tokens>14969546 tokens left</total_tokens>"}

**Content is hashed, never stored.** Request bodies carry the operator's
CLAUDE.md and whatever files the agent read; `.gitignore` already warns that
the `fail-*.json` dumps must never be committed for exactly this reason. A
digest is enough to say "this block changed", which is the whole question.

    uv run python scripts/prefix_stability.py req1.json req2.json
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import pathlib
import re
import sys
from typing import Any

logger = logging.getLogger(__name__)

#: Content that carries a number which moves every turn. A block matching one
#: of these AND marked cacheable is a prefix poisoned by construction.
VOLATILE = (
    re.compile(
        r"<total_tokens>\s*\d+\s*tokens?\s*left\s*</total_tokens>", re.IGNORECASE
    ),
    re.compile(r"\b\d+\s+tokens?\s+(?:left|remaining)\b", re.IGNORECASE),
)


@dataclasses.dataclass(frozen=True)
class Block:
    index: int
    role: str
    digest: str
    cacheable: bool
    volatile: bool
    chars: int


def _text_of(content: Any) -> str:
    """Flatten a message's content to text, whatever shape it arrives in."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _has_cache_control(message: dict[str, Any]) -> bool:
    if "cache_control" in message:
        return True
    content = message.get("content")
    if isinstance(content, list):
        return any(
            isinstance(item, dict) and "cache_control" in item for item in content
        )
    return False


def blocks(payload: dict[str, Any]) -> list[Block]:
    """One Block per message, content hashed rather than kept."""
    out: list[Block] = []
    for i, message in enumerate(payload.get("messages") or []):
        if not isinstance(message, dict):
            continue
        text = _text_of(message.get("content"))
        out.append(
            Block(
                index=i,
                role=str(message.get("role", "?")),
                digest=hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12],
                cacheable=_has_cache_control(message),
                volatile=any(p.search(text) for p in VOLATILE),
                chars=len(text),
            )
        )
    return out


def cache_horizon(items: list[Block]) -> int:
    """Index of the last cacheable block; -1 when nothing is marked.

    Everything at or before this index must be byte-identical between two
    requests or the prefix is thrown away.
    """
    marked = [b.index for b in items if b.cacheable]
    return max(marked) if marked else -1


def first_divergence(a: list[Block], b: list[Block]) -> Block | None:
    """The first block within the cache horizon that differs between requests.

    That block is what broke the prefix. Returns None when the cached region
    is identical, which means the miss came from somewhere else and #50's
    mechanism is not the explanation for this pair.
    """
    horizon = min(cache_horizon(a), cache_horizon(b))
    if horizon < 0:
        return None
    for left, right in zip(a, b, strict=False):
        if left.index > horizon:
            return None
        if left.digest != right.digest or left.role != right.role:
            return right
    # One request is shorter than the other inside the cached region.
    if len(a) != len(b):
        shorter = a if len(a) < len(b) else b
        if len(shorter) <= horizon:
            longer = b if shorter is a else a
            return longer[len(shorter)]
    return None


def describe(a: list[Block], b: list[Block]) -> str:
    horizon = min(cache_horizon(a), cache_horizon(b))
    if horizon < 0:
        return "neither request marks a cacheable prefix; nothing to compare"
    culprit = first_divergence(a, b)
    volatile_marked = [x for x in b if x.cacheable and x.volatile]
    cached = sum(1 for x in b if x.index <= horizon)
    lines = [f"cache horizon: message {horizon} ({cached} messages cached)"]
    if culprit is None:
        lines.append("cached region is IDENTICAL -- the prefix was not broken here")
    else:
        lines.append(
            f"first divergence: message {culprit.index} role={culprit.role} "
            f"({culprit.chars} chars)"
            + ("  <- carries a volatile token count" if culprit.volatile else "")
        )
    for x in volatile_marked:
        lines.append(
            f"cacheable AND volatile: message {x.index} role={x.role} "
            f"-- a changing number inside the cached prefix poisons it every turn"
        )
    if not volatile_marked:
        lines.append("no cacheable block carries a volatile token count")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("first", type=pathlib.Path)
    p.add_argument("second", type=pathlib.Path)
    args = p.parse_args(argv)
    try:
        a = blocks(json.loads(args.first.read_text()))
        b = blocks(json.loads(args.second.read_text()))
    except (OSError, ValueError) as exc:
        logger.error("could not read a request payload: %s", exc)
        return 1
    logger.info("%s", describe(a, b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
