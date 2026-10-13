#!/usr/bin/env python3
"""Measure prefix-cache hits on a running vLLM server, by prompt length (#626).

vllm-project/vllm#52244 reports that a hybrid GDN model with MTP drafting gets
**no** prefix-cache hit on a replayed prompt whose length is a multiple of the
cache block, and less than the ceiling on other lengths. This asks a running
server whether it does that, and asks the engine rather than a clock: vLLM's
`/metrics` counts prefix-cache queries and hits in tokens, so the hit on a
replay is the counter's change across that one request.

Each length is a *pair* of identical requests. The first fills the cache; the
second is the reading. Prompts are token-id lists sent to `/v1/completions`,
so a length is exact and no chat template adds tokens. Each length starts with
its own random prefix, so no length inherits another's cache.

The reading is only clean on an otherwise idle server: anything else that
reaches it between the two counter reads is counted too.

    uv run python scripts/vllm_prefix_hits.py --url http://127.0.0.1:8888 \\
        --model qwen3.8-flash-next --block 832 --k 1 2 3 4 --out hits.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import secrets
import sys
import urllib.request
from dataclasses import asdict, dataclass

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import logs

logger = logging.getLogger(__name__)

HITS = "vllm:prefix_cache_hits"
QUERIES = "vllm:prefix_cache_queries"
FILLER = (
    "The quick brown fox jumps over the lazy dog while the committee reviews "
    "the quarterly ledger, line by line, and files each finding in order. "
)


@dataclass(frozen=True)
class Reading:
    tokens: int
    kind: str  # "multiple", "plus1" or "half"
    ceiling: int
    queried: float
    hit: float


def counter(metrics: str, name: str) -> float:
    """The sum of a counter over every label set, by its base name.

    Accepts the name with or without the `_total` suffix that Prometheus
    clients append to counters.
    """
    total = 0.0
    found = False
    for line in metrics.splitlines():
        if not line or line.startswith("#"):
            continue
        metric = line.split("{", 1)[0].split(" ", 1)[0]
        if metric in (name, f"{name}_total"):
            total += float(line.rsplit(" ", 1)[1])
            found = True
    if not found:
        raise KeyError(f"no counter {name} in /metrics")
    return total


def lengths(block: int, ks: list[int]) -> list[tuple[int, str]]:
    """Prompt lengths to test: each block multiple, and two controls beside it."""
    out: list[tuple[int, str]] = []
    for k in ks:
        out.append((k * block, "multiple"))
        out.append((k * block + 1, "plus1"))
        out.append((k * block + block // 2, "half"))
    return out


def ceiling(tokens: int, block: int) -> int:
    """The most a replay can hit: whole blocks before the last token.

    The last prompt token is always recomputed for its logits, so a hit is
    capped at `tokens - 1`, rounded down to the block.
    """
    return (tokens - 1) // block * block


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:
        body: bytes = resp.read()
    return body.decode()


def _post(url: str, payload: dict[str, object]) -> dict[str, object]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        out: dict[str, object] = json.loads(resp.read())
    return out


def token_ids(url: str, model: str, text: str) -> list[int]:
    got = _post(f"{url}/tokenize", {"model": model, "prompt": text})
    ids = got["tokens"]
    assert isinstance(ids, list)
    return [int(t) for t in ids]


def measure(url: str, model: str, block: int, ks: list[int]) -> list[Reading]:
    filler = token_ids(url, model, FILLER * 400)
    readings = []
    for tokens, kind in lengths(block, ks):
        prefix = token_ids(url, model, f"Probe {secrets.token_hex(8)}. ")
        if tokens > len(prefix) + len(filler):
            raise ValueError(f"{tokens} tokens is longer than the filler")
        prompt = (prefix + filler)[:tokens]
        body = {"model": model, "prompt": prompt, "max_tokens": 1, "temperature": 0}
        _post(f"{url}/v1/completions", body)  # fills the cache
        before = _get(f"{url}/metrics")
        _post(f"{url}/v1/completions", body)  # the reading
        after = _get(f"{url}/metrics")
        r = Reading(
            tokens=tokens,
            kind=kind,
            ceiling=ceiling(tokens, block),
            queried=counter(after, QUERIES) - counter(before, QUERIES),
            hit=counter(after, HITS) - counter(before, HITS),
        )
        logger.info(
            "%6d tokens (%s): hit %d of ceiling %d (queried %d)",
            r.tokens,
            r.kind,
            r.hit,
            r.ceiling,
            r.queried,
        )
        readings.append(r)
    return readings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="http://127.0.0.1:8888")
    p.add_argument("--model", required=True)
    p.add_argument("--block", type=int, required=True, help="the server's block size")
    p.add_argument("--k", type=int, nargs="+", default=[1, 2, 3, 4])
    p.add_argument("--out", type=pathlib.Path, help="append one JSON line per length")
    args = p.parse_args(argv)
    logs.configure()
    readings = measure(args.url, args.model, args.block, args.k)
    if args.out:
        with args.out.open("a") as f:
            for r in readings:
                f.write(json.dumps(asdict(r)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
