"""Block until a model server can actually serve, or give up.

    uv run python wait_ready.py --base-url http://127.0.0.1:8020 --model qwen3.8-flash-next-q3

Exit 0 when a real completion succeeds, 1 on timeout, 2 on bad usage. Safe to
call from a shell script; importable as `wait_ready.ready()`.

**Do not poll /health for this.** On 2026-08-31 a llama.cpp server answered
`/health` with `{"status":"ok"}` and HTTP 200 while every completion returned
503, because an 84 GB model was still being read off disk. A batch launched on
that signal failed its smoke gate three times in the same second and reported a
degraded model. `curl` compounds it: a 503 is a successful HTTP transaction, so
`curl -s .../health` exits 0 without `--fail`.

The only honest probe is a request of the kind the benchmark will actually
send. This one asks for a single token.

Both signals are reported, because the gap between them is the diagnostic:
health ok + completion 503 means "still loading", while both failing means
"nothing is listening".
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.error
import urllib.request

import provenance

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300
DEFAULT_INTERVAL = 5


def health(base_url: str, timeout: int = 5) -> str:
    """What /health claims. Advisory only -- it reports ok before it is true."""
    try:
        request = urllib.request.Request(f"{base_url.rstrip('/')}/health")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return f"{response.status} {response.read(120).decode(errors='replace').strip()}"
    except urllib.error.HTTPError as exc:
        return f"{exc.code}"
    except Exception as exc:  # noqa: BLE001 - advisory probe, never fatal
        return f"{type(exc).__name__}"


def serves(
    base_url: str, model: str, token: str, timeout: int = 30
) -> tuple[bool, str]:
    """Can it answer a one-token request? This is the real readiness test."""
    body = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            json.loads(response.read().decode(errors="replace"), strict=False)
        return True, "ok"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - any failure means not ready yet
        return False, type(exc).__name__


def ready(
    base_url: str,
    model: str,
    token: str = "local",
    timeout: int = DEFAULT_TIMEOUT,
    interval: int = DEFAULT_INTERVAL,
    sleep=time.sleep,
    now=time.monotonic,
) -> bool:
    """Poll until a real completion succeeds. Returns False on timeout."""
    deadline = now() + timeout
    attempt = 0
    while True:
        attempt += 1
        ok, detail = serves(base_url, model, token)
        if ok:
            logger.info("ready after %d attempt(s)", attempt)
            return True
        remaining = deadline - now()
        if remaining <= 0:
            logger.error(
                "not ready after %ds (%d attempts); last: completion %s, /health says %s",
                timeout,
                attempt,
                detail,
                health(base_url),
            )
            return False
        # The gap between the two signals is the diagnostic, so log both.
        logger.info(
            "not ready (completion %s, /health %s); %ds left",
            detail,
            health(base_url),
            int(remaining),
        )
        sleep(min(interval, max(remaining, 0)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--token", default="local")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    args = parser.parse_args()
    provenance.configure()
    return (
        0
        if ready(args.base_url, args.model, args.token, args.timeout, args.interval)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
