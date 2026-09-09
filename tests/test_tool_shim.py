"""The tool-format shim's lifecycle (#112, #235).

The strip is worth 23 points of pass rate. A shim in the other mode does not
fail; it silently produces the other experiment. So the mode is read back out
of the shim's own startup line and never inferred from the variable that was
set.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import tool_shim
import unitctl
from source_text import code_of


def test_strip_on_removes_the_variable_rather_than_clearing_it() -> None:
    # The shim tests the variable's PRESENCE. Setting it empty is still
    # present, and an operator with it exported would hand the off mode to the
    # on arm -- the same absence the #149 R arm is defined by.
    env, unset = tool_shim.strip_env(True)
    assert env == {}
    assert unset == ("SHIM_NO_STRIP",)


def test_strip_off_sets_it() -> None:
    env, unset = tool_shim.strip_env(False)
    assert env == {"SHIM_NO_STRIP": "1"}
    assert unset == ()


def test_two_ports_are_two_units() -> None:
    # greedy_mtp_ab runs a temperature-pinned shim on :8102 while :8101 keeps
    # serving the 262 rows that already compare. One unit name would have the
    # second stop the first.
    assert tool_shim.unit_name(8101) != tool_shim.unit_name(8102)


def test_a_missing_log_has_not_said_anything(tmp_path) -> None:
    assert tool_shim.mode_line(tmp_path / "absent.log") is None


def test_the_mode_is_read_from_the_log(tmp_path) -> None:
    log = tmp_path / "shim.log"
    log.write_text("listening on 8101\nscaffolding strip: ON\n")
    assert tool_shim.mode_line(log) == "scaffolding strip: ON"


def test_a_shim_in_the_other_mode_is_refused(tmp_path, monkeypatch) -> None:
    log = tmp_path / "shim.log"
    log.write_text("scaffolding strip: OFF\n")
    monkeypatch.setattr(tool_shim.ports, "answers", lambda port, timeout=1.0: True)
    monkeypatch.setattr(unitctl, "read", lambda name, state_dir=None: object())
    monkeypatch.setattr(unitctl, "state", lambda unit: unitctl.RUNNING)
    with pytest.raises(tool_shim.WrongMode) as caught:
        tool_shim._wait("tool-shim-8101", log, 8101, True, 1.0)
    assert "23 points" in str(caught.value)


def test_the_right_mode_passes(tmp_path, monkeypatch) -> None:
    log = tmp_path / "shim.log"
    log.write_text("scaffolding strip: ON\n")
    monkeypatch.setattr(tool_shim.ports, "answers", lambda port, timeout=1.0: True)
    monkeypatch.setattr(unitctl, "read", lambda name, state_dir=None: object())
    monkeypatch.setattr(unitctl, "state", lambda unit: unitctl.RUNNING)
    tool_shim._wait("tool-shim-8101", log, 8101, True, 1.0)


def test_a_shim_that_exited_is_not_waited_on(tmp_path, monkeypatch) -> None:
    log = tmp_path / "shim.log"
    log.write_text("")
    monkeypatch.setattr(tool_shim.ports, "answers", lambda port, timeout=1.0: False)
    monkeypatch.setattr(unitctl, "read", lambda name, state_dir=None: None)
    monkeypatch.setattr(unitctl, "state", lambda unit: unitctl.STOPPED)
    with pytest.raises(tool_shim.NeverReady) as caught:
        tool_shim._wait("tool-shim-8101", log, 8101, True, 5.0)
    assert "exited" in str(caught.value)


def test_the_module_that_replaces_pkill_calls_none() -> None:
    code = code_of(ROOT / "scripts" / "lib" / "tool_shim.py")
    assert "pkill" not in code
    assert "pgrep" not in code
