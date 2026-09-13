"""The pre-trial memory gate (scripts/memory_gate.py).

Two OOM kills on 2026-09-13 came from starting work while the unified pool was
in flux. The gate waits for memory to be above a floor AND no longer falling
before it lets the next trial start. These check it does not wave a launch
through early and does give up rather than hang.
"""

from __future__ import annotations

import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import memory_gate


def _run(monkeypatch, avails, floor=10.0, settle=3, timeout=100.0):
    """Drive gate() over a scripted sequence of MemAvailable readings."""
    seq = iter(avails)
    last = {"v": avails[-1]}

    def fake_meminfo():
        try:
            last["v"] = next(seq)
        except StopIteration:
            pass
        a = last["v"]
        return {
            "total_gib": 122.0,
            "avail_gib": a,
            "free_gib": a,
            "used_gib": 122.0 - a,
        }

    monkeypatch.setattr(memory_gate, "_meminfo", fake_meminfo)
    monkeypatch.setattr(memory_gate.time, "sleep", lambda *_: None)
    monkeypatch.setattr(memory_gate.time, "monotonic", lambda: 0.0)  # never times out
    out = io.StringIO()
    ok = memory_gate.gate(floor, timeout, 0.0, settle, out=out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    return ok, lines


def test_ready_only_after_the_floor_holds_for_settle_readings(monkeypatch):
    ok, lines = _run(monkeypatch, [20, 20, 20, 20], floor=10, settle=3)
    assert ok
    assert lines[-1]["result"] == "ready"
    # not declared ready before three consecutive good samples
    assert sum(1 for line in lines if line.get("settled")) == 1


def test_a_reading_below_the_floor_resets_the_count(monkeypatch):
    # good, good, DROP, good, good, good -> only the tail run of 3 counts
    ok, lines = _run(monkeypatch, [20, 20, 5, 20, 20, 20], floor=10, settle=3)
    assert ok
    # the dip must have reset progress, so readiness comes late, not at t=2
    settled_at = next(line["t"] for line in lines if line.get("settled"))
    assert settled_at >= 5


def test_timeout_when_the_floor_is_never_reached(monkeypatch):
    times = iter([0.0, 1.0, 2.0, 3.0, 99.0])
    monkeypatch.setattr(memory_gate.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(memory_gate.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        memory_gate,
        "_meminfo",
        lambda: {
            "total_gib": 122.0,
            "avail_gib": 2.0,
            "free_gib": 2.0,
            "used_gib": 120.0,
        },
    )
    out = io.StringIO()
    ok = memory_gate.gate(50.0, timeout=10.0, interval=0.0, settle_readings=3, out=out)
    assert not ok
    assert json.loads(out.getvalue().splitlines()[-1])["result"] == "timeout"
