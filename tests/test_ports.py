"""Identify a process by the port it holds (#235).

Every process-identification bug this repo has had came from matching a name,
so the module that replaces `pgrep` is the one that must not do it. These tests
are about the three ways "the port is held" can be wrong, because a refusal
that collapses them sends the reader to the wrong machine.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ports
from source_text import code_of


def test_answers_is_false_on_a_port_nothing_holds() -> None:
    # 1 is privileged and nothing in this project binds it.
    assert ports.answers(1, timeout=0.2) is False


def test_holder_is_none_when_nothing_listens(monkeypatch) -> None:
    monkeypatch.setattr(ports, "holder", lambda port: None)
    ok, why = ports.held_by(9, "anything")
    assert ok is False
    assert "nothing is listening on :9" == why


def test_held_by_names_the_process_when_it_is_the_wrong_one(monkeypatch) -> None:
    monkeypatch.setattr(ports, "holder", lambda port: 4242)
    monkeypatch.setattr(
        ports, "command_of", lambda pid: "/usr/bin/ds4-server --metal --port 8101"
    )
    ok, why = ports.held_by(8101, "qwen_tool_shim")
    assert ok is False
    # The whole command line, so the reader can see what to stop. A refusal
    # that says only "wrong process" is a refusal you have to debug.
    assert "ds4-server" in why and "4242" in why


def test_held_by_distinguishes_a_dead_holder_from_a_wrong_one(monkeypatch) -> None:
    monkeypatch.setattr(ports, "holder", lambda port: 4242)
    monkeypatch.setattr(ports, "command_of", lambda pid: None)
    ok, why = ports.held_by(8101, "qwen_tool_shim")
    assert ok is False
    assert "already gone" in why


def test_held_by_passes_when_the_marker_is_in_the_command(monkeypatch) -> None:
    monkeypatch.setattr(ports, "holder", lambda port: 7)
    monkeypatch.setattr(
        ports, "command_of", lambda pid: "python ds4_qwen_tool_shim.py --port 8101"
    )
    ok, why = ports.held_by(8101, "qwen_tool_shim")
    assert ok is True
    assert "pid 7" in why


def test_the_module_that_replaces_pgrep_does_not_call_pgrep() -> None:
    # `code_of` strips docstrings and comments: the explanation of why pgrep
    # was removed is the part worth keeping, and a plain `in` test deletes it.
    code = code_of(ROOT / "scripts" / "lib" / "ports.py")
    assert "pgrep" not in code
    assert "pkill" not in code
