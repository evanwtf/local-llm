"""Stopping a driver must stop the thing that writes rows (#268).

On 2026-09-09 a driver was stopped with SIGINT. Its context managers stopped
the server, cleared the unit records and released the machine lock. Its
`run.py` child kept going for five more minutes and wrote three rows against a
server that no longer existed, on a machine the lock now advertised as free.

Everything here uses a `sleep` or a small Python script as the child. None of
it needs an engine, a GPU, or a model.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import child
from source_text import code_of


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_child_that_exits_gives_back_its_status(tmp_path) -> None:
    log = tmp_path / "run.log"
    assert (
        child.run([sys.executable, "-c", "raise SystemExit(3)"], cwd=tmp_path, log=log)
        == 3
    )
    assert child.run([sys.executable, "-c", "print('ok')"], cwd=tmp_path, log=log) == 0
    assert "ok" in log.read_text()


def test_the_whole_tree_dies_not_just_the_child(tmp_path) -> None:
    """The grandchild is the one that outlived the driver.

    `run.py` re-spawns `opencode`, so killing the immediate child is not
    enough -- and `subprocess.run` does no more than that.
    """
    marker = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, pathlib, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))\n"
        "time.sleep(120)\n"
    )
    log = tmp_path / "tree.log"
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.05)
    grandchild = int(marker.read_text())
    assert alive(grandchild)

    child.terminate(proc)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and alive(grandchild):
        time.sleep(0.05)
    assert not alive(grandchild), "the grandchild outlived the teardown -- #268"
    assert proc.poll() is not None


def test_an_interruption_still_reaps_the_tree(tmp_path) -> None:
    """A driver stopped mid-arm must not leave its measurement running.

    The exception the driver is stopping for propagates; the tree comes down
    first.
    """
    started: dict[str, int] = {}
    real_popen = subprocess.Popen

    class Interrupting(real_popen):  # type: ignore[misc]
        def wait(self, timeout=None):
            if "pid" not in started:
                started["pid"] = self.pid
                raise KeyboardInterrupt
            return super().wait(timeout)

    subprocess.Popen = Interrupting  # type: ignore[misc]
    try:
        with pytest.raises(KeyboardInterrupt):
            child.run(
                [sys.executable, "-c", "import time; time.sleep(120)"],
                cwd=tmp_path,
                log=tmp_path / "i.log",
            )
    finally:
        subprocess.Popen = real_popen  # type: ignore[misc]

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and alive(started["pid"]):
        time.sleep(0.05)
    assert not alive(started["pid"])


def test_terminating_an_exited_child_is_a_no_op(tmp_path) -> None:
    proc = subprocess.Popen([sys.executable, "-c", ""], start_new_session=True)
    proc.wait()
    child.terminate(proc)  # must not raise, must not signal a recycled pid


def test_terminate_never_raises_on_a_vanished_group(tmp_path, monkeypatch) -> None:
    # It runs in a `finally`, often while an exception is already propagating.
    # A failure to reap must not replace the reason the driver is stopping.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        monkeypatch.setattr(
            child.os,
            "killpg",
            lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError),
        )
        child.terminate(proc)
    finally:
        monkeypatch.undo()
        proc.kill()
        proc.wait()


def test_the_driver_does_not_reach_for_subprocess_run_for_the_measurement() -> None:
    # The engine was always managed; the thing that writes rows was not.
    code = code_of(ROOT / "scripts" / "route_agent_ab.py")
    assert "child.run(" in code
