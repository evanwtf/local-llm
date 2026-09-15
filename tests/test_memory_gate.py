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


# --- macOS (#363): /proc/meminfo does not exist, so the gate crashed ---------

# `vm_stat` captured on the M5 Max (128 GiB) at 2026-09-14T23:43:34-0400, idle
# after a reboot. Trimmed to the counters the parser reads plus two it must
# skip, including the quoted "Translation faults" key.
VM_STAT_M5 = """\
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                  5695530.
Pages active:                                1059613.
Pages inactive:                               636639.
Pages speculative:                            631443.
Pages throttled:                                   0.
Pages wired down:                             230581.
Pages purgeable:                               44205.
"Translation faults":                       10293315.
File-backed pages:                           1399380.
"""
MEMSIZE_M5 = 137438953472  # sysctl hw.memsize: exactly 128 GiB


def test_vm_stat_parses_available_as_free_inactive_speculative_purgeable():
    m = memory_gate.parse_vm_stat(VM_STAT_M5, MEMSIZE_M5)
    # free 86.907 + inactive 9.714 + speculative 9.635 + purgeable 0.675 GiB
    assert m == {
        "total_gib": 128.0,
        "avail_gib": 106.9,
        "free_gib": 86.9,
        "used_gib": 21.1,
    }


def test_vm_stat_without_a_page_size_header_is_refused():
    # Apple silicon pages are 16 KiB. Assuming the 4 KiB x86 default would
    # report a quarter of the real headroom and hold every trial at the gate.
    body = VM_STAT_M5.split("\n", 1)[1]
    try:
        memory_gate.parse_vm_stat(body, MEMSIZE_M5)
    except ValueError as exc:
        assert "page size" in str(exc)
    else:
        raise AssertionError("parsed vm_stat with no page size header")


def test_vm_stat_missing_free_or_inactive_is_refused():
    for key in ("Pages free", "Pages inactive"):
        text = "\n".join(
            line for line in VM_STAT_M5.splitlines() if not line.startswith(key)
        )
        try:
            memory_gate.parse_vm_stat(text, MEMSIZE_M5)
        except ValueError as exc:
            assert key.lower() in str(exc).lower()
        else:
            raise AssertionError(f"parsed vm_stat with no {key!r}")


def test_vm_stat_missing_optional_counters_count_as_zero():
    text = "\n".join(
        line
        for line in VM_STAT_M5.splitlines()
        if not line.startswith(("Pages speculative", "Pages purgeable"))
    )
    m = memory_gate.parse_vm_stat(text, MEMSIZE_M5)
    # free 86.907 + inactive 9.714 GiB only
    assert m["avail_gib"] == 96.6


def test_meminfo_uses_vm_stat_on_darwin(monkeypatch):
    monkeypatch.setattr(memory_gate.sys, "platform", "darwin")
    monkeypatch.setattr(
        memory_gate, "_darwin_sources", lambda: (VM_STAT_M5, MEMSIZE_M5)
    )
    assert memory_gate._meminfo()["avail_gib"] == 106.9
