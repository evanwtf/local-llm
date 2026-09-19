"""Every machine's opener runs the opening routine and reads what the closer writes.

A departing session runs the closer, `hardware/agent-closer-prompt.md`, and
leaves a closer log in `~/.local-llm-bench/closer-logs/` (#556). The next
session runs its machine's opener. The opener's §1 is the opening routine: the
same ten steps every time, whatever state the machine is in (#558). It must
work from a fresh clone on a new machine, and it reads a closer log without
depending on one.

A new machine's opener, copied from an older one, or an edit to one opener
alone, would drop a step on that machine -- and a skipped step looks exactly
like a step that found nothing.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HARDWARE = ROOT / "hardware"
CLOSER = HARDWARE / "agent-closer-prompt.md"
LOG_DIR = "~/.local-llm-bench/closer-logs/"

STEPS = [
    "### Step 1. The kitchen exists",
    "### Step 2. Did the power go out?",
    "### Step 3. Locks and claims",
    "### Step 4. Servers",
    "### Step 5. Runs",
    "### Step 6. Worktrees, stray files, and stashes",
    "### Step 7. Stock",
    "### Step 8. The closer log, if there is one",
    "### Step 9. CI, PRs, and the queue",
    "### Step 10. Open for service",
]


def opener_prompts() -> list[pathlib.Path]:
    return sorted(HARDWARE.glob("*/agent-opener-prompt-*.md"))


OPENERS = opener_prompts()


def test_there_is_an_opener_to_check() -> None:
    assert OPENERS, "no hardware/*/agent-opener-prompt-*.md found"


@pytest.mark.parametrize("opener", OPENERS, ids=lambda p: p.parent.name)
def test_every_opener_runs_the_ten_steps_in_order(opener: pathlib.Path) -> None:
    text = opener.read_text()
    positions = []
    for step in STEPS:
        assert step in text, f"{opener.name} lacks {step!r}"
        positions.append(text.index(step))
    assert positions == sorted(positions), f"{opener.name} runs the steps out of order"


@pytest.mark.parametrize("opener", OPENERS, ids=lambda p: p.parent.name)
def test_every_opener_starts_from_nothing(opener: pathlib.Path) -> None:
    """Step 1 must clone when there is no checkout, not assume one."""
    text = opener.read_text()
    assert "[ -d ~/git/local-llm/.git ] || git clone" in text, opener.name
    assert "[ -d .venv ] || uv sync" in text, opener.name
    assert "gh auth status" in text, opener.name


@pytest.mark.parametrize("opener", OPENERS, ids=lambda p: p.parent.name)
def test_every_opener_reads_the_closer_log_without_depending_on_it(
    opener: pathlib.Path,
) -> None:
    text = opener.read_text()
    assert LOG_DIR in text, f"{opener.name} never looks in {LOG_DIR}"
    assert "closer-logs/done/" in text, f"{opener.name} never retires a log"
    assert "**No closer log\nis the normal case**" in text, opener.name
    assert "It never replaces a check." in text, opener.name


@pytest.mark.parametrize("opener", OPENERS, ids=lambda p: p.parent.name)
def test_every_opener_links_the_shared_closer(opener: pathlib.Path) -> None:
    assert "](../agent-closer-prompt.md)" in opener.read_text(), opener.name
    assert (opener.parent / "../agent-closer-prompt.md").resolve() == CLOSER


def test_the_closer_names_every_machines_opener() -> None:
    """A machine missing from its table gets no machine-specific commands."""
    text = CLOSER.read_text()
    for opener in OPENERS:
        rel = opener.relative_to(HARDWARE).as_posix()
        assert f"]({rel})" in text, f"the closer does not link {rel}"


def test_the_closer_writes_where_the_openers_read() -> None:
    text = CLOSER.read_text()
    assert LOG_DIR in text
    assert "closer-logs/done/" in text
