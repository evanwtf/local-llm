"""Generate HumanEval completions from a ds4-server endpoint.

Writes one JSON object per problem to the output file as soon as it arrives, so
an interrupted run resumes instead of starting over. Scoring is a separate step
(score.py) and never runs model code in this process.
"""

import argparse
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

INSTRUCTION = (
    "Complete the following Python function. Reply with the entire function, "
    "including the signature and any imports it needs, inside a single "
    "```python code block. Do not add explanation, tests or example usage.\n\n"
    "```python\n{prompt}```\n"
)

CODE_BLOCK = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)


def extract_code(text, prompt, entry_point):
    """Pull a function body out of a model reply.

    Prefers the last fenced block that defines the entry point, because models
    often show a wrong first attempt before the final answer.
    """
    blocks = CODE_BLOCK.findall(text)
    for block in reversed(blocks):
        if f"def {entry_point}" in block:
            return block
    if blocks:
        return blocks[-1]
    # No fence at all. If the reply continues the signature, glue it on.
    if f"def {entry_point}" in text:
        return text[text.index("def " + entry_point):]
    return prompt + text


def request(url, model, prompt, max_tokens, timeout):
    body = json.dumps(
        {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--problems", default="HumanEval.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8000/v1/chat/completions")
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--limit", type=int, default=0, help="stop after N problems")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    problems = [json.loads(line) for line in open(args.problems)]
    if args.limit:
        problems = problems[: args.limit]

    done = set()
    if os.path.exists(args.out):
        with open(args.out) as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["task_id"])
                except (ValueError, KeyError):
                    continue
        logger.info("resuming: %d already generated", len(done))

    todo = [p for p in problems if p["task_id"] not in done]
    logger.info("generating %d completions -> %s", len(todo), args.out)

    started = time.time()
    tokens = 0
    with open(args.out, "a") as out:
        for i, prob in enumerate(todo, 1):
            prompt = INSTRUCTION.format(prompt=prob["prompt"])
            t0 = time.time()
            try:
                data = request(
                    args.url, args.model, prompt, args.max_tokens, args.timeout
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                logger.error("%s: request failed: %s", prob["task_id"], exc)
                record = {
                    "task_id": prob["task_id"],
                    "completion": "",
                    "error": str(exc),
                    "seconds": time.time() - t0,
                }
                out.write(json.dumps(record) + "\n")
                out.flush()
                continue

            msg = data["choices"][0]["message"]
            text = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            usage = data.get("usage") or {}
            completion_tokens = usage.get("completion_tokens", 0)
            tokens += completion_tokens
            elapsed = time.time() - t0

            # A reasoning model may leave content empty and put the answer in
            # the reasoning channel; fall back so those are not scored as blank.
            source = text if "```" in text or "def " in text else (text + reasoning)
            code = extract_code(source, prob["prompt"], prob["entry_point"])

            record = {
                "task_id": prob["task_id"],
                "completion": code,
                "raw_content": text,
                "raw_reasoning_len": len(reasoning),
                "finish_reason": data["choices"][0].get("finish_reason"),
                "completion_tokens": completion_tokens,
                "seconds": elapsed,
            }
            out.write(json.dumps(record) + "\n")
            out.flush()

            rate = tokens / max(time.time() - started, 1e-9)
            logger.info(
                "%3d/%3d %-16s %5.1fs %5d tok  finish=%-8s  avg %.1f tok/s",
                i,
                len(todo),
                prob["task_id"],
                elapsed,
                completion_tokens,
                record["finish_reason"],
                rate,
            )

    logger.info("done in %.1f min", (time.time() - started) / 60)


if __name__ == "__main__":
    main()
