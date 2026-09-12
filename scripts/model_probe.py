"""Check a served model's answers in code, never by reading them.

    uv run python scripts/model_probe.py --base-url http://127.0.0.1:8020 \
        --model qwen3.8-flash-next-q3 --thinking on off

On 2026-09-12 a thinking-off smoke check returned `kramdetneb` for "reverse the
string benchmark" and it was reported as correct. It is not: `benchmark`
reversed is `kramhcneb`. The output was plausible-looking and was accepted by
eye, and that wrong check contributed to a decision to spend a machine-hour.

So every expectation here is **computed, not transcribed**. `"benchmark"[::-1]`
cannot be mistyped in a way that still looks right, while a hand-written
`"kramhcneb"` can -- and a typo in the expected value fails in the direction
that silently passes a wrong answer.

The probes deliberately cover the class the agent suite is blind to. Its oracle
is a repository's own tests, and none of its ten tasks turns on reversing a
string, counting characters, or index arithmetic -- so a model that got worse at
exactly that would still score 30/30 (#4, #333).
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
import logs

logger = logging.getLogger(__name__)

WORDS = ("benchmark", "mailbox", "parser", "envelope", "quoting")


def probes() -> list[tuple[str, str]]:
    """(prompt, expected) with every expectation computed from Python itself."""
    out: list[tuple[str, str]] = []
    for w in WORDS:
        out.append(
            (f'Reverse the string "{w}". Reply with only the reversed string.', w[::-1])
        )
        out.append(
            (
                f'How many characters are in "{w}"? Reply with only the number.',
                str(len(w)),
            )
        )
    out.append(
        (
            (
                'How many times does the letter "r" appear in "strawberry"? '
                "Reply with only the number."
            ),
            str("strawberry".count("r")),
        )
    )
    a, b = 17, 23
    out.append((f"What is {a} * {b}? Reply with only the number.", str(a * b)))
    return out


def ask(base_url: str, model: str, prompt: str, thinking: bool | None) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1200,
        "temperature": 0,
    }
    if thinking is not None:
        payload["chat_template_kwargs"] = {"enable_thinking": thinking}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as response:
        return json.loads(response.read())


def graded(text: str, expected: str) -> bool:
    """Substring, case-insensitive, punctuation-tolerant.

    Deliberately generous: the question is whether the model knows the answer,
    not whether it obeyed "reply with only". A strict match would fail a correct
    answer wrapped in a sentence and report a capability loss that is not there.
    """
    return expected.lower() in (text or "").lower().replace("`", "")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8020")
    p.add_argument("--model", required=True)
    p.add_argument(
        "--thinking",
        nargs="+",
        default=["default"],
        choices=["on", "off", "default"],
        help="which thinking settings to probe; more than one compares them",
    )
    p.add_argument("--json-out", type=pathlib.Path)
    args = p.parse_args(argv)

    logs.configure()
    cases = probes()
    results = {}
    for mode in args.thinking:
        flag = {"on": True, "off": False, "default": None}[mode]
        ok = 0
        tokens = 0
        rows = []
        for prompt, expected in cases:
            got = ask(args.base_url, args.model, prompt, flag)
            text = (got["choices"][0]["message"].get("content") or "").strip()
            tokens += got["usage"]["completion_tokens"]
            good = graded(text, expected)
            ok += good
            rows.append(
                {
                    "prompt": prompt,
                    "expected": expected,
                    "got": text[:120],
                    "correct": good,
                }
            )
            if not good:
                logger.warning(
                    "thinking=%s WRONG: %s -> %r, wanted %r",
                    mode,
                    prompt[:44],
                    text[:40],
                    expected,
                )
        logger.info(
            "thinking=%-7s %2d/%-2d correct   %5d completion tokens",
            mode,
            ok,
            len(cases),
            tokens,
        )
        results[mode] = {
            "correct": ok,
            "total": len(cases),
            "tokens": tokens,
            "rows": rows,
        }
    if args.json_out:
        args.json_out.write_text(json.dumps(results, indent=2) + "\n")
        logger.info("wrote %s", args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
