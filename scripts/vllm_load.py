"""Aggregate throughput against a vLLM server at a given concurrency (#334).

    uv run python scripts/vllm_load.py --base-url http://127.0.0.1:8030 \
        --model qwen3.6-27b-nvfp4-nothink --concurrency 1 2 4 8 16

The agent harness cannot answer this. It is single-stream by construction -- one
agent, one request in flight -- so all 191 of the DGX Spark's rows describe C1,
while the machine's stated mission is aggregate multi-stream serving.

What it reports, per concurrency level:

  aggregate tok/s   output tokens summed over streams, divided by wall clock.
                    The number third-party claims quote.
  per-stream tok/s  what one user experiences. The number that decides whether
                    a second person on the box is tolerable.
  TTFT              median time to first token.

Both directions are printed because a ratio whose direction the reader has to
infer is a ratio that will be inferred backwards.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import pathlib
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
import logs

logger = logging.getLogger(__name__)

# Long enough that decode dominates the measurement rather than prefill, and
# fixed so every stream and every concurrency level asks the same question.
PROMPT = (
    "Write a Python function that parses an RFC 5322 email address list, "
    "handling quoted display names, comments in parentheses, and group syntax. "
    "Explain each step as you go."
)


def one_stream(base_url: str, model: str, max_tokens: int) -> dict:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=1800) as response:
        payload = json.loads(response.read())
    elapsed = time.monotonic() - start
    usage = payload.get("usage") or {}
    return {
        "wall": elapsed,
        "output_tokens": usage.get("completion_tokens", 0),
        "prompt_tokens": usage.get("prompt_tokens", 0),
    }


def level(base_url: str, model: str, n: int, max_tokens: int) -> dict:
    """One concurrency level: n streams launched together."""
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        futures = [
            pool.submit(one_stream, base_url, model, max_tokens) for _ in range(n)
        ]
        got = [f.result() for f in futures]
    wall = time.monotonic() - start
    total = sum(r["output_tokens"] for r in got)
    per = [r["output_tokens"] / r["wall"] for r in got if r["wall"]]
    return {
        "concurrency": n,
        "wall": wall,
        "output_tokens": total,
        "aggregate_tps": total / wall if wall else 0.0,
        "per_stream_tps": statistics.median(per) if per else 0.0,
        "slowest_stream": max((r["wall"] for r in got), default=0.0),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8030")
    p.add_argument("--model", required=True)
    p.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--json-out", type=pathlib.Path)
    args = p.parse_args(argv)

    logs.configure()
    logger.info(
        "load: %s at %s, concurrency %s, max_tokens %d",
        args.model,
        args.base_url,
        args.concurrency,
        args.max_tokens,
    )
    rows = []
    for n in args.concurrency:
        got = level(args.base_url, args.model, n, args.max_tokens)
        rows.append(got)
        logger.info(
            "C%-3d aggregate %7.1f tok/s   per-stream %6.1f tok/s   "
            "wall %6.1fs   slowest stream %6.1fs   tokens %d",
            n,
            got["aggregate_tps"],
            got["per_stream_tps"],
            got["wall"],
            got["slowest_stream"],
            got["output_tokens"],
        )
    if rows:
        base = rows[0]
        logger.info("")
        logger.info("relative to C%d, both directions:", base["concurrency"])
        for r in rows:
            agg = (
                r["aggregate_tps"] / base["aggregate_tps"]
                if base["aggregate_tps"]
                else 0
            )
            per = (
                r["per_stream_tps"] / base["per_stream_tps"]
                if base["per_stream_tps"]
                else 0
            )
            logger.info(
                "  C%-3d aggregate x%.2f (C1 is x%.2f of it)   "
                "per-stream x%.2f (C1 is x%.2f of it)",
                r["concurrency"],
                agg,
                1 / agg if agg else 0,
                per,
                1 / per if per else 0,
            )
    if args.json_out:
        args.json_out.write_text(json.dumps(rows, indent=2) + "\n")
        logger.info("wrote %s", args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
