"""The server teardown helper: `scripts/lib/ds4_server.sh` (#145).

`stack_agent_ab.sh` leaked its last model server on every clean finish -- four
runs in a row, most recently 97.9 GiB -- because the final `restart_server` had
no matching stop. It blocked the next run and preflight reported the machine
healthy, since a leftover from a finished run looks exactly like a server the
current run needs.

These tests drive the helper with a fake `pgrep`/`pkill` on PATH, so they need
no ds4-server and no Metal. What they check is the part that was missing: that
teardown happens on **every** exit path, that the exit status survives it, and
that arming it does not silently discard a trap somebody else installed.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import textwrap

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
HELPER = REPO / "scripts" / "lib" / "ds4_server.sh"


@pytest.fixture
def fake_ps(tmp_path: pathlib.Path) -> pathlib.Path:
    """A PATH where a 'server' runs until something kills it.

    `state` holds the pretend process. pgrep reports it, pkill removes it, and
    both record every call so a test can say what the script actually did.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "state").write_text("running\n")
    (bin_dir / "pgrep").write_text(
        textwrap.dedent(f"""\
        #!/bin/sh
        echo "pgrep $*" >> {tmp_path}/calls
        [ "$(cat {tmp_path}/state)" = "running" ] || exit 1
        echo 4242
        """)
    )
    (bin_dir / "pkill").write_text(
        textwrap.dedent(f"""\
        #!/bin/sh
        echo "pkill $*" >> {tmp_path}/calls
        echo stopped > {tmp_path}/state
        """)
    )
    for tool in ("pgrep", "pkill"):
        (bin_dir / tool).chmod(0o755)
    return tmp_path


def run_script(fake_ps: pathlib.Path, body: str, send_int: bool = False):
    script = fake_ps / "run.sh"
    script.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\nsource {HELPER}\n" + textwrap.dedent(body)
    )
    script.chmod(0o755)
    env = {"PATH": f"{fake_ps / 'bin'}:/usr/bin:/bin", "HOME": str(fake_ps)}
    proc = subprocess.Popen(
        ["bash", str(script)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # Its own process group, so the interrupt below can be delivered the
        # way a terminal delivers one.
        start_new_session=True,
    )
    if send_int:
        # **To the group, not to the shell.** bash running `sleep 30` in the
        # foreground does not act on a signal sent only to itself until the
        # child returns -- signalling just the shell made this test hang for
        # the full 30 seconds. Ctrl-C at a terminal signals the whole
        # foreground process group, and that is what has to be emulated, or
        # the test passes on a mechanism the real interrupt never uses.
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    out, _ = proc.communicate(timeout=30)
    return proc.returncode, out, (fake_ps / "state").read_text().strip()


def test_a_clean_finish_stops_the_server(fake_ps):
    """The exact #145 case: all sweeps complete, and the last server stays up."""
    code, out, state = run_script(fake_ps, """
        ds4_arm_stop_trap
        echo "all 8 sweeps complete"
    """)
    assert code == 0
    assert state == "stopped", "a finished run must not leave 98 GiB resident"


def test_an_interrupted_run_stops_the_server(fake_ps):
    """Ctrl-C is the likeliest way a five-hour batch ends."""
    code, out, state = run_script(fake_ps, """
        ds4_arm_stop_trap
        sleep 10
    """, send_int=True)
    assert state == "stopped"
    assert code != 0


def test_a_failing_run_stops_the_server_and_keeps_its_status(fake_ps):
    """Teardown is a side effect. It must not turn a failed run into a pass."""
    code, out, state = run_script(fake_ps, """
        ds4_arm_stop_trap
        exit 3
    """)
    assert state == "stopped"
    assert code == 3, "the run's own exit status is the one that matters"


def test_a_successful_run_keeps_its_zero(fake_ps):
    code, _, _ = run_script(fake_ps, """
        ds4_arm_stop_trap
        true
    """)
    assert code == 0


def test_arming_the_trap_does_not_discard_an_existing_one(fake_ps):
    """`restart_between_trials.sh` traps EXIT to release the preflight lock.

    A bare `trap ... EXIT` would replace it, and the lock would outlive the run
    that took it -- trading a leaked server for a leaked lock.
    """
    code, out, state = run_script(fake_ps, f"""
        trap 'echo released > {fake_ps}/lock' EXIT
        ds4_arm_stop_trap
        echo done
    """)
    assert code == 0
    assert state == "stopped"
    assert (fake_ps / "lock").read_text().strip() == "released"


def test_stopping_a_machine_with_no_server_is_silent_and_succeeds(fake_ps):
    """Nothing running is the normal case before a run. It is not an error."""
    (fake_ps / "state").write_text("stopped\n")
    code, out, _ = run_script(fake_ps, """
        ds4_stop_server "no server here"
        echo "exit=$?"
    """)
    assert code == 0
    assert "exit=0" in out
    assert "stopping ds4-server" not in out
    assert not (fake_ps / "calls").exists() or "pkill" not in (fake_ps / "calls").read_text()


def test_a_server_that_will_not_die_is_reported_rather_than_ignored(fake_ps):
    """A SIGKILL that does not take it is the one case worth a loud refusal."""
    (fake_ps / "bin" / "pkill").write_text("#!/bin/sh\nexit 0\n")  # kills nothing
    (fake_ps / "bin" / "pkill").chmod(0o755)
    code, out, _ = run_script(fake_ps, """
        set +e
        ds4_stop_server "wedged"
        echo "exit=$?"
    """)
    assert "REFUSING: ds4-server would not stop" in out
    assert "exit=1" in out
