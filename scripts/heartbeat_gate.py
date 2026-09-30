"""Stop hook: a tick turn must end with the heartbeat script's own output.

The opener (§3a) says to generate the heartbeat, not write it. On 2026-09-30
the DGX session hand-wrote its 08:04 tick anyway: it dropped the outlet power,
the questions and the timing, all of which `cluster_heartbeat.py` prints. A
rule the session can forget is a discipline; this hook makes it a mechanism.

Claude Code runs this when a turn is about to end, with a JSON object on
stdin (`transcript_path`, `stop_hook_active`). The hook reads the transcript:

- the turn's prompt is not a local-llm tick → allow;
- the final message carries a heartbeat whose body matches its signature
  (`heartbeat.verify`) → allow;
- otherwise → exit 2. Claude Code then keeps the turn open and shows the
  session the reason on stderr, so it runs the script and sends its output.

It blocks once per turn. When `stop_hook_active` is set, the session has
already been told once, and the hook allows the stop rather than loop.

Wire it in the user settings of each machine that runs the loop:

    {"hooks": {"Stop": [{"hooks": [{"type": "command",
      "command": "python3 ~/git/local-llm/scripts/heartbeat_gate.py"}]}]}}
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
from collections.abc import Callable, Iterable
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import heartbeat as hb

#: Every tick prompt carries this (the opener's /loop line).
TICK_MARK = "local-llm operator tick"

FIX = (
    "Run the heartbeat script and send its output, unedited, as the final"
    " message: `uv run python scripts/heartbeat.py` (M5 Max, Ryzen), or"
    " `uv run python scripts/heartbeat.py --tick && uv run python"
    " scripts/cluster_heartbeat.py --dry-run` (the DGX cluster). Put any event"
    " lines below it. Fix a wrong field with `--set`, never by editing the text."
)


def _text(content: Any) -> str | None:
    """The typed text of a message, or None for a tool result."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    texts = [
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    return "\n".join(texts) if texts else None


def last_turn(entries: Iterable[dict[str, Any]]) -> tuple[str, str]:
    """(the turn's prompt, its final assistant text). Empty when absent."""
    prompt, final = "", ""
    for e in entries:
        msg = e.get("message") or {}
        text = _text(msg.get("content"))
        if e.get("type") == "user" and text is not None:
            prompt, final = text, ""
        elif e.get("type") == "assistant" and text:
            final = text
    return prompt, final


def read_transcript(path: pathlib.Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def decide(
    hook: dict[str, Any],
    *,
    reader: Callable[[pathlib.Path], list[dict[str, Any]]] = read_transcript,
    sleep: Callable[[float], None] | None = None,
    tries: int = 12,
    pause: float = 0.5,
) -> str | None:
    """None to allow the stop; else the reason to block it."""
    if hook.get("stop_hook_active"):
        return None
    path = hook.get("transcript_path")
    if not path:
        return None
    # The hook can run before Claude Code writes the final message to the
    # transcript. On 2026-09-30 it read the text before a correct heartbeat
    # and blocked it. So take the message from the hook's input when it is
    # there, and otherwise re-read the transcript for a few seconds.
    nap = sleep or time.sleep
    given = hook.get("last_assistant_message")
    why = "no transcript"
    for attempt in range(tries):
        if attempt:
            nap(pause)
        try:
            entries = reader(pathlib.Path(path).expanduser())
        except OSError:
            return None
        prompt, final = last_turn(entries)
        if TICK_MARK not in prompt:
            return None
        why = hb.verify(given if isinstance(given, str) else final)
        if why is None or isinstance(given, str):
            break
    if why is None:
        return None
    return (
        f"This tick's final message is not the heartbeat script's output: {why}. {FIX}"
    )


def main() -> int:
    try:
        hook = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    reason = decide(hook if isinstance(hook, dict) else {})
    if reason is None:
        return 0
    sys.stderr.write(reason + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
