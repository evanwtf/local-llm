"""start/stop/status for repo processes (#234).

These use real processes deliberately. The whole point of the module is what
the operating system does with pids, process groups and signals, and a mocked
`os.kill` would assert only that the code calls the functions it calls.
"""

from __future__ import annotations

import json
import pathlib
import signal
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import unitctl

# A unit that forks a child and waits, so "did stop reap the tree?" is a real
# question. `uv run python server.py` has exactly this shape: the recorded pid
# is the launcher, the thing holding the port is its child.
FORKING = ["bash", "-c", "sleep 60 & sleep 60; wait"]


@pytest.fixture
def state_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "units"


@pytest.fixture
def unit(state_dir: pathlib.Path):
    """Start a forking unit and guarantee it is gone afterwards."""
    started = unitctl.start("probe", FORKING, state_dir=state_dir)
    yield started
    unitctl.stop("probe", timeout=5, state_dir=state_dir)


def group_members(pid: int) -> list[int]:
    """Pids in the process group led by `pid`, straight from ps."""
    proc = subprocess.run(
        ["ps", "-o", "pid=", "-g", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    return [int(x) for x in proc.stdout.split()] if proc.returncode == 0 else []


# --- the lifecycle -----------------------------------------------------------


def test_status_is_answered_from_a_record_not_a_search(unit, state_dir):
    """No pattern, no ps scan: the pid was recorded when we spawned it."""
    assert unitctl.state(unitctl.read("probe", state_dir)) == unitctl.RUNNING
    stored = json.loads((state_dir / "probe.json").read_text())
    assert stored["pid"] == unit.pid
    assert stored["command"] == FORKING


def test_stop_reaps_the_children_not_just_the_recorded_pid(state_dir):
    """`uv run python server.py` records `uv`. Killing only that orphans the
    server, which then holds its port and its GPU memory forever. The unit is
    started in its own process group so stop can signal all of it."""
    started = unitctl.start("probe", FORKING, state_dir=state_dir)
    for _ in range(50):
        if len(group_members(started.pid)) >= 2:
            break
        time.sleep(0.05)
    assert len(group_members(started.pid)) >= 2, "the fixture never forked"
    unitctl.stop("probe", timeout=5, state_dir=state_dir)
    assert group_members(started.pid) == []


def test_stop_removes_the_record_so_status_reads_stopped(state_dir):
    unitctl.start("probe", FORKING, state_dir=state_dir)
    unitctl.stop("probe", timeout=5, state_dir=state_dir)
    assert unitctl.read("probe", state_dir) is None
    assert unitctl.state(None) == unitctl.STOPPED


def test_stopping_something_that_is_not_running_is_not_an_error(state_dir):
    """A driver's cleanup path runs on every exit, including the ones where
    the unit never started."""
    assert unitctl.stop("absent", state_dir=state_dir) == unitctl.STOPPED


def test_a_second_start_is_refused_rather_than_silently_doubled(unit, state_dir):
    """Two ds4 servers on one port is the failure this prevents: the second
    binds nothing, the first keeps serving, and the arm measures the old
    weights while the log describes the new ones."""
    with pytest.raises(RuntimeError, match="already running"):
        unitctl.start("probe", FORKING, state_dir=state_dir)


# --- the two traps a naive pidfile falls into --------------------------------


def test_a_dead_pid_reads_as_stale_not_running(state_dir):
    """A record outliving its process must never report `running`; a driver
    would then skip the start and measure nothing."""
    unitctl.start("probe", ["true"], state_dir=state_dir)
    for _ in range(50):
        if unitctl.state(unitctl.read("probe", state_dir)) != unitctl.RUNNING:
            break
        time.sleep(0.05)
    assert unitctl.state(unitctl.read("probe", state_dir)) == unitctl.STALE


def test_a_reused_pid_is_not_our_process(state_dir, monkeypatch):
    """Pids are reused. A stale record naming a pid the kernel has handed to
    something else would aim `stop` at an innocent process -- so the record
    also carries the process's own start time, and a mismatch is `stale`."""
    unitctl.start("probe", FORKING, state_dir=state_dir)
    try:
        assert unitctl.state(unitctl.read("probe", state_dir)) == unitctl.RUNNING
        monkeypatch.setattr(unitctl, "start_key", lambda pid: "a different boot time")
        assert unitctl.state(unitctl.read("probe", state_dir)) == unitctl.STALE
    finally:
        monkeypatch.undo()
        unitctl.stop("probe", timeout=5, state_dir=state_dir)


def test_stop_does_not_signal_a_pid_it_no_longer_owns(state_dir, monkeypatch):
    """The consequence of the test above: a stale record must be cleared, not
    acted on. Killing a pid the kernel has reassigned is how a supervisor takes
    down something it has never heard of."""
    started = unitctl.start("probe", FORKING, state_dir=state_dir)
    signalled: list[int] = []
    try:
        monkeypatch.setattr(unitctl, "start_key", lambda pid: "not ours")
        monkeypatch.setattr(
            unitctl, "_signal_group", lambda pid, sig: signalled.append(pid)
        )
        assert unitctl.stop("probe", state_dir=state_dir) == unitctl.STALE
        assert signalled == [], "a stale record must not be used to kill anything"
        assert group_members(started.pid), "and the process must still be alive"
    finally:
        monkeypatch.undo()
        unitctl._signal_group(started.pid, signal.SIGKILL)


def test_a_malformed_record_is_treated_as_absent(state_dir):
    """A half-written record must not crash a driver's cleanup path."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "probe.json").write_text("{ not json")
    assert unitctl.read("probe", state_dir) is None
    assert unitctl.state(unitctl.read("probe", state_dir)) == unitctl.STOPPED


def test_a_record_missing_its_pid_is_treated_as_absent(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "probe.json").write_text(json.dumps({"name": "probe"}))
    assert unitctl.read("probe", state_dir) is None


# --- names become filenames --------------------------------------------------


@pytest.mark.parametrize("name", ["../escape", "a/b", "", ".", ".."])
def test_a_name_that_would_escape_the_state_directory_is_refused(name, state_dir):
    with pytest.raises(ValueError):
        unitctl.record_path(name, state_dir)


# --- the CLI contract other scripts branch on --------------------------------


def test_status_exit_codes_follow_systemctl(state_dir):
    """0 running, 3 stopped -- so a shell driver can `if ! status; then start`
    without parsing any output."""
    assert unitctl.main(["--state-dir", str(state_dir), "status", "probe"]) == 3
    unitctl.start("probe", FORKING, state_dir=state_dir)
    try:
        assert unitctl.main(["--state-dir", str(state_dir), "status", "probe"]) == 0
    finally:
        unitctl.stop("probe", timeout=5, state_dir=state_dir)
    assert unitctl.main(["--state-dir", str(state_dir), "status", "probe"]) == 3


def test_the_command_is_split_on_a_standalone_double_dash(state_dir, tmp_path):
    """`argparse.REMAINDER` swallowed the options: `start p --log x -- cmd`
    parsed as command=['--log','x','--','cmd'] and tried to execute `--log`.
    The boundary must mean what it looks like."""
    log = tmp_path / "probe.log"
    rc = unitctl.main(
        ["--state-dir", str(state_dir), "start", "probe", "--log", str(log), "--"]
        + FORKING
    )
    try:
        assert rc == 0
        stored = json.loads((state_dir / "probe.json").read_text())
        assert stored["command"] == FORKING
        assert stored["log"] == str(log)
    finally:
        unitctl.stop("probe", timeout=5, state_dir=state_dir)


def test_start_with_no_command_is_an_error_not_an_empty_unit(state_dir):
    assert unitctl.main(["--state-dir", str(state_dir), "start", "probe"]) == 1
    assert unitctl.read("probe", state_dir) is None


def test_list_names_every_recorded_unit(state_dir):
    unitctl.start("one", FORKING, state_dir=state_dir)
    unitctl.start("two", FORKING, state_dir=state_dir)
    try:
        assert unitctl.units(state_dir) == ["one", "two"]
    finally:
        unitctl.stop("one", timeout=5, state_dir=state_dir)
        unitctl.stop("two", timeout=5, state_dir=state_dir)
    assert unitctl.units(state_dir) == []


def test_the_log_receives_the_units_output(state_dir, tmp_path):
    log = tmp_path / "out.log"
    unitctl.start(
        "probe", ["bash", "-c", "echo hello-from-unit"], log=log, state_dir=state_dir
    )
    for _ in range(50):
        if log.exists() and "hello-from-unit" in log.read_text():
            break
        time.sleep(0.05)
    unitctl.stop("probe", timeout=5, state_dir=state_dir)
    assert "hello-from-unit" in log.read_text()


def test_stop_is_prompt_when_the_unit_honors_sigterm(state_dir):
    """Every stop took 5.01s and ended in SIGKILL -- including `sleep`, which
    dies on SIGTERM instantly. The cause was not signalling: a child of ours
    becomes a ZOMBIE when it exits and stays one until reaped, and
    `os.kill(pid, 0)` succeeds on a zombie, so the wait loop never saw it die.

    It matters beyond speed. A ds4-server routinely SIGKILLed is one killed
    mid-write to its KV directory, and the timeout is per arm restart.
    """
    unitctl.start("probe", ["sleep", "60"], state_dir=state_dir)
    started = time.monotonic()
    unitctl.stop("probe", timeout=5, state_dir=state_dir)
    assert time.monotonic() - started < 2.0, "SIGTERM should be enough"


def test_a_zombie_is_not_reported_as_running(state_dir):
    """The check underneath. A process that has exited is not running, however
    politely it still answers signal 0."""
    started = unitctl.start("probe", ["true"], state_dir=state_dir)
    try:
        for _ in range(100):
            if not unitctl.alive(started.pid):
                break
            time.sleep(0.02)
        assert not unitctl.alive(started.pid)
        assert unitctl.state(unitctl.read("probe", state_dir)) == unitctl.STALE
    finally:
        unitctl.stop("probe", timeout=5, state_dir=state_dir)


def test_ps_failing_after_a_good_spawn_reads_stale(state_dir, monkeypatch):
    """Fail closed: an identity we can no longer confirm is not one we will
    signal. (I described this backwards on the PR; --deepseek caught it.)"""
    unitctl.start("probe", ["sleep", "60"], state_dir=state_dir)
    try:
        assert unitctl.state(unitctl.read("probe", state_dir)) == unitctl.RUNNING
        monkeypatch.setattr(unitctl, "start_key", lambda pid: None)
        assert unitctl.state(unitctl.read("probe", state_dir)) == unitctl.STALE
    finally:
        monkeypatch.undo()
        unitctl.stop("probe", timeout=5, state_dir=state_dir)


def test_ps_failing_at_spawn_still_reads_running(state_dir, monkeypatch):
    """The genuinely weaker case, and the right trade: refusing here would
    call a freshly-started unit dead."""
    monkeypatch.setattr(unitctl, "start_key", lambda pid: None)
    unitctl.start("probe", ["sleep", "60"], state_dir=state_dir)
    monkeypatch.undo()
    try:
        assert unitctl.read("probe", state_dir).start_key is None
        assert unitctl.state(unitctl.read("probe", state_dir)) == unitctl.RUNNING
    finally:
        unitctl.stop("probe", timeout=5, state_dir=state_dir)


def test_a_stale_unit_is_not_stopped_so_a_caller_must_test_for_running(state_dir):
    """The usage trap --deepseek named: `if state(u) == STOPPED: start()` will
    not restart a unit that died under it, because STALE is neither."""
    unitctl.start("probe", ["true"], state_dir=state_dir)
    for _ in range(100):
        if unitctl.state(unitctl.read("probe", state_dir)) != unitctl.RUNNING:
            break
        time.sleep(0.02)
    found = unitctl.state(unitctl.read("probe", state_dir))
    assert found == unitctl.STALE
    assert found != unitctl.STOPPED
    unitctl.stop("probe", timeout=5, state_dir=state_dir)


def test_a_spawn_that_cannot_be_recorded_is_stopped_not_leaked(state_dir, monkeypatch):
    """Found by --deepseek reviewing #245: the fifth teardown path.

    `start` spawns, then records. If the record never lands -- an unwritable
    state dir, a full disk -- the process is running and NOTHING can find it.
    `stop` reads the record, sees none, and reports `stopped` while the process
    holds its port and its GPU memory.

    The shell this replaces would have caught it: `pkill -f 'ds4-server
    --metal'` needs no record. Refusing to search is exactly what makes the
    recording load-bearing, so the spawn and the record must be
    all-or-nothing.
    """
    spawned: list[int] = []
    real_popen = unitctl.subprocess.Popen

    def watch(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc.pid)
        return proc

    real_write = pathlib.Path.write_text

    def fail_on_record(self, *args, **kwargs):
        if self.suffix == ".json":
            raise OSError("record write failed")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(unitctl.subprocess, "Popen", watch)
    monkeypatch.setattr(pathlib.Path, "write_text", fail_on_record)
    with pytest.raises(OSError, match="record write failed"):
        unitctl.start("probe", ["sleep", "60"], state_dir=state_dir)
    monkeypatch.undo()

    assert spawned, "the test is meaningless unless a process was really spawned"
    assert group_members(spawned[0]) == [], (
        "a process nothing can find must not survive"
    )
    assert unitctl.read("probe", state_dir) is None
    assert "probe" not in unitctl._OWNED


def test_an_interrupt_between_spawn_and_record_does_not_leak(state_dir, monkeypatch):
    """Ctrl-C is the likeliest way to end a long batch, and it lands here as a
    KeyboardInterrupt -- which is a BaseException, not an Exception."""
    spawned: list[int] = []
    real_popen = unitctl.subprocess.Popen

    def watch(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc.pid)
        return proc

    monkeypatch.setattr(unitctl.subprocess, "Popen", watch)
    monkeypatch.setattr(
        unitctl, "start_key", lambda pid: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        unitctl.start("probe", ["sleep", "60"], state_dir=state_dir)
    monkeypatch.undo()

    assert spawned
    assert group_members(spawned[0]) == []
    assert unitctl.read("probe", state_dir) is None
