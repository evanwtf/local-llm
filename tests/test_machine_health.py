"""Reboot detection in scripts/machine_health.py.

A reboot between turns wipes every server, the run lock's pid, and the session
scratchpad. Undetected it reads as a baffling run of "server died" / "stale
lock" findings; `boot_state` turns it into one line. This checks it fires
exactly once per reboot and never invents one.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import machine_health


def _redirect(monkeypatch, tmp_path, boot):
    monkeypatch.setattr(machine_health, "BOOT_MARK", tmp_path / "last-boot-id")
    monkeypatch.setattr(machine_health, "boot_id", lambda: boot)
    monkeypatch.setattr(machine_health, "uptime_seconds", lambda: 123.0)


def test_first_ever_call_is_not_a_reboot(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path, "boot-A")
    s = machine_health.boot_state()
    assert s["first_seen"] and not s["rebooted"]


def test_same_boot_id_is_not_a_reboot(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path, "boot-A")
    machine_health.boot_state()
    again = machine_health.boot_state()
    assert not again["rebooted"] and not again["first_seen"]


def test_a_changed_boot_id_is_a_reboot_once(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path, "boot-A")
    machine_health.boot_state()
    _redirect(monkeypatch, tmp_path, "boot-B")
    first = machine_health.boot_state()
    assert first["rebooted"], "the reboot must be reported"
    second = machine_health.boot_state()
    assert not second["rebooted"], "it must be reported once, not every call after"


def test_an_unreadable_boot_id_never_claims_a_reboot(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path, "boot-A")
    machine_health.boot_state()
    _redirect(monkeypatch, tmp_path, None)
    s = machine_health.boot_state()
    assert not s["rebooted"], "no current id means unknown, not rebooted"


def _quiet_machine(monkeypatch, tmp_path):
    """No dirty tree, no lock, nothing serving -- so only the memory guard can
    speak in check(). Every check() below is for a SERVER launch."""
    monkeypatch.setattr(machine_health, "LOCK", tmp_path / "run-lock.json")
    monkeypatch.setattr(machine_health, "tree_dirty", lambda: "")
    monkeypatch.setattr(machine_health, "served_model", lambda port: None)


def test_server_launch_blocked_while_departing_memory_is_held(monkeypatch, tmp_path):
    # A previous server freed its port but not its ~115 GiB: nothing is serving,
    # yet the pool is nearly full. Launching now is the #360 OOM.
    _quiet_machine(monkeypatch, tmp_path)
    monkeypatch.setattr(machine_health, "mem_held_gib", lambda: 115.0)
    problems = machine_health.check("server")
    assert any("#360" in p for p in problems), problems


def test_settled_memory_does_not_block_a_server_launch(monkeypatch, tmp_path):
    # The pool has been reclaimed (idle baseline). A launch must proceed.
    _quiet_machine(monkeypatch, tmp_path)
    monkeypatch.setattr(machine_health, "mem_held_gib", lambda: 5.0)
    assert machine_health.check("server") == []


def test_a_run_launch_ignores_held_memory(monkeypatch, tmp_path):
    # intent="run" launches no server, so it cannot cause the #360 OOM; a full
    # pool is the healthy server it is about to run against, not a blocker.
    _quiet_machine(monkeypatch, tmp_path)
    monkeypatch.setattr(machine_health, "mem_held_gib", lambda: 115.0)
    assert machine_health.check("run") == []


def test_held_memory_is_not_reported_when_a_port_is_serving(monkeypatch, tmp_path):
    # A resident, answering server legitimately holds the memory; the port
    # message covers it. The memory guard must not double-report the same server.
    _quiet_machine(monkeypatch, tmp_path)
    monkeypatch.setattr(machine_health, "served_model", lambda port: "some-model")
    monkeypatch.setattr(machine_health, "mem_held_gib", lambda: 115.0)
    problems = machine_health.check("server")
    assert problems and not any("#360" in p for p in problems), problems


def test_unreadable_meminfo_never_blocks_a_launch(monkeypatch, tmp_path):
    # If /proc/meminfo cannot be read, mem_held_gib returns None; unknown must
    # not be treated as full, or a launch is blocked on missing information.
    _quiet_machine(monkeypatch, tmp_path)
    monkeypatch.setattr(machine_health, "mem_held_gib", lambda: None)
    assert machine_health.check("server") == []
