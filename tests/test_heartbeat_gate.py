"""The Stop hook blocks a tick turn that ends without the script's heartbeat.

The negative cases matter most: a hook that blocks an ordinary turn, or loops
on a turn it has already blocked, would stop every session that loads it.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import heartbeat as hb
import heartbeat_gate as gate

TICK = "local-llm operator tick: read ~/git/local-llm/hardware/agent-opener-prompt.md"
NOW = dt.datetime(2026, 9, 30, 8, 4, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "heartbeat_gate.py"


@pytest.fixture(autouse=True)
def no_naps(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retries would sleep 5.5 s in every blocking test."""
    monkeypatch.setattr(gate.time, "sleep", lambda _: None)


def signed() -> str:
    body = hb.render(
        NOW,
        "DGX cluster",
        {"task": "#840"},
        sensors=["x"],
        disk="d",
        prs="p",
        alerts=[],
    )
    return hb.sign(body, "scripts/cluster_heartbeat.py")


def user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_result() -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": "ok"}]},
    }


def said(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }


def tool_use() -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use"}]}}


def transcript(tmp_path: pathlib.Path, entries: list[dict]) -> dict:
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return {"transcript_path": str(p), "stop_hook_active": False}


def test_a_tick_ending_with_the_signed_heartbeat_passes(tmp_path) -> None:
    hook = transcript(tmp_path, [user(TICK), tool_use(), tool_result(), said(signed())])
    assert gate.decide(hook) is None


def test_a_hand_written_tick_is_blocked(tmp_path) -> None:
    hook = transcript(
        tmp_path, [user(TICK), tool_use(), tool_result(), said("2026-09-30 08:04 EDT")]
    )
    got = gate.decide(hook) or ""
    assert "no signature" in got and "cluster_heartbeat.py --dry-run" in got


def test_only_the_final_message_counts(tmp_path) -> None:
    """A heartbeat sent between tool calls may never reach the operator."""
    entries = [user(TICK), said(signed()), tool_use(), tool_result(), said("done")]
    assert gate.decide(transcript(tmp_path, entries)) is not None


def test_a_turn_that_is_not_a_tick_is_never_blocked(tmp_path) -> None:
    hook = transcript(tmp_path, [user("close #327"), said("closed")])
    assert gate.decide(hook) is None


def test_a_later_prompt_ends_the_tick_turn(tmp_path) -> None:
    entries = [user(TICK), said(signed()), user("what next?"), said("this")]
    assert gate.decide(transcript(tmp_path, entries)) is None


def test_a_second_stop_is_allowed_so_the_hook_cannot_loop(tmp_path) -> None:
    hook = transcript(tmp_path, [user(TICK), said("freehand")])
    assert gate.decide(hook | {"stop_hook_active": True}) is None


def test_a_missing_transcript_allows_the_stop(tmp_path) -> None:
    assert gate.decide({"transcript_path": str(tmp_path / "gone.jsonl")}) is None
    assert gate.decide({}) is None


def test_the_hook_exits_2_with_the_reason_on_stderr(tmp_path) -> None:
    hook = transcript(tmp_path, [user(TICK), said("freehand")])
    hook["last_assistant_message"] = "freehand"  # final as given: no retries
    p = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(hook),
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 2 and "heartbeat script's output" in p.stderr


def test_the_hook_exits_0_on_bad_input() -> None:
    p = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 0


def test_a_final_message_written_after_the_hook_starts_is_seen(tmp_path) -> None:
    """The hook ran before the transcript held the final message and blocked a
    correct heartbeat (M5 Max, 2026-09-30 08:41). It must re-read."""
    early = [user(TICK), said("working"), tool_use(), tool_result()]
    reads = iter([early, early, [*early, said(signed())]])
    naps: list[float] = []
    got = gate.decide(
        {"transcript_path": str(tmp_path / "t.jsonl")},
        reader=lambda _: next(reads),
        sleep=naps.append,
    )
    assert got is None and naps == [0.5, 0.5]


def test_a_hand_written_tick_is_still_blocked_after_the_retries(tmp_path) -> None:
    entries = [user(TICK), said("freehand")]
    naps: list[float] = []
    got = gate.decide(
        {"transcript_path": "x"}, reader=lambda _: entries, sleep=naps.append, tries=3
    )
    assert got is not None and len(naps) == 2


def test_the_hooks_own_last_message_wins_over_the_transcript(tmp_path) -> None:
    stale = [user(TICK), said("working")]
    hook = {"transcript_path": "x", "last_assistant_message": signed()}
    naps: list[float] = []
    assert gate.decide(hook, reader=lambda _: stale, sleep=naps.append) is None
    assert naps == []
    bad = hook | {"last_assistant_message": "freehand"}
    assert gate.decide(bad, reader=lambda _: stale, sleep=naps.append) is not None
    assert naps == []  # the hook's own text is final; no waiting
