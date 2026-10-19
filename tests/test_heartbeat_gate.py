"""The Stop hook blocks a tick turn that ends without the script's heartbeat.

The negative cases matter most: a hook that blocks an ordinary turn, or loops
on a turn it has already blocked, would stop every session that loads it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
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
def state_file(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Never read or stamp the real ~/.local-llm-bench state from a test."""
    p = tmp_path / "heartbeat.json"
    monkeypatch.setattr(gate, "STATE_PATH", p)
    return p


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
    # HOME is the test's own: the hook reads ~/.local-llm-bench/heartbeat.json,
    # and a machine whose real state says "sent 5 min ago" let this stop pass.
    p = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(hook),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(tmp_path)},
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
    assert gate.decide(hook, reader=lambda _: stale, sleep=naps.append, now=NOW) is None
    assert naps == []
    bad = hook | {"last_assistant_message": "freehand"}
    later = NOW + dt.timedelta(minutes=hb.SEND_EVERY_MIN)  # past the send gap
    assert (
        gate.decide(bad, reader=lambda _: stale, sleep=naps.append, now=later)
        is not None
    )
    assert naps == []  # the hook's own text is final; no waiting


# -- the 29-minute send gap (operator, 2026-10-19) ------------------------------


def test_a_quiet_tick_may_end_without_a_heartbeat_inside_the_gap(
    tmp_path, state_file
) -> None:
    sent = NOW - dt.timedelta(minutes=10)
    hb.write_state(state_file, {"sent_at": sent.isoformat()}, NOW, stamp=False)
    hook = transcript(tmp_path, [user(TICK), tool_use(), tool_result()])
    assert gate.decide(hook, now=NOW) is None


def test_a_tick_must_send_once_the_gap_has_passed(tmp_path, state_file) -> None:
    sent = NOW - dt.timedelta(minutes=29)
    hb.write_state(state_file, {"sent_at": sent.isoformat()}, NOW, stamp=False)
    hook = transcript(tmp_path, [user(TICK), said("quiet")])
    assert gate.decide(hook, now=NOW) is not None


def test_a_tick_with_no_record_of_a_send_must_send(tmp_path) -> None:
    hook = transcript(tmp_path, [user(TICK), said("quiet")])
    assert gate.decide(hook, now=NOW) is not None


def test_a_sent_heartbeat_starts_the_gap(tmp_path, state_file) -> None:
    hook = transcript(tmp_path, [user(TICK), said(signed())])
    assert gate.decide(hook, now=NOW) is None
    assert hb.read_state(state_file)["sent_at"] == NOW.isoformat(timespec="seconds")
    quiet = transcript(tmp_path, [user(TICK), said("")])
    later = NOW + dt.timedelta(minutes=10)
    assert gate.decide(quiet, now=later) is None


def test_a_hand_written_heartbeat_is_still_blocked_inside_the_gap(
    tmp_path, state_file
) -> None:
    """Inside the gap a tick may say nothing, but not a fake heartbeat."""
    sent = NOW - dt.timedelta(minutes=10)
    hb.write_state(state_file, {"sent_at": sent.isoformat()}, NOW, stamp=False)
    fake = signed().replace("#840", "#999")
    hook = transcript(tmp_path, [user(TICK), said(fake)])
    assert gate.decide(hook, now=NOW) is not None


def test_send_due_reads_the_gap() -> None:
    assert hb.send_due({}, NOW)
    assert not hb.send_due(
        {"sent_at": (NOW - dt.timedelta(minutes=28)).isoformat()}, NOW
    )
    assert hb.send_due({"sent_at": (NOW - dt.timedelta(minutes=29)).isoformat()}, NOW)


def test_a_heartbeat_sent_outside_a_tick_still_starts_the_gap(
    tmp_path, state_file
) -> None:
    """A heartbeat sent in a background-notification turn counts (2026-10-19)."""
    hook = transcript(tmp_path, [user("task finished"), said(signed())])
    assert gate.decide(hook, now=NOW) is None
    assert hb.read_state(state_file)["sent_at"] == NOW.isoformat(timespec="seconds")


def test_a_non_tick_turn_without_a_heartbeat_records_nothing(
    tmp_path, state_file
) -> None:
    hook = transcript(tmp_path, [user("hello"), said("hi")])
    assert gate.decide(hook, now=NOW) is None
    assert "sent_at" not in hb.read_state(state_file)
