"""Every machine opens a session the way the shared closing prompt expects (#556).

A departing session runs `hardware/agent-closing-prompt.md` and leaves a closing
log in `~/.local-llm-bench/handoff/`. The next session finds that log only if
its machine's handoff prompt tells it to look. A new machine's prompt, copied
from an older one, or an edit to one prompt alone, would leave that machine's
openers blind to the log -- and a log nobody reads looks exactly like a close
that worked.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HARDWARE = ROOT / "hardware"
CLOSING = HARDWARE / "agent-closing-prompt.md"
HANDOFF_DIR = "~/.local-llm-bench/handoff/"


def handoff_prompts() -> list[pathlib.Path]:
    return sorted(HARDWARE.glob("*/agent-handoff-prompt-*.md"))


def test_there_is_a_handoff_prompt_to_check() -> None:
    assert handoff_prompts(), "no hardware/*/agent-handoff-prompt-*.md found"


def test_every_handoff_prompt_reads_the_closing_log() -> None:
    for prompt in handoff_prompts():
        text = prompt.read_text()
        assert HANDOFF_DIR in text, f"{prompt.name} never looks in {HANDOFF_DIR}"
        assert "### 1a. Read what the last crew left" in text, prompt.name
        assert "handoff/done/" in text, f"{prompt.name} never retires a log"


def test_every_handoff_prompt_links_the_shared_closing_prompt() -> None:
    for prompt in handoff_prompts():
        assert "](../agent-closing-prompt.md)" in prompt.read_text(), prompt.name
        assert (prompt.parent / "../agent-closing-prompt.md").resolve() == CLOSING


def test_the_closing_prompt_names_every_machines_handoff_prompt() -> None:
    """A machine missing from its table gets no machine-specific commands."""
    text = CLOSING.read_text()
    for prompt in handoff_prompts():
        rel = prompt.relative_to(HARDWARE).as_posix()
        assert f"]({rel})" in text, f"closing prompt does not link {rel}"


def test_the_closing_prompt_writes_where_the_openers_read() -> None:
    text = CLOSING.read_text()
    assert HANDOFF_DIR in text
    assert "handoff/done/" in text
