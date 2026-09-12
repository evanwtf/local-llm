"""Restart-between-trials: two shells against the one port (#235, #112, #77).

`restart_between_trials.sh` (arm A) and `restart_between_trials_armB.sh`
(arm B) are both replaced by `restart_between_trials.py`, so this differential
runs THREE drivers and compares two pairs.

Merging two shells into one module is the change most likely to lose an arm,
and the port's own docstring says why it was worth the risk: arm B's shell
started a server with `--mtp-timing` and never passed `--server-log`, so every
counter the engine emitted went to a file nothing read -- three cycles
asserting only that the flag had been passed. Arm A had no counters to pass,
so the bug could only exist in the copy, and did. That is the case for one
code path; this file is the check that the merge did not take an arm with it.

## The one place the port does NOT match arm A's shell

Arm A's shell passes no `--server-log`. The port passes it for both arms,
deliberately: it is the fix for the arm B bug above, and a code path that
passes it only sometimes is the shape that produced the bug.

That difference is not exempted here. It is asserted to be EXACTLY one pair,
which is the stronger statement: it says the divergence is the one we chose
and that nothing else drifted alongside it.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHELL_A = ROOT / "vault" / "restart_between_trials.sh"
SHELL_B = ROOT / "vault" / "restart_between_trials_armB.sh"

for sub in ("scripts", "scripts/lib", "benchmarks/agent"):
    sys.path.insert(0, str(ROOT / sub))

import equiv
import restart_between_trials as driver
import wait_ready

TRIALS = 3

# COLUMNS/LINES are terminal geometry, injected by bash when it has a tty and
# absent when it does not. They describe the window the comparison was run
# from, never the run, and they made these differentials fail on a machine
# whose shell reported a size ("run 1 env differs": COLUMNS=80, LINES=24).
_SHELL_ARTIFACTS = frozenset({"PWD", "OLDPWD", "SHLVL", "_", "COLUMNS", "LINES"})


def _meaningful_env(env: dict[str, str]) -> dict[str, str]:
    return {
        k: v
        for k, v in env.items()
        if k not in equiv.CONTROLLED_ENV_KEYS
        and k not in _SHELL_ARTIFACTS
        and k not in ("LOGDIR", "BENCH_LOGS")
    }


@contextlib.contextmanager
def _noop(*args, **kwargs):
    yield


def _fakes(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    out = tmp_path / "rec.jsonl"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    equiv.write_uv_fake_running_real(shim_dir / "uv", out, ROOT)
    equiv.write_fake_pgrep(shim_dir)
    equiv.write_fake_pkill(shim_dir)
    equiv.write_fake_ds4_server(tmp_path / "home" / "git" / "ds4-metal", out, ROOT)
    return out, shim_dir


def _shell(
    script: pathlib.Path,
    tmp_path: pathlib.Path,
    out: pathlib.Path,
    shim_dir: pathlib.Path,
    arm: str,
) -> None:
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{shim_dir}:{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path / "home"),
            "EQUIV_OUT": str(out),
            "EQUIV_ARM": f"shell{arm}",
            "LOGDIR": str(tmp_path / f"logs{arm}"),
            "BENCH_LOGS": str(tmp_path / "bench-logs"),
        }
    )
    (tmp_path / f"logs{arm}").mkdir(exist_ok=True)
    (tmp_path / "bench-logs").mkdir(exist_ok=True)
    got = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert got.returncode == 0, f"{script.name} failed:\n{got.stdout}\n{got.stderr}"


def _port(
    arm: str,
    tmp_path: pathlib.Path,
    out: pathlib.Path,
    shim_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The port's `cycle`, real except where it would touch the machine.

    `serving` is NOT stubbed: it spawns the fake ds4-server, which is half of
    what this differential compares. `child.run` is not stubbed either -- it
    spawns `uv run python run.py` through the fake `uv` on PATH.
    """
    home = tmp_path / "home"
    monkeypatch.setattr(driver, "port_answers", lambda *a, **k: True)
    monkeypatch.setattr(
        driver.preflight, "acquire_lock", lambda *a, **k: (True, "ours")
    )
    monkeypatch.setattr(
        driver.preflight, "release_lock", lambda *a, **k: (True, "released")
    )
    monkeypatch.setattr(driver, "kv_prefix_audit", lambda *a, **k: None)
    monkeypatch.setattr(
        wait_ready, "ready", lambda *a, **k: equiv.wait_for_program(out, "ds4-server")
    )
    monkeypatch.setattr(driver, "DS4_TREE", home / "git" / "ds4-metal")
    models = home / "models" / "qwen3.8-flash-next-ds4-q4"
    monkeypatch.setattr(
        driver,
        "DS4_MODEL",
        models
        / (
            "Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-"
            "Q8QSA-Q8Shared-Q8Out.gguf"
        ),
    )
    monkeypatch.setattr(driver, "DS4_PLE", models / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf")
    monkeypatch.setattr(driver, "DS4_MTP", models / "qwen3.8-flash-next-q4-mtp.gguf")
    spec = driver.ARMS[arm]
    kv = home / ".ds4" / ("server-kv-mtp" if arm == "B" else "server-kv")
    monkeypatch.setattr(driver, "ARMS", {**driver.ARMS, arm: _with_kv(spec, kv)})
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EQUIV_OUT", str(out))
    monkeypatch.setenv("EQUIV_ARM", f"port{arm}")
    rc = driver.cycle(
        driver.ARMS[arm],
        tmp_path / f"logs{arm}",
        tmp_path / "bench-logs",
        os.getpid(),
        trials=TRIALS,
    )
    assert rc == 0, f"port arm {arm} failed rc={rc}"


def _with_kv(spec, kv: pathlib.Path):
    import dataclasses

    return dataclasses.replace(spec, kv=kv)


def _of(invs, program: str, arm: str) -> list[equiv.Invocation]:
    return sorted(
        (i for i in invs if i.program == program and i.arm == arm),
        key=lambda i: i.argv,
    )


def _both(arm: str, tmp_path, monkeypatch) -> list[equiv.Invocation]:
    out, shim_dir = _fakes(tmp_path)
    _shell(SHELL_A if arm == "A" else SHELL_B, tmp_path, out, shim_dir, arm)
    _port(arm, tmp_path, out, shim_dir, monkeypatch)
    return equiv.load(out)


def test_both_scripts_parse() -> None:
    for script in (SHELL_A, SHELL_B):
        subprocess.run(["bash", "-n", str(script)], check=True)


@pytest.mark.parametrize("arm", ["A", "B"])
def test_the_shell_and_the_port_start_the_same_servers(arm, tmp_path, monkeypatch):
    """Three servers per side, and for arm B the MTP flags are the arm.

    Compared as a set: the three restarts of one arm use identical argv, and
    the experiment is that there are THREE of them, which is asserted by count.
    """
    invs = _both(arm, tmp_path, monkeypatch)
    s = _of(invs, "ds4-server", f"shell{arm}")
    p = _of(invs, "ds4-server", f"port{arm}")
    assert len(s) == len(p) == TRIALS, (
        f"a fresh server per trial IS the experiment: shell {len(s)}, port {len(p)}"
    )
    s_set = {equiv.canonical(i.argv, frozenset()) for i in s}
    p_set = {equiv.canonical(i.argv, frozenset()) for i in p}
    assert s_set == p_set, f"arm {arm}: shell server argv {s_set} vs port {p_set}"

    mtp = any(x[0] == "--mtp-model" for x in next(iter(s_set)))
    assert mtp is (arm == "B"), f"arm {arm} carries mtp flags: {mtp}"
    if arm == "B":
        pairs = next(iter(s_set))
        assert ("--mtp-draft", "7") in pairs
        assert any(x[0] == "--mtp-timing" for x in pairs)
    # The two SIDES must agree; neither is expected to be empty. A driver runs
    # in whatever environment the operator has, and both inherit it -- the
    # question is only whether either one adds something the other does not.
    s_env = {tuple(sorted(_meaningful_env(i.env).items())) for i in s}
    p_env = {tuple(sorted(_meaningful_env(i.env).items())) for i in p}
    assert s_env == p_env, f"arm {arm}: the server env differs between sides"


def test_arm_b_matches_its_shell_exactly(tmp_path, monkeypatch) -> None:
    """No deviation on arm B: its shell already passes `--server-log`."""
    invs = _both("B", tmp_path, monkeypatch)
    s = _of(invs, "run.py", "shellB")
    p = _of(invs, "run.py", "portB")
    assert len(s) == len(p) == TRIALS
    for a, b in zip(s, p, strict=True):
        assert set(equiv.canonical(a.argv, frozenset())) == set(
            equiv.canonical(b.argv, frozenset())
        ), f"shell {a.argv} vs port {b.argv}"
        assert _meaningful_env(a.env) == _meaningful_env(b.env)


def test_arm_a_differs_from_its_shell_by_exactly_the_server_log(
    tmp_path, monkeypatch
) -> None:
    """The one deliberate divergence, pinned by value rather than exempted.

    Arm A's shell passes no `--server-log`; the port passes it for both arms,
    because a code path that passes it only sometimes is the shape that let
    arm B run three cycles writing counters nothing read. Asserting the
    difference is EXACTLY this one pair says the divergence is the one we
    chose -- an exemption for the flag would also hide anything else that
    drifted with it.
    """
    invs = _both("A", tmp_path, monkeypatch)
    s = _of(invs, "run.py", "shellA")
    p = _of(invs, "run.py", "portA")
    assert len(s) == len(p) == TRIALS
    for a, b in zip(s, p, strict=True):
        diff = set(equiv.canonical(a.argv, frozenset())) ^ set(
            equiv.canonical(b.argv, frozenset())
        )
        assert len(diff) == 1, f"arm A drifted beyond --server-log: {diff}"
        (flag, value) = next(iter(diff))
        assert flag == "--server-log", f"unexpected difference {flag}={value}"
        assert value.endswith(".log") and "ds4server-trial" in value
        assert _meaningful_env(a.env) == _meaningful_env(b.env)


@pytest.mark.parametrize("arm", ["A", "B"])
def test_the_lock_is_taken_once_for_the_whole_cycle(arm, tmp_path, monkeypatch):
    """#133. The window the lock exists for is the gap BETWEEN trials, where
    the server is deliberately down and a process scan truthfully reports "all
    clear" while the machine is committed for hours. Three claims instead of
    one would leave that gap unprotected three times over.

    Read off the SHELL side only: the port holds it through `preflight`
    in-process, which this differential stubs, so the count here is a check on
    the shell's own `--acquire-lock`, and `run.py` carrying `--no-lock` on both
    sides is what says the port does the same.
    """
    invs = _both(arm, tmp_path, monkeypatch)
    acquires = [
        i
        for i in invs
        if i.program == "preflight.py"
        and i.arm == f"shell{arm}"
        and "--acquire-lock" in i.argv
    ]
    assert len(acquires) == 1, f"shell took the lock {len(acquires)} times"
    for side in (f"shell{arm}", f"port{arm}"):
        for i in _of(invs, "run.py", side):
            assert "--no-lock" in i.argv, f"{side} let run.py claim the lock too"


@pytest.mark.parametrize("arm", ["A", "B"])
def test_every_flag_the_port_passes_run_py_is_one_run_py_declares(arm) -> None:
    """#264, per arm. `--draft-log-engine` is arm B's alone."""
    spec = driver.ARMS[arm]
    argv = driver.run_argv(spec, pathlib.Path("/tmp/x.log"))
    equiv.assert_flags_declared(argv, equiv.declared_run_flags())
    assert ("--draft-log-engine" in argv) is (arm == "B")


def test_each_arm_keeps_its_own_kv_directory() -> None:
    """ds4 rejects the other configuration's checkpoints when a flag changes
    the KV format. A shared directory makes one arm re-prefill where the other
    hit cache, and the only symptom is that it looks slower -- which is the
    quantity being measured."""
    assert driver.ARMS["A"].kv != driver.ARMS["B"].kv
    assert driver.ARMS["A"].kv.name == "server-kv"
    assert driver.ARMS["B"].kv.name == "server-kv-mtp"


def test_the_shells_it_replaces_are_still_here() -> None:
    assert SHELL_A.exists() and SHELL_B.exists()
