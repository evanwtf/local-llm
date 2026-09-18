"""earlyoom's configuration, as code. #458

The setting under test is the one that decided whether the machine survived
2026-09-17: earlyoom acts only when memory AND swap are both under their
thresholds, so `-s 20,10` made it unfireable on a box whose swap never drains.
These tests pin that, and pin the drift check that says when the machine has
wandered away from the file.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import setup_earlyoom as se


def test_swap_is_ignored_because_the_thresholds_are_anded():
    """`-s 100,100` is the whole point. earlyoom v1.7: "both memory and swap
    must be below minimum for earlyoom to act." At 06:48 on 2026-09-17 memory
    was at 0.00% and swap at 91.45% free, so nothing fired."""
    assert "-s 100,100" in se.earlyoom_args()
    assert "-s 20,10" not in se.earlyoom_args()


def test_memory_thresholds_sit_below_the_per_server_floor():
    """dgx_server.py stops a server at 14 GiB (#456); earlyoom's 10% of
    121.7 GiB is about 12 GiB. The order matters: the wrapper should take a
    server down cleanly before the box-wide killer has to."""
    assert se.MEM_THRESHOLDS == "10,5"
    sigterm_pct = int(se.MEM_THRESHOLDS.split(",")[0])
    assert sigterm_pct / 100 * 121.7 < 14.0


def test_sshd_is_never_a_victim_and_the_engines_are_preferred():
    """Killing sshd is what turned the 2026-09-13 exhaustion into six
    physical reboots."""
    for name in ("sshd", "systemd", "dockerd", "earlyoom"):
        assert name in se.AVOID_REGEX, name
    for name in ("vllm", "llama-server", "ollama", "python"):
        assert name in se.PREFER_REGEX, name
    assert "sshd" not in se.PREFER_REGEX


def test_the_dropin_sets_both_priority_and_oom_immunity():
    """earlyoom's own -p fails under the unit's DynamicUser sandbox, so
    systemd has to do it."""
    body = se.dropin_file()
    assert "Nice=-20" in body
    assert "OOMScoreAdjust=-1000" in body
    assert "[Service]" in body


def test_both_files_say_where_they_are_managed_from():
    """A hand-edit is how this drifted in the first place."""
    for body in (se.defaults_file(), se.dropin_file()):
        assert "scripts/setup_earlyoom.py" in body


def test_the_defaults_file_records_why_swap_is_ignored():
    """The next reader will see -s 100,100 and wonder if it is a mistake. The
    incident line is the answer, in the file itself."""
    body = se.defaults_file()
    assert "91.45%" in body
    assert "AND" in body


def test_drift_is_reported_per_file(tmp_path, monkeypatch):
    defaults = tmp_path / "earlyoom"
    dropin = tmp_path / "priority.conf"
    monkeypatch.setattr(se, "DEFAULTS_PATH", defaults)
    monkeypatch.setattr(se, "DROPIN_PATH", dropin)

    # Nothing installed at all.
    assert len(se.drift()) == 2

    # The pre-#458 config: present, and unfireable.
    defaults.write_text('EARLYOOM_ARGS="-m 10,5 -s 20,10 -r 60"\n')
    dropin.write_text(se.dropin_file())
    reported = se.drift()
    assert len(reported) == 1
    assert "-s 20,10" in reported[0]

    # What this script writes.
    defaults.write_text(se.defaults_file())
    assert se.drift() == []


def test_a_comment_edit_is_not_reported_as_drift(tmp_path, monkeypatch):
    """The check compares the EARLYOOM_ARGS line, not the whole file, so an
    added comment does not read as a drifted safety setting."""
    defaults = tmp_path / "earlyoom"
    dropin = tmp_path / "priority.conf"
    monkeypatch.setattr(se, "DEFAULTS_PATH", defaults)
    monkeypatch.setattr(se, "DROPIN_PATH", dropin)
    defaults.write_text("# a note from the operator\n" + se.defaults_file())
    dropin.write_text(se.dropin_file())
    assert se.drift() == []


def test_a_changed_dropin_is_reported_whole(tmp_path, monkeypatch):
    """Every line in the drop-in is load-bearing, so it is compared whole."""
    defaults = tmp_path / "earlyoom"
    dropin = tmp_path / "priority.conf"
    monkeypatch.setattr(se, "DEFAULTS_PATH", defaults)
    monkeypatch.setattr(se, "DROPIN_PATH", dropin)
    defaults.write_text(se.defaults_file())
    dropin.write_text("[Service]\nNice=0\n")
    assert [d for d in se.drift() if "differs" in d]


def test_backup_is_dated_so_a_second_apply_keeps_the_original(tmp_path):
    """`earlyoom.bak-pre458` already holds the original config. A fixed backup
    name would overwrite the only record of it on the next apply."""
    path = tmp_path / "earlyoom"
    path.write_text("original")
    first = se._backup(path)
    assert first is not None and first.read_text() == "original"
    assert "bak-" in first.name
    assert se._backup(tmp_path / "absent") is None


def test_print_mode_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(se, "DEFAULTS_PATH", tmp_path / "earlyoom")
    monkeypatch.setattr(se, "DROPIN_PATH", tmp_path / "priority.conf")
    assert se.main(["--print"]) == 0
    assert "EARLYOOM_ARGS=" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_check_exits_1_on_drift_and_0_when_the_box_agrees(tmp_path, monkeypatch):
    defaults = tmp_path / "earlyoom"
    dropin = tmp_path / "priority.conf"
    monkeypatch.setattr(se, "DEFAULTS_PATH", defaults)
    monkeypatch.setattr(se, "DROPIN_PATH", dropin)
    assert se.main([]) == 1
    defaults.write_text(se.defaults_file())
    dropin.write_text(se.dropin_file())
    assert se.main([]) == 0
