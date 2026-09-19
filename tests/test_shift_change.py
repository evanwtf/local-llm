"""Every machine's opener reads what the shared closer writes (#556).

A departing session runs the closer, `hardware/agent-closer-prompt.md`, and
leaves a closer log in `~/.local-llm-bench/closer-logs/`. The next session
finds that log only if its machine's opener tells it to look. A new machine's prompt, copied
from an older one, or an edit to one prompt alone, would leave that machine's
new sessions blind to the log -- and a log nobody reads looks exactly like a close
that worked.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HARDWARE = ROOT / "hardware"
CLOSER = HARDWARE / "agent-closer-prompt.md"
LOG_DIR = "~/.local-llm-bench/closer-logs/"


def opener_prompts() -> list[pathlib.Path]:
    return sorted(HARDWARE.glob("*/agent-opener-prompt-*.md"))


def test_there_is_an_opener_to_check() -> None:
    assert opener_prompts(), "no hardware/*/agent-opener-prompt-*.md found"


def test_every_opener_reads_the_closer_log() -> None:
    for prompt in opener_prompts():
        text = prompt.read_text()
        assert LOG_DIR in text, f"{prompt.name} never looks in {LOG_DIR}"
        assert "### 1a. Read what the last crew left" in text, prompt.name
        assert "closer-logs/done/" in text, f"{prompt.name} never retires a log"


def test_every_opener_links_the_shared_closer() -> None:
    for prompt in opener_prompts():
        assert "](../agent-closer-prompt.md)" in prompt.read_text(), prompt.name
        assert (prompt.parent / "../agent-closer-prompt.md").resolve() == CLOSER


def test_the_closer_names_every_machines_opener() -> None:
    """A machine missing from its table gets no machine-specific commands."""
    text = CLOSER.read_text()
    for prompt in opener_prompts():
        rel = prompt.relative_to(HARDWARE).as_posix()
        assert f"]({rel})" in text, f"the closer does not link {rel}"


def test_the_closer_writes_where_the_openers_read() -> None:
    text = CLOSER.read_text()
    assert LOG_DIR in text
    assert "closer-logs/done/" in text
