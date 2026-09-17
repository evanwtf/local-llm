"""The OOM watchdog: what counts as an event, and what gets restarted. #459

The failure this guards is subtle in one direction and expensive in the other.
Reporting a kill that never happened writes a false save into an incident
record; restarting a unit that is merely starting up fights systemd. Both are
decisions made from one line of text, so the lines are pinned here verbatim
from the two real incidents.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import oom_watchdog as ow

# Verbatim from the 2026-09-13 lockup (#360, preserved in the incident doc).
KERNEL_LINE = (
    "2026-09-13T07:47:06-0400 spark-231e kernel: oom-kill:"
    "constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,mems_allowed=0,"
    "global_oom,task_memcg=/user.slice,task=VLLM::EngineCor,pid=2298697,uid=1000"
)
KILLED_LINE = (
    "2026-09-13T07:47:06-0400 spark-231e kernel: Out of memory: "
    "Killed process 2298697 (VLLM::EngineCor) total-vm:155592932kB"
)
# Verbatim from the #362 validation, the one time earlyoom did fire.
EARLYOOM_KILL_LINE = (
    "2026-09-14T19:16:25-0400 spark-231e earlyoom[373869]: sending SIGTERM to "
    'process 373870 uid 1000 "python3": badness 1546, VmRSS 108256 MiB'
)
# Verbatim from the 2026-09-17 lockup: 0% available and NOT a kill, because
# earlyoom's swap condition was never met (#458).
EARLYOOM_REPORT_LINE = (
    "2026-09-17T06:48:20-0400 spark-231e earlyoom[1523]: mem avail: "
    "0 of 124610 MiB ( 0.00%), swap free: 14983 of 16383 MiB (91.45%)"
)


def test_a_kernel_oom_kill_is_an_event():
    assert ow.classify(KERNEL_LINE) == "kernel-oom"
    assert ow.classify(KILLED_LINE) == "kernel-oom"


def test_an_earlyoom_kill_is_an_event():
    assert ow.classify(EARLYOOM_KILL_LINE) == "earlyoom-kill"


def test_earlyooms_zero_percent_report_is_not_a_kill():
    """The 2026-09-17 line. It reads as the worst moment of the day and means
    only that earlyoom was watching -- it killed nothing, which is why the box
    locked up. Counting it as a kill would record a save that never happened.
    """
    assert ow.classify(EARLYOOM_REPORT_LINE) is None


def test_ordinary_journal_noise_is_not_an_event():
    for line in (
        "2026-09-17T06:46:20-0400 spark-231e kernel: nvfanread arm-ffa-17: telemetry read OK",
        '2026-09-17T06:46:28-0400 spark-231e ollama[2070]: [GIN] 200 | GET "/api/tags"',
        "2026-09-17T06:52:14-0400 spark-231e earlyoom[1530]: sending SIGTERM when mem <= 10.00%",
    ):
        assert ow.classify(line) is None, line


def test_events_keeps_order_and_kind():
    found = ow.events(f"{EARLYOOM_REPORT_LINE}\n{KERNEL_LINE}\n{EARLYOOM_KILL_LINE}")
    assert [e["kind"] for e in found] == ["kernel-oom", "earlyoom-kill"]
    assert "VLLM::EngineCor" in found[0]["line"]


def test_an_inactive_unit_is_restarted(tmp_path, monkeypatch):
    restarted = []
    monkeypatch.setattr(ow, "read_journal", lambda since: "")
    monkeypatch.setattr(ow, "unit_active", lambda unit: unit != "ssh.service")
    monkeypatch.setattr(ow, "restart", lambda unit, dry: restarted.append(unit) or True)
    result = ow.sweep("-10min", state_path=tmp_path / "s.json")
    assert restarted == ["ssh.service"]
    assert result["restarted"] == ["ssh.service"]


def test_an_unknown_unit_state_is_never_restarted(tmp_path, monkeypatch):
    """`systemctl` unreachable, or a transitional `activating`: unknown is not
    down, and a restart issued into it races systemd for no reason."""
    monkeypatch.setattr(ow, "read_journal", lambda since: "")
    monkeypatch.setattr(ow, "unit_active", lambda unit: None)
    monkeypatch.setattr(
        ow,
        "restart",
        lambda unit, dry: (_ for _ in ()).throw(AssertionError("restarted on unknown")),
    )
    result = ow.sweep("-10min", state_path=tmp_path / "s.json")
    assert result["restarted"] == []


def test_dry_run_reports_without_restarting(tmp_path, monkeypatch):
    monkeypatch.setattr(ow, "read_journal", lambda since: KERNEL_LINE)
    monkeypatch.setattr(ow, "unit_active", lambda unit: False)
    calls = []

    def fake_restart(unit, dry_run):
        calls.append((unit, dry_run))
        return False  # what restart() returns in dry-run

    monkeypatch.setattr(ow, "restart", fake_restart)
    result = ow.sweep("-10min", dry_run=True, state_path=tmp_path / "s.json")
    assert [c[1] for c in calls] == [True, True]
    assert result["restarted"] == []
    assert len(result["events"]) == 1


def test_a_pass_is_recorded_for_the_next_reader(tmp_path, monkeypatch):
    """The journal rotates. #390 lost the 2026-09-13 kernel lines that way, so
    a pass writes what it saw where an incident write-up can find it."""
    state = tmp_path / "s.json"
    monkeypatch.setattr(ow, "read_journal", lambda since: KILLED_LINE)
    monkeypatch.setattr(ow, "unit_active", lambda unit: True)
    ow.sweep("-10min", state_path=state)
    stored = json.loads(state.read_text())
    assert stored["events"][0]["kind"] == "kernel-oom"
    assert stored["units"]["ssh.service"] is True
    assert stored["since"] == "-10min"
    assert stored["checked"]


def test_an_unwritable_state_path_does_not_fail_the_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(ow, "read_journal", lambda since: "")
    monkeypatch.setattr(ow, "unit_active", lambda unit: True)
    result = ow.sweep("-10min", state_path=tmp_path / "nope" / "x" / "s.json")
    assert result["events"] == []


def test_main_exits_1_when_there_is_something_to_report(tmp_path, monkeypatch):
    monkeypatch.setattr(ow, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(ow, "read_journal", lambda since: KERNEL_LINE)
    monkeypatch.setattr(ow, "unit_active", lambda unit: True)
    assert ow.main(["--once"]) == 1
    monkeypatch.setattr(ow, "read_journal", lambda since: "")
    assert ow.main(["--once"]) == 0


def test_the_timer_and_service_units_are_shipped_and_coherent():
    """The units are the installable half of #459; a typo in `Unit=` makes a
    timer that fires nothing, which looks exactly like a quiet box."""
    root = pathlib.Path(__file__).resolve().parents[1]
    service = (root / "systemd" / "local-llm-oom-watchdog.service").read_text()
    timer = (root / "systemd" / "local-llm-oom-watchdog.timer").read_text()
    assert "scripts/oom_watchdog.py --once" in service
    # It reacts to the event that kills ordinary processes, so it must not be
    # an ordinary candidate itself.
    assert "OOMScoreAdjust=-1000" in service
    assert "MemoryMax=" in service
    assert "Unit=local-llm-oom-watchdog.service" in timer
    assert "OnUnitActiveSec=" in timer
    assert "WantedBy=timers.target" in timer


def test_a_relative_window_is_passed_through(tmp_path, monkeypatch):
    """`--since=-24h` must reach journalctl verbatim. It needs the `=`, since
    argparse reads a bare `-24h` as another flag -- which is a usage error, not
    a quiet default, so this pins the working form."""
    seen = {}
    monkeypatch.setattr(ow, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(
        ow, "read_journal", lambda since: seen.setdefault("since", since) or ""
    )
    monkeypatch.setattr(ow, "unit_active", lambda unit: True)
    assert ow.main(["--once", "--since=-24h"]) == 0
    assert seen["since"] == "-24h"


def test_json_mode_keeps_stdout_parseable(tmp_path, monkeypatch, capsys):
    """Log records default to stdout, which would put text in front of the
    object and make `--json | jq` fail on a pass that found something."""
    monkeypatch.setattr(ow, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(ow, "read_journal", lambda since: KERNEL_LINE)
    monkeypatch.setattr(ow, "unit_active", lambda unit: True)
    ow.main(["--once", "--json"])
    out = capsys.readouterr()
    # Only stdout is asserted: root logging is process-global, so whichever
    # test configured it first owns the handler, and the stream this call asks
    # for may already be set. stdout staying clean is the contract that matters.
    assert json.loads(out.out)["events"][0]["kind"] == "kernel-oom"
