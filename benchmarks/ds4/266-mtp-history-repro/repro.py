"""#266 repro driver: drive the MTP-loaded ds4-server at long context with a
forced tool call, looking for the HTTP 500 ("prefill failed at position N")
that the server log attributes to stage=MTP-history on a tool-call-recovery
re-prefill. Probabilistic (per Codex): send several, report how many 500'd.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

PORT = 8199
PROMPT_FILE = (
    sys.argv[1]
    if len(sys.argv) > 1
    else ("/Users/evanhoffman/git/ds4-metal/speed-bench/promessi_sposi.txt")
)
# chars per request: ~4 chars/token, so 52k chars ~= 13k tokens, near the
# observed failure band (pos 11.8k-16.9k).
CHARS = int(sys.argv[2]) if len(sys.argv) > 2 else 60000
N = int(sys.argv[3]) if len(sys.argv) > 3 else 6

with open(PROMPT_FILE, encoding="utf-8", errors="ignore") as _fh:
    text = _fh.read()[:CHARS]

tool = {
    "type": "function",
    "function": {
        "name": "record_summary",
        "description": "Record a one-line summary of the passage.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
}


def one(i: int) -> tuple[int, str]:
    body = {
        "model": "qwen",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Always respond by calling a function named "
                    "`deep_analyze` with a single argument `findings` that is "
                    "a JSON array of objects. Never call any other function "
                    "and never reply in plain text."
                ),
            },
            {"role": "user", "content": text},
            {
                "role": "user",
                "content": "Call deep_analyze with your findings about this passage.",
            },
        ],
        "tools": [tool],  # only record_summary is defined; deep_analyze is not
        "tool_choice": "auto",
        "max_tokens": 600,
        "temperature": 0,
    }
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="ignore")[:200]
    except Exception as e:  # noqa: BLE001
        return -1, str(e)[:200]


fails = 0
for i in range(N):
    code, err = one(i)
    tag = "OK " if code == 200 else "500" if code == 500 else str(code)
    print(f"[{i + 1}/{N}] chars={CHARS} status={code} {tag} {err}")
    if code == 500:
        fails += 1
print(f"\n{fails}/{N} returned 500 at chars={CHARS}")
