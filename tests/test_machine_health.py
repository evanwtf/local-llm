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
