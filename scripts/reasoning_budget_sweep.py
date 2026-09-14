"""Does a short max_tokens with thinking ON return empty content? (#349)

The single-Spark field rule under test: with thinking on and a short `max_tokens`,
the model spends its whole budget reasoning and returns **empty content** -- a
turn-1 death with no answer. The corpus scanner
(`reasoning_budget_signature.py`) found the shape but could not prove the ceiling
mechanism, because rows do not record `max_tokens`. This sweep sets the ceiling on
purpose.

Against one served, thinking-capable model it sends the **same fixed prompt** at a
descending series of `max_tokens`, once with thinking on and once with thinking off
(the control), and records where `content` goes empty while reasoning was spent.
The prediction:

- **thinking on**: below some floor, content is empty and `finish_reason` is
  `length` -- the budget was consumed by reasoning before any answer began;
- **thinking off**: the same budget returns a short but non-empty answer.

If that holds, the recommendations file needs a `max_tokens` floor whenever
thinking is on -- and some corpus `solution_empty` rows are budget exhaustion, not
degeneration.

    uv run python scripts/reasoning_budget_sweep.py \
        --base-url http://127.0.0.1:8030 --model qwen3.6-35b-a3b-nvfp4 \
        --out hardware/Cortex-X925-128GB-GB10/reasoning-budget-sweep.jsonl

Temperature is 0 so the only thing changing between arms is the ceiling and the
thinking toggle. This writes its own sidecar file; it does NOT touch results.jsonl
(these are not agent-task rows).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.request

DEFAULT_MAX_TOKENS = [16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]
# A prompt that needs a little reasoning but has a short correct answer, so a
# starved turn is visibly empty rather than merely truncated mid-answer.
DEFAULT_PROMPT = (
    "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the "
    "ball. How much does the ball cost? Reply with just the amount."
)


def classify_response(data: dict) -> dict:
    """Reduce one chat/completions response to the fields the sweep compares.

    ``content_empty`` is the finding: the model produced no answer. Reasoning is
    reported separately (from ``completion_tokens_details.reasoning_tokens`` when
    the engine breaks it out, else the length of ``reasoning_content``), because
    the whole point is to see content starve *while reasoning was spent*.
    """
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    reasoning_tokens = details.get("reasoning_tokens")
    if reasoning_tokens is None:
        # Engine did not break it out; fall back to a char-based proxy so the
        # arm is still interpretable (marked approximate by the None above).
        reasoning_tokens = len(reasoning)
    return {
        "content_empty": content.strip() == "",
        "content_chars": len(content),
        "reasoning_tokens": reasoning_tokens,
        "reasoning_chars": len(reasoning),
        "completion_tokens": usage.get("completion_tokens"),
        "finish_reason": choice.get("finish_reason"),
        "content": content[:200],
    }


def _post(
    base_url: str, model: str, prompt: str, max_tokens: int, thinking: bool
) -> dict:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": thinking},
        }
    ).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8030")
    p.add_argument("--model", required=True)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--max-tokens", type=int, nargs="+", default=DEFAULT_MAX_TOKENS)
    p.add_argument("--out", type=pathlib.Path, help="append each arm as JSONL")
    args = p.parse_args(argv)

    rows = []
    print(
        f"{'thinking':9} {'max_tok':>8} {'reason_tok':>10} {'cont_chars':>10} "
        f"{'empty':>6} {'finish':>10}  answer"
    )
    for thinking in (True, False):
        for mt in sorted(args.max_tokens):
            data = _post(args.base_url, args.model, args.prompt, mt, thinking)
            c = classify_response(data)
            row = {
                "thinking": thinking,
                "max_tokens": mt,
                "model": args.model,
                "prompt": args.prompt,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                **c,
            }
            rows.append(row)
            print(
                f"{'on' if thinking else 'off':9} {mt:>8} "
                f"{c['reasoning_tokens']!s:>10} {c['content_chars']:>10} "
                f"{('EMPTY' if c['content_empty'] else '-'):>6} "
                f"{c['finish_reason']!s:>10}  {c['content'][:40]!r}"
            )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        print(f"\nwrote {len(rows)} arms to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
