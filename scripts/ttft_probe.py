"""Time-to-first-token against a vLLM endpoint, client-observed vs engine (#346).

#346's claim: most of a harness's time-to-first-token is the *client*, not the
accelerator -- a field report saw ~18 s under Hermes and ~2 s under OpenCode
against the SAME vLLM endpoint. The measurement that settles it is a subtraction
(`scripts/engine_timing.py` does it for llama.cpp logs; this does the vLLM side):

    harness overhead = client-observed first token - the engine's own TTFT

The right-hand term is exact and free: vLLM times every request identically
whoever sent it, in the `vllm:time_to_first_token_seconds` histogram. This probe
sends a streaming request itself (so its own client-observed TTFT is the raw
no-harness FLOOR), and reads the histogram delta across the request for the
engine term. For a real client (Hermes, OpenClaw, OpenCode) you take its
client-observed TTFT from its own timing and subtract the engine TTFT the
histogram recorded for the same request -- the remainder is the harness's, with
no instrumentation of the client at all.

    uv run python scripts/ttft_probe.py --base-url http://127.0.0.1:8030 \
        --model qwen3.6-35b-a3b-nvfp4 --reps 5

Temperature 0; two prompt sizes (trivial, where prefill is negligible so the
number is almost pure overhead, and a realistic first turn) unless --prompt is
given. Writes nothing unless --out is passed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time
import urllib.request

TRIVIAL = "Reply with just: OK"
# A realistic agent first turn: a system-ish instruction plus a small file, so
# prefill is real work rather than a handful of tokens.
REALISTIC = (
    "You are a coding agent. Here is a file:\n\n"
    + "def add(a, b):\n    return a - b\n\n" * 40
    + "\nThe add function is wrong. Reply with only the corrected one-line body."
)


def parse_histogram(text: str, metric: str) -> tuple[float, float] | None:
    """Return (sum, count) for a vLLM histogram, or None if absent.

    Labels vary (engine, model_name), so match the metric name up to the brace
    rather than a fixed label set.
    """
    total_sum = total_count = None
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        if name == metric + "_sum":
            total_sum = float(line.rsplit(" ", 1)[1])
        elif name == metric + "_count":
            total_count = float(line.rsplit(" ", 1)[1])
    if total_sum is None or total_count is None:
        return None
    return total_sum, total_count


def engine_ttft_ms(
    before: tuple[float, float], after: tuple[float, float]
) -> float | None:
    """Per-request engine TTFT from the histogram delta, in ms.

    ``(sum_after - sum_before) / (count_after - count_before)``. None when no new
    request landed in the window (count did not advance) -- never divide by zero
    and never manufacture a 0 ms that would read as an instant prefill.
    """
    d_sum = after[0] - before[0]
    d_count = after[1] - before[1]
    if d_count <= 0:
        return None
    return (d_sum / d_count) * 1000.0


def _metrics(base_url: str) -> str:
    with urllib.request.urlopen(base_url.rstrip("/") + "/metrics", timeout=10) as r:
        return r.read().decode(errors="replace")


def _stream_ttft_ms(base_url: str, model: str, prompt: str) -> float | None:
    """Client-observed TTFT: wall time from request send to first content token."""
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
            "temperature": 0,
            "stream": True,
        }
    ).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=120) as r:
        for line in r:
            s = line.decode(errors="replace").strip()
            if s.startswith("data:") and s != "data: [DONE]":
                try:
                    delta = json.loads(s[5:])["choices"][0].get("delta", {})
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta.get("content"):
                    return (time.monotonic() - t0) * 1000.0
    return None


def probe(base_url: str, model: str, prompt: str) -> dict:
    """One request: client-observed TTFT and the engine's own TTFT for it."""
    metric = "vllm:time_to_first_token_seconds"
    before = parse_histogram(_metrics(base_url), metric)
    client = _stream_ttft_ms(base_url, model, prompt)
    after = parse_histogram(_metrics(base_url), metric)
    engine = engine_ttft_ms(before, after) if before and after else None
    overhead = client - engine if (client is not None and engine is not None) else None
    return {"client_ms": client, "engine_ms": engine, "overhead_ms": overhead}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8030")
    p.add_argument("--model", required=True)
    p.add_argument("--prompt", default=None, help="override; else trivial + realistic")
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--out", type=pathlib.Path)
    args = p.parse_args(argv)

    prompts = (
        [("custom", args.prompt)]
        if args.prompt
        else [("trivial", TRIVIAL), ("realistic", REALISTIC)]
    )
    rows = []
    print(
        f"{'prompt':10} {'rep':>3} {'client ms':>10} {'engine ms':>10} {'overhead ms':>12}"
    )
    for label, prompt in prompts:
        for rep in range(args.reps):
            r = probe(args.base_url, args.model, prompt)
            r.update(prompt=label, rep=rep, model=args.model)
            rows.append(r)
            em = f"{r['engine_ms']:.1f}" if r["engine_ms"] is not None else "-"
            om = f"{r['overhead_ms']:.1f}" if r["overhead_ms"] is not None else "-"
            print(f"{label:10} {rep:>3} {r['client_ms']:>10.1f} {em:>10} {om:>12}")
    for label, _ in prompts:
        cs = [r["client_ms"] for r in rows if r["prompt"] == label and r["client_ms"]]
        es = [r["engine_ms"] for r in rows if r["prompt"] == label and r["engine_ms"]]
        if cs:
            print(
                f"\n{label}: median client {statistics.median(cs):.1f} ms, "
                f"median engine {statistics.median(es):.1f} ms"
                if es
                else f"\n{label}: median client {statistics.median(cs):.1f} ms"
            )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(f"\nwrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
