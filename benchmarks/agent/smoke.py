"""Three trivial coding exercises, run against a backend before any trial (#63).

Why this exists
---------------
On 2026-08-31 a GLM cell ran four trials and produced three empty patches
before anyone noticed the model was answering in a degraded mode. The cause
was a shim rewriting `thinking:{"type":"adaptive"}` to `disabled`; #63 then
measured that arm at 4/8 correct on trivial functions against 8/8 for
thinking on. Nothing in preflight could see it: the servers were up, the
versions were current, the repo was pristine, and the model returned 200 OK
to every request. It was answering, just badly.

Preflight checks that the machine is ready. This checks that the *model* is,
by making it write three functions whose answers are not in doubt and then
executing them.

Choice of tasks
---------------
All three are ones the degraded arm got WRONG in #63, so each is a live
detector rather than a formality:

    reverse       150 tok / 5s  wrong (off)   276 tok / 8s  right (on)
    fib           127 tok / 4s  wrong (off)   336 tok / 10s right (on)
    mergesorted   548 tok / 19s wrong (off)   431 tok / 15s right (on)

They span string handling, recursion and list merging, and the healthy arm
finished each in under 20 s. The 300 s budget is therefore ~15x headroom: it
catches a wedged or looping server without ever firing on a slow-but-working
one.

Protocol
--------
Requests go to `/v1/messages` in Anthropic form carrying
`thinking:{"type":"adaptive"}` -- the exact shape Claude Code sends. That is
deliberate. An OpenAI-style `/v1/chat/completions` probe would have passed
happily on 2026-08-31, because the defect lived in a shim that only rewrites
Anthropic thinking blocks. A gate must travel the same road as the client.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

logger = logging.getLogger(__name__)

DEADLINE_SECONDS = 300

# Modest on purpose. The gate must be fast, and running out of budget is no
# longer treated as a wrong answer -- see gate() -- so a reasoning model that
# needs more room is warned about rather than refused.
MAX_TOKENS = 4000

SMOKE_TASKS: tuple[tuple[str, str, str], ...] = (
    (
        "reverse",
        (
            "Write a Python function reverse_string(s: str) -> str that reverses a "
            "string. Reply with the function in a single ```python code block and "
            "nothing else."
        ),
        "assert reverse_string('hello') == 'olleh'",
    ),
    (
        "fib",
        (
            "Write a Python function fib(n: int) -> int returning the nth Fibonacci "
            "number, with fib(0)=0 and fib(1)=1. Reply with the function in a single "
            "```python code block and nothing else."
        ),
        "assert fib(0) == 0 and fib(1) == 1 and fib(10) == 55",
    ),
    (
        "mergesorted",
        (
            "Write a Python function merge_sorted(a: list, b: list) -> list that merges "
            "two sorted lists into one sorted list. Reply with the function in a single "
            "```python code block and nothing else."
        ),
        "assert merge_sorted([1, 3, 5], [2, 4]) == [1, 2, 3, 4, 5]",
    ),
)


class SmokeFailure(Exception):
    """A backend answered, but not correctly enough to measure anything with."""


def extract_code(text: str) -> str:
    """Prefer a fenced block; fall back to the whole reply.

    A model that ignores the formatting instruction but writes correct code
    still passes. The gate is about capability, not obedience.
    """
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return blocks[0] if blocks else text


def verify(text: str, assertion: str) -> bool:
    namespace: dict = {}
    try:
        exec(extract_code(text), namespace)  # noqa: S102 - our own prompt, local server
        exec(assertion, namespace)  # noqa: S102
    except Exception:  # noqa: BLE001 - any failure to run is a failed check
        return False
    return True


def _post(
    base_url: str, token: str, model: str, prompt: str, timeout: int
) -> tuple[str, str | None]:
    """Returns (visible text, stop_reason).

    Only `text` blocks are answer text. A reasoning model may also return
    `thinking` blocks, and on 2026-08-31 `qwen3.6:27b-coding-mxfp8` returned
    **nothing but** thinking: it spent the whole budget reasoning and stopped at
    `max_tokens` before writing a line of code. Reading only `text` yielded ""
    and the gate scored a 24/24 backend as unable to reverse a string.

    So the budget is generous and `stop_reason` is returned: running out of room
    to think is a different failure from writing the wrong function, and the
    caller must be able to say which happened.
    """
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "thinking": {"type": "adaptive"},
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode(), strict=False)
    blocks = payload.get("content") or []
    text = "".join(
        b.get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") != "thinking"
    )
    return text, payload.get("stop_reason")


def _ran_out(row: dict) -> bool:
    """True when the model was still working, not wrong: a timeout or an
    exhausted token budget. Distinct from producing a bad answer."""
    return row.get("stop_reason") == "max_tokens" or "Timeout" in (
        row.get("error") or ""
    )


def check(
    backend: dict,
    deadline: int = DEADLINE_SECONDS,
    post: Callable[..., tuple[str, str | None]] = _post,
) -> list[dict]:
    """Run the three exercises. Returns one row per task; never raises."""
    rows = []
    for name, prompt, assertion in SMOKE_TASKS:
        started = time.monotonic()
        try:
            text, stop_reason = post(
                backend["base_url"],
                backend.get("auth_token", ""),
                backend["model"],
                prompt,
                deadline,
            )
            correct = verify(text, assertion)
            # An empty answer that stopped at max_tokens means the model ran out
            # of room while thinking, not that it got the function wrong.
            error = (
                "no answer text; stop_reason=max_tokens (spent the budget thinking)"
                if not text.strip() and stop_reason == "max_tokens"
                else None
            )
        except Exception as exc:  # noqa: BLE001 - a refused connection is a failed
            # gate, not a crash; the batch must be told, not killed by a traceback
            correct, error, stop_reason = False, f"{type(exc).__name__}: {exc}", None
        wall = time.monotonic() - started
        rows.append(
            {
                "task": name,
                "correct": correct,
                "wall_seconds": round(wall, 1),
                "within_deadline": wall <= deadline,
                "stop_reason": stop_reason,
                "error": error,
            }
        )
    return rows


def gate(
    backend: dict,
    name: str,
    deadline: int = DEADLINE_SECONDS,
    post: Callable[..., tuple[str, str | None]] = _post,
) -> list[dict]:
    """Refuse to start a batch against a backend that cannot do the basics.

    A hosted backend (no `base_url`) is skipped and said to be skipped: the
    failure this guards against is a local server or shim serving a degraded
    mode, and a smoke test against a metered API bills real money every run.
    """
    if not backend.get("base_url"):
        logger.info("smoke: %s has no base_url (hosted); skipped", name)
        return []

    rows = check(backend, deadline, post)
    for row in rows:
        logger.info(
            "smoke: %s %-12s correct=%s %.1fs%s",
            name,
            row["task"],
            row["correct"],
            row["wall_seconds"],
            f" ({row['error']})" if row["error"] else "",
        )

    # Refuse on a WRONG answer; warn on a SLOW one. These are different signals
    # and conflating them cost a false refusal on 2026-08-31: qwen3.6-coding,
    # 24/24 on real tasks, was blocked because it spent 900 s thinking about
    # fib(10) while answering the other two correctly.
    #
    # A degraded backend -- the failure this gate exists to catch -- is fast,
    # confident and wrong. Slowness is what the trials themselves measure.
    wrong = [
        r["task"]
        for r in rows
        if not r["correct"] and r["within_deadline"] and not _ran_out(r)
    ]
    slow = [r["task"] for r in rows if not r["within_deadline"] or _ran_out(r)]

    if slow:
        logger.warning(
            "smoke: %s did not finish %s in %ds. Not refusing -- slowness is "
            "measured by the trials. Watch for timeouts in this batch.",
            name,
            slow,
            deadline,
        )
    if wrong:
        raise SmokeFailure(
            f"{name} failed the smoke gate: wrong={wrong} -- the backend answered "
            "in time and got it wrong, which is what a degraded model looks like. "
            "Check the model alias, the thinking mode, and any shim in the path "
            "before spending a batch on it."
        )
    return rows
