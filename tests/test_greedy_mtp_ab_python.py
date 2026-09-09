"""The Python greedy MTP A/B driver (#151/#39, stage 4 of #235).

`test_greedy_mtp_ab.py` covers the shell script this replaces, and stays until
#235's rule is met: the `.sh` is deleted only after its Python replacement has
produced a run that agrees with it. The two files are not duplicates -- that
one asserts on shell text it cannot execute, this one calls the code.

Every test here is a failure the shell version could have -- and in one case
did -- reach the machine with. A driver that runs for eleven hours unattended
gets exactly one chance to be right, so its refusals and its teardown are
worth more coverage than its happy path.
"""

from __future__ import annotations

import contextlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import greedy_mtp_ab as driver
from source_text import code_of

SHELL = ROOT / "scripts" / "greedy_mtp_ab.sh"


# --- the bug that cost an arm on 2026-09-08 ----------------------------------


def test_the_control_arm_builds_a_command_with_no_mtp_flags() -> None:
    """The shell held the MTP flags in an array, and an empty array is an
    *unbound variable* under `set -u` on bash 3.2. Only the control arm's array
    is empty, so the treatment arm ran all 15 tasks and its pair died at the
    first line. Half a night, and an A/B with no B.

    A list that is sometimes empty is not a special case in Python. This test
    exists so nobody reintroduces the special case."""
    command = driver.server_command(False, pathlib.Path("/tmp/kv"))
    assert command, "the control arm still needs a server"
    for flag in ("--mtp", "--mtp-model", "--mtp-draft", "--mtp-timing"):
        assert flag not in command, f"the control arm must not carry {flag}"


def test_the_treatment_arm_carries_the_mtp_flags() -> None:
    command = driver.server_command(True, pathlib.Path("/tmp/kv"))
    assert "--mtp-model" in command
    assert "7" in command, "draft length"
    assert "--mtp-timing" in command, "no timing, no drafting_share, no read-out"


def test_the_two_arms_use_different_kv_directories() -> None:
    """The MTP and non-MTP KV formats are incompatible and ds4 rejects the
    other's checkpoints. A shared directory leaves one arm re-prefilling every
    task, and the only symptom is that it looks slower -- which is precisely
    the quantity being measured."""
    assert driver.KV_MTP != driver.KV_PLAIN


# --- position bias, which alternation cancels only on an even count ----------


def test_the_arms_alternate_between_rounds() -> None:
    assert driver.arm_order(1) != driver.arm_order(2)


def test_each_arm_goes_first_equally_often_over_an_even_run() -> None:
    """Whichever arm runs first is faster in 9 of 12 reps, median +0.9% and
    +5.9% on a cold first rep (#130, #201). Alternation cancels that only when
    each arm leads the same number of times."""
    for rounds in (2, 4, 6, 8):
        leaders = [driver.arm_order(r)[0][0] for r in range(1, rounds + 1)]
        assert leaders.count("mtp") == leaders.count("plain") == rounds // 2


def test_an_odd_round_count_is_refused() -> None:
    assert driver.main(["--rounds", "3"]) == 2


def test_an_odd_round_count_can_be_forced() -> None:
    """Refusing is right; refusing without a way through is how a guard gets
    deleted instead of satisfied."""
    calls: list[int] = []
    with _patched(driver, "sweep", lambda r, *a, **k: calls.append(r) or 0):
        assert driver.main(["--rounds", "3", "--allow-odd-rounds"]) == 0
    assert calls == [3]


def test_an_even_round_count_runs() -> None:
    with _patched(driver, "sweep", lambda *a, **k: 0):
        assert driver.main(["--rounds", "2"]) == 0


# --- teardown, which the shell did with sed over `trap -p` -------------------


def test_the_shim_is_stopped_when_an_arm_raises(monkeypatch, tmp_path) -> None:
    """The shell chained EXIT traps by parsing `trap -p` with sed, because a
    second bare `trap` would silently discard the one releasing the run lock.
    Here the ordering is the nesting, and this asserts it survives a crash
    partway through -- a leftover shim on :8102 pins temperature 0 for whatever
    runs next, and nothing downstream would say so."""
    events = _wire(monkeypatch, tmp_path)

    def explode(*args: object, **kwargs: object) -> int:
        raise RuntimeError("the arm fell over")

    monkeypatch.setattr(driver, "run_arm", explode)
    with pytest.raises(RuntimeError):
        driver.sweep(2, 1, "b", tmp_path, 4242)
    assert "shim-stop" in events, "a leftover greedy shim silently pins the next run"
    assert "lock-release" in events, "an unreleased lock blocks every later run"


def test_the_lock_is_released_when_the_shim_never_answers(
    monkeypatch, tmp_path
) -> None:
    """The failure that must not deadlock the machine: acquire, then fail
    before any arm runs."""
    events = _wire(monkeypatch, tmp_path, shim_ready=False)
    with pytest.raises(RuntimeError):
        driver.sweep(2, 1, "b", tmp_path, 4242)
    assert "lock-release" in events
    assert "shim-stop" in events


def test_a_busy_machine_is_refused_and_nothing_starts(monkeypatch, tmp_path) -> None:
    events = _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(
        driver.preflight, "acquire_lock", lambda *a, **k: (False, "held by #39")
    )
    with pytest.raises(RuntimeError, match="held by #39"):
        driver.sweep(2, 1, "b", tmp_path, 4242)
    assert "shim-start" not in events, "nothing may start on a machine we do not hold"
    assert "lock-release" not in events, "and we must not release another run's lock"


# --- the shape of a full run -------------------------------------------------


def test_a_two_round_run_starts_four_servers_and_one_shim(
    monkeypatch, tmp_path
) -> None:
    """Two rounds, two arms: four servers. One shim for the whole run, not one
    per arm -- restarting it between arms would re-cost its startup inside the
    measured window."""
    events = _wire(monkeypatch, tmp_path)
    assert driver.sweep(2, 1, "b", tmp_path, 4242) == 0
    assert events.count("serve") == 4
    assert events.count("shim-start") == 1
    assert events.count("arm") == 4


def test_a_failed_arm_makes_the_whole_run_report_failure(monkeypatch, tmp_path) -> None:
    """The shell piped each arm through `tee` and lost the exit status to the
    pipe, so a driver that lost an arm still exited 0. That is how the
    2026-09-08 run reported nothing wrong while holding no control arm.

    The sweep still finishes -- the surviving arms are worth having -- but it
    must not read as clean."""
    events = _wire(monkeypatch, tmp_path)
    codes = iter([0, 7, 0, 0])

    def flaky(backend, tag, logdir, batch, trials):
        events.append("arm")
        return next(codes)

    monkeypatch.setattr(driver, "run_arm", flaky)
    assert driver.sweep(2, 1, "b", tmp_path, 4242) == 1
    assert events.count("arm") == 4, "one bad arm must not abort the rest"


def test_a_clean_run_reports_success(monkeypatch, tmp_path) -> None:
    events = _wire(monkeypatch, tmp_path)
    assert driver.sweep(2, 1, "b", tmp_path, 4242) == 0
    assert events.count("arm") == 4


def test_every_arm_gets_its_own_server_log(monkeypatch, tmp_path) -> None:
    """One log per arm, or `assert_graph` reads the previous arm's line and a
    control arm passes an MTP graph check it never ran."""
    logs: list[pathlib.Path] = []
    _wire(monkeypatch, tmp_path, server_logs=logs)
    driver.sweep(2, 1, "b", tmp_path, 4242)
    assert len(logs) == len(set(logs)) == 4


def test_each_arm_asserts_the_graph_it_asked_for(monkeypatch, tmp_path) -> None:
    """The whole result turns on this. Every MTP row published before
    2026-09-08 came from an arm whose graph line said `MTP=off`, and nothing in
    the harness noticed for weeks."""
    wants: list[bool] = []
    _wire(monkeypatch, tmp_path, wants=wants)
    driver.sweep(2, 1, "b", tmp_path, 4242)
    assert sorted(wants) == [False, False, True, True]


def test_the_arms_are_the_two_greedy_backends() -> None:
    """Both arms must pin temperature 0. A greedy MTP arm against a
    non-greedy control measures greedy decoding, not speculation."""
    backends = {b for r in (1, 2) for _, b in driver.arm_order(r)}
    assert backends == {"qwen38fnds4mtp7greedy", "qwen38fnds4greedy"}


def test_the_run_does_not_take_the_harness_lock_twice() -> None:
    """`run.py` claims the machine lock itself unless told not to. The driver
    holds it for the whole run, so every arm must pass --no-lock or the first
    arm refuses to start against the driver's own claim.

    This asserts on the argv the driver builds. An earlier draft of this test
    built the list itself and checked its own literal, which would have passed
    with the flag deleted from the driver entirely."""
    argv = driver.arm_argv(
        "qwen38fnds4greedy", "r1-plain", pathlib.Path("/tmp"), "b", 1
    )
    assert "--no-lock" in argv


def test_every_arm_records_a_draft_log_and_its_own_server_log() -> None:
    """No `--draft-log-engine ds4`, no cycle counts -- and the drafting share
    is the one number that says whether the MTP arm was an MTP arm."""
    argv = driver.arm_argv(
        "qwen38fnds4mtp7greedy", "r1-mtp", pathlib.Path("/tmp"), "b", 1
    )
    assert argv[argv.index("--draft-log-engine") + 1] == "ds4"
    assert argv[argv.index("--server-log") + 1].endswith("ds4server-r1-mtp.log")
    assert argv[argv.index("--backend") + 1] == "qwen38fnds4mtp7greedy"


# --- what the port must not have brought across ------------------------------


def test_the_driver_does_not_look_for_processes_by_name() -> None:
    """`pkill -f 'qwen_tool_shim.py --port 8102'` matches any process whose
    command line quotes that string -- including a shell that is about to run
    it, and including this driver's own `pgrep` waiter. #235 is the whole
    point: record the pid."""
    code = code_of(ROOT / "scripts" / "greedy_mtp_ab.py")
    assert "pgrep" not in code
    assert "pkill" not in code


def test_the_shell_it_replaces_is_still_here() -> None:
    """#235's binding rule: the `.sh` is deleted only after its Python
    replacement has produced a run that agrees with it. Until then both exist,
    and deleting the shell early is what makes disagreement unmeasurable."""
    assert SHELL.exists(), "see #235 -- do not delete until a run agrees"


# --- fixtures ----------------------------------------------------------------


@contextlib.contextmanager
def _patched(obj: object, name: str, value: object):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _wire(
    monkeypatch,
    tmp_path: pathlib.Path,
    *,
    shim_ready: bool = True,
    server_logs: list | None = None,
    wants: list | None = None,
) -> list[str]:
    """Replace every effect with a recorder. No process, no port, no server."""
    events: list[str] = []

    monkeypatch.setattr(
        driver.preflight, "acquire_lock", lambda *a, **k: (True, "ours")
    )
    monkeypatch.setattr(
        driver.preflight,
        "release_lock",
        lambda *a, **k: (events.append("lock-release"), (True, "released"))[1],
    )

    class FakeUnit:
        pid = 4243

    def fake_start(name, command, **kwargs):
        events.append("shim-start")
        return FakeUnit()

    monkeypatch.setattr(driver.unitctl, "start", fake_start)
    monkeypatch.setattr(
        driver.unitctl, "stop", lambda *a, **k: events.append("shim-stop") or "stopped"
    )
    monkeypatch.setattr(driver.unitctl, "read", lambda *a, **k: None)
    monkeypatch.setattr(driver.unitctl, "state", lambda *a: driver.unitctl.RUNNING)
    monkeypatch.setattr(driver, "port_answers", lambda *a, **k: shim_ready)

    @contextlib.contextmanager
    def fake_serving(command, log, *, cwd, model_id, want_mtp, port=0, **kwargs):
        events.append("serve")
        if server_logs is not None:
            server_logs.append(log)
        if wants is not None:
            wants.append(want_mtp)
        yield FakeUnit()

    monkeypatch.setattr(driver.ds4_server, "serving", fake_serving)

    def fake_run_arm(backend, tag, logdir, batch, trials):
        events.append("arm")
        return 0

    monkeypatch.setattr(driver, "run_arm", fake_run_arm)

    monkeypatch.setattr(driver, "KV_MTP", tmp_path / "kv-mtp")
    monkeypatch.setattr(driver, "KV_PLAIN", tmp_path / "kv-plain")
    if not shim_ready:
        monkeypatch.setattr(driver.time, "monotonic", _clock())
    return events


def _clock():
    """A monotonic clock that runs out immediately, so the readiness loop
    times out without a real wait."""
    ticks = iter([0.0] + [1e9] * 100)
    return lambda: next(ticks)
