"""The opener runs the loop first, then the opening routine, on every machine.

A departing session runs the closer, `hardware/agent-closer-prompt.md`, and
leaves a closer log in `~/.local-llm-bench/closer-logs/` (#556). The next
session runs the opener, `hardware/agent-opener-prompt.md`, which every
operator machine shares (2026-09-30). Its step 0 arms the 30-minute loop; its
§1 is the opening routine: the same ten steps every time, whatever state the
machine is in (#558). It must work from a fresh clone on a new machine, and it
reads a closer log without depending on one.

Until 2026-09-30 there was one opener per machine. Each armed its heartbeat
at the end of the routine, and each fixed the heartbeat its own way. On the
DGX cluster a chain of one-shot wakeups broke and the pair sat idle for
2 h 40 min. These tests pin the order and the rules that fix it, so an edit
cannot quietly drop one.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HARDWARE = ROOT / "hardware"
OPENER = HARDWARE / "agent-opener-prompt.md"
CLOSER = HARDWARE / "agent-closer-prompt.md"
LOG_DIR = "~/.local-llm-bench/closer-logs/"

sys.path.insert(0, str(ROOT / "scripts"))

STEPS = [
    "## 0. Step 0: the loop is armed before anything else",
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

#: The operator machines and their sections. The Core i3-7100 is a remote
#: client with no operator session, and the single Spark is the cluster's head.
SECTIONS = {
    "M5-Max-128GB": "## 9. The M5 Max MacBook Pro (128 GB)",
    "Cortex-X925-GB10-x2": "## 10. The dual DGX Spark cluster",
    "Ryzen9-7900X-RTX3080Ti": "## 11. The Ryzen 9 7900X / RTX 3080 Ti desktop",
}
NOT_OPERATED = {"Corei3-7100-KabyLake-SGT2_HDGraphics630_", "Cortex-X925-GB10"}

TEXT = OPENER.read_text()


def test_there_is_one_opener() -> None:
    """A per-machine copy is how the three drifted apart."""
    stray = sorted(HARDWARE.glob("*/agent-opener-prompt*.md"))
    assert stray == [], f"per-machine openers are back: {stray}"


def test_the_stub_arms_a_fixed_interval_loop_before_anything_else() -> None:
    stub = TEXT[TEXT.index("## The stub to paste") : TEXT.index("\n---\n")]
    assert "/loop 30m local-llm operator tick" in stub
    # the loop is the first thing the stub does, before reading the file
    assert stub.index("/loop 30m") < stub.index("2. Follow the file")
    assert "{DEADLINE}" not in TEXT  # the loop has no deadline; it stops on 3 events


def test_step_0_comes_before_the_routine_and_the_steps_run_in_order() -> None:
    positions = []
    for step in STEPS:
        assert step in TEXT, f"the opener lacks {step!r}"
        positions.append(TEXT.index(step))
    assert positions == sorted(positions), "the steps are out of order"


def test_step_0_forbids_a_one_shot_chain_and_arms_the_watchdog() -> None:
    step0 = TEXT[TEXT.index(STEPS[0]) : TEXT.index("## 1. The opening routine")]
    assert "Never drive this loop with `ScheduleWakeup`" in step0
    assert "exactly one** recurring job" in step0
    assert "scripts/heartbeat.py --watchdog" in step0
    assert "scripts/heartbeat.py --armed" in step0
    for gone in ("ScheduleWakeup` (`/loop` dynamic", "sleep 1860"):
        assert gone not in TEXT, f"an old loop instruction is back: {gone!r}"


def test_the_loop_stops_on_three_events_only() -> None:
    stops = TEXT[TEXT.index("### When the loop stops") :]
    stops = stops[: stops.index("### 3a.")]
    for event in ("The session ends.", "The machine reboots.", "the closer"):
        assert event in stops


def test_the_heartbeat_lists_the_operators_fields_in_order() -> None:
    hb = TEXT[TEXT.index("### 3a. The heartbeat") : TEXT.index("### 3b.")]
    fields = [
        "**The time**",
        "**Task:**",
        "**Sensors:**",
        "**Disk:**",
        "**PRs:**",
        "**Next:**",
        "**Questions for you:**",
    ]
    positions = [hb.index(f) for f in fields]
    assert positions == sorted(positions)
    assert "**No GPU\n   utilization.**" in hb or "**No GPU utilization.**" in hb


def test_the_script_renders_the_same_order_the_opener_states() -> None:
    import datetime as dt

    import heartbeat

    body = heartbeat.render(
        dt.datetime(2026, 9, 30, 6, 30, tzinfo=dt.UTC),
        "x",
        {"task": "t", "next": "n"},
        sensors=["s"],
        disk="d",
        prs="p",
        alerts=[],
    )
    labels = re.findall(r"^- \*\*([^*]+):\*\*", body, flags=re.MULTILINE)
    assert labels == [
        "Task",
        "Timing",  # started and ETA, operator 2026-09-30
        "Sensors",
        "Disk",
        "PRs",
        "Next",
        "Questions for you",
    ]


def test_every_operator_machine_has_a_section() -> None:
    import machines

    slugs = {m.slug for m in machines.MACHINES}
    assert set(SECTIONS) | NOT_OPERATED == slugs, "a machine has no section"
    positions = [TEXT.index(h) for h in SECTIONS.values()]
    assert positions == sorted(positions)
    for slug, heading in SECTIONS.items():
        assert f"`hardware:{slug}`" in TEXT[TEXT.index(heading) :], slug


def test_the_opener_starts_from_nothing() -> None:
    """Step 1 must clone when there is no checkout, not assume one."""
    assert "[ -d ~/git/local-llm/.git ] || git clone" in TEXT
    assert "[ -d .venv ] || uv sync" in TEXT
    assert "gh auth status" in TEXT


def test_the_opener_reads_the_closer_log_without_depending_on_it() -> None:
    assert LOG_DIR in TEXT
    assert "closer-logs/done/" in TEXT
    assert "**No closer log\nis the normal case**" in TEXT
    assert "It never replaces a check." in TEXT


def test_the_opener_links_the_shared_closer() -> None:
    assert "](agent-closer-prompt.md)" in TEXT


def test_the_closer_links_the_opener_and_every_section() -> None:
    text = CLOSER.read_text()
    assert "](agent-opener-prompt.md)" in text
    for heading in SECTIONS.values():
        anchor = re.sub(r"[^\w\- ]", "", heading.lstrip("# ").lower()).replace(" ", "-")
        assert f"](agent-opener-prompt.md#{anchor})" in text, anchor


def test_the_closer_stops_the_loop() -> None:
    text = CLOSER.read_text()
    assert "CronDelete" in text and "stop the watchdog" in text


def test_the_closer_writes_where_the_opener_reads() -> None:
    text = CLOSER.read_text()
    assert LOG_DIR in text
    assert "closer-logs/done/" in text


@pytest.mark.parametrize("path", ["README.md", "Cortex-X925-128GB-GB10-x2/README.md"])
def test_no_doc_links_a_deleted_opener(path: str) -> None:
    text = (HARDWARE / path).read_text()
    assert not re.search(r"agent-opener-prompt-[\w-]+\.md", text), path


def test_the_opener_says_where_macos_and_linux_differ() -> None:
    """The DGX head's harness kills a background task at ~30 min; the M5 Max's
    does not (2026-09-30). A loop rule tuned on one broke on the other."""
    section = TEXT[TEXT.index("### macOS and Linux") : TEXT.index("## 1. The opening")]
    for row in (
        "a `run_in_background` task",
        "the watchdog ends by",
        "a scheduler outside the session",
        "a command with a time limit",
        "engine and tool upgrades",
    ):
        assert row in section, row
