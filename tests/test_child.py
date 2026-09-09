"""Stopping a driver must stop the thing that writes rows (#268).

On 2026-09-09 a driver was stopped with SIGINT. Its context managers stopped
the server, cleared the unit records and released the machine lock. Its
`run.py` child kept going for five more minutes and wrote three rows against a
server that no longer existed, on a machine the lock now advertised as free.

Everything here uses a `sleep` or a small Python script as the child. None of
it needs an engine, a GPU, or a model.
"""

from __future__ import annotations

import ast
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


def measurement_spawns(path: pathlib.Path) -> list[tuple[int, str, str]]:
    """`(line, how, argv-expression)` for every spawn of a COMPUTED command.

    The rule that separates the two kinds of subprocess a driver has:

    * a **list literal** is a fixed tool -- `git`, `lsof`, `pgrep`,
      `./ds4_test`, the KV audit. It spawns nothing of its own, finishes in
      milliseconds, and `subprocess.run` is right for it.
    * a **computed** argv -- `run_argv(...)`, `arm_argv(...)`, a bare `argv`
      parameter -- is a measurement command line. `run.py` re-spawns
      `opencode`, so it must go through `child.run`.

    Reading the argv expression rather than the callee name is what makes this
    checkable. The test it replaces asserted `"child.run(" in code` for ONE
    driver by name, and `route_agent_ab.py` satisfied it while still calling
    `subprocess.run` three times -- correctly, as it happens, for two `git`
    invocations and a `./ds4_test`. A substring cannot tell those apart from
    the measurement, so it passed while three other drivers spawned `run.py`
    with `subprocess.run`.
    """
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        how = ast.unparse(node.func)
        if how not in ("subprocess.run", "subprocess.Popen", "child.run"):
            continue
        argv = node.args[0]
        if isinstance(argv, ast.List):
            continue
        out.append((node.lineno, how, ast.unparse(argv)))
    return out


def drivers_that_spawn_the_harness() -> list[pathlib.Path]:
    return sorted(
        p
        for p in (ROOT / "scripts").glob("*.py")
        if "benchmarks/agent/run.py" in p.read_text()
    )


def test_every_driver_spawns_the_measurement_through_child_run() -> None:
    """The convention, over every driver, not one of them.

    Three carried `subprocess.run` on the measurement while their argv/env
    differentials were green -- and a differential cannot see this by
    construction: `child.run` and `subprocess.run` hand the child the same
    command line. What differs is only what happens when the driver is
    stopped, which is the axis no differential looks at.
    """
    assert drivers_that_spawn_the_harness(), "found no drivers; the glob is wrong"
    wrong = [
        f"{path.name}:{line} {how}({argv})"
        for path in drivers_that_spawn_the_harness()
        for line, how, argv in measurement_spawns(path)
        if how != "child.run"
    ]
    assert not wrong, "the measurement child must go through child.run (#268): " + (
        ", ".join(wrong)
    )


def test_a_fixed_tool_may_still_use_subprocess_run() -> None:
    """The exemption is real, and asserting it keeps the rule from over-reaching.

    `git rev-parse`, `lsof`, `pgrep` and the KV audit spawn nothing and finish
    at once. Routing them through `child.run` would buy nothing and would make
    the rule look arbitrary, which is how a rule stops being followed.
    """
    literals = [
        p.name
        for p in drivers_that_spawn_the_harness()
        if "subprocess.run(" in code_of(p)
    ]
    assert literals, (
        "no driver calls subprocess.run at all any more -- if that is "
        "deliberate, delete this test rather than letting it assert nothing"
    )


def test_subprocess_run_alone_leaves_the_grandchild(tmp_path) -> None:
    """Why the rule exists, demonstrated rather than asserted.

    The twin of test_the_whole_tree_dies_not_just_the_child. That one proves
    `child.terminate` takes the tree down; this one proves the thing it is
    being preferred over does not, so the convention carries its own evidence.
    """
    marker = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, pathlib, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))\n"
        "time.sleep(120)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.05)
    grandchild = int(marker.read_text())
    try:
        assert alive(grandchild)
        # What subprocess.run does on its way out: the immediate child, and
        # nothing else.
        proc.kill()
        proc.wait()
        time.sleep(0.5)
        assert alive(grandchild), (
            "the grandchild died without child.terminate, so this test proves "
            "nothing on this platform -- check the premise before trusting the rule"
        )
    finally:
        child.terminate(proc)


def test_env_merges_and_unset_removes(tmp_path, monkeypatch) -> None:
    """The absence case, which a dict cannot express.

    #149's withheld arm is `env -u DS4_METAL_ENABLE_TENSOR`. A merged dict has
    no way to say "not set": a key it does not mention is inherited, so an arm
    defined by an ABSENT variable silently becomes the other arm when the
    variable happens to be exported. That is not a hypothetical -- ds4 enables
    the tensor route by itself on any device whose name contains M5.
    """
    monkeypatch.setenv("CHILD_TEST_INHERITED", "yes")
    monkeypatch.setenv("CHILD_TEST_REMOVED", "yes")
    log = tmp_path / "env.log"
    rc = child.run(
        [
            sys.executable,
            "-c",
            (
                "import os;print("
                "os.environ.get('CHILD_TEST_INHERITED'),"
                "os.environ.get('CHILD_TEST_REMOVED'),"
                "os.environ.get('CHILD_TEST_ADDED'))"
            ),
        ],
        cwd=tmp_path,
        log=log,
        env={"CHILD_TEST_ADDED": "added"},
        unset=["CHILD_TEST_REMOVED"],
    )
    assert rc == 0
    assert log.read_text().split() == ["yes", "None", "added"]


def test_unsetting_a_variable_that_is_not_set_is_not_an_error(tmp_path) -> None:
    log = tmp_path / "noop.log"
    assert (
        child.run(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            log=log,
            unset=["CHILD_TEST_NEVER_SET"],
        )
        == 0
    )


def test_append_keeps_what_the_log_already_holds(tmp_path) -> None:
    """A driver writes the arm's definition first, so the probe can read it."""
    log = tmp_path / "arm.log"
    log.write_text("arm=withheld unset=DS4_METAL_ENABLE_TENSOR\n")
    rc = child.run(
        [sys.executable, "-c", "print('child ran')"],
        cwd=tmp_path,
        log=log,
        append=True,
    )
    assert rc == 0
    assert log.read_text().splitlines() == [
        "arm=withheld unset=DS4_METAL_ENABLE_TENSOR",
        "child ran",
    ]


def test_the_default_still_truncates(tmp_path) -> None:
    """append=False is the default, and a re-run must not read as one run."""
    log = tmp_path / "arm.log"
    log.write_text("stale\n")
    child.run([sys.executable, "-c", "print('fresh')"], cwd=tmp_path, log=log)
    assert log.read_text() == "fresh\n"
