"""#235 retirement differential for strip_toggle_ab: the absence-defined arm.

The strip is the shim's mode, and the ON arm is defined by the ABSENCE of
`SHIM_NO_STRIP` in the shim's child env. The shell says it with `env -u
SHIM_NO_STRIP`; the port with `tool_shim.serving(strip=True)`, which returns
`unset=("SHIM_NO_STRIP",)`. A dict cannot express absence -- not mentioning a
key means "inherit", so an operator who exported it would hand the off mode to
the on arm. This is the hardest thing the port has to reproduce, and the
strongest evidence in the retirement set.

Both sides run against the same fake `uv` (which records the shim's argv+env
and prints the scaffolding line the driver greps for). The recorded shim env
must agree: ON arm absent, OFF arm set to 1.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for sub in ("scripts", "scripts/lib", "benchmarks/agent"):
    sys.path.insert(0, str(ROOT / sub))

import equiv
import tool_shim
import unitctl

SCRIPT = ROOT / "scripts" / "strip_toggle_ab.sh"
SHIM_VAR = "SHIM_NO_STRIP"


def _fake_uv_dir(tmp_path: pathlib.Path, out: pathlib.Path) -> pathlib.Path:
    """A PATH dir holding the fake `uv` (shim mode on)."""
    shim = tmp_path / "shim"
    shim.mkdir()
    equiv.write_uv_fake_running_real(shim / "uv", out, ROOT, shim=True)
    return shim


def _base_env(out: pathlib.Path, shim_dir: pathlib.Path) -> dict[str, str]:
    return {
        "PATH": f"{shim_dir}:{os.environ.get('PATH', '')}",
        "HOME": str(pathlib.Path(out).parent),
        "EQUIV_OUT": str(out),
        "EQUIV_ARM": "shell",
    }


def _shell_shim_envs(
    tmp_path: pathlib.Path, out: pathlib.Path, shim_dir: pathlib.Path
) -> dict[str, str]:
    """Run the real `.sh`'s `start_shim` for both arms; return env by arm."""
    logdir = tmp_path / "logs"
    logdir.mkdir()
    env = _base_env(out, shim_dir)
    env["LOGDIR"] = str(logdir)
    env["REPO"] = str(ROOT)
    env["SHIM_PORT"] = "8101"
    got: dict[str, str] = {}
    for arm in ("on", "off"):
        bash = (
            f"eval \"$(sed -n '/^start_shim()/,/^}}/p' {SCRIPT})\"\n"
            f"LOGDIR={logdir} REPO={ROOT} SHIM_PORT=8101 start_shim {arm}\n"
        )
        subprocess.run(
            ["bash", "-c", bash],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        invs = equiv.by_program(equiv.load(out), "ds4_qwen_tool_shim.py")
        got[arm] = invs[-1].env
    return got


def _port_shim_envs(
    tmp_path: pathlib.Path,
    out: pathlib.Path,
    shim_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    """Run the port's `tool_shim.serving` for both arms; return env by arm."""
    # `unitctl.STATE_DIR` is read at import, so an env var set here would not
    # reach it; patch the module constant so the unit record stays in tmp.
    monkeypatch.setattr(unitctl, "STATE_DIR", tmp_path / "units")
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("EQUIV_OUT", str(out))
    monkeypatch.setenv("EQUIV_ARM", "port")
    # The fake shim exits immediately, so the readiness wait would raise
    # NeverReady. The differential is about the env, not readiness; skip it.
    monkeypatch.setattr(tool_shim, "_wait", lambda *a, **k: None)
    got: dict[str, str] = {}
    for arm, strip in (("on", True), ("off", False)):
        log = tmp_path / f"shim-{arm}.log"
        before = len(equiv.by_program(equiv.load(out), "ds4_qwen_tool_shim.py"))
        with tool_shim.serving(log, repo=ROOT, port=8101, strip=strip):
            # `serving`'s `finally` stops the unit the moment the block exits,
            # and the fake shim is a child that records then exits. Wait for
            # its record here, inside the block, before the stop can kill it.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                invs = equiv.by_program(equiv.load(out), "ds4_qwen_tool_shim.py")
                if len(invs) > before:
                    break
                time.sleep(0.05)
        invs = equiv.by_program(equiv.load(out), "ds4_qwen_tool_shim.py")
        assert len(invs) > before, f"port {arm} arm never spawned the shim"
        got[arm] = invs[-1].env
    return got


def test_the_on_arm_removes_shim_no_strip_on_both_sides(tmp_path, monkeypatch) -> None:
    """The absence-defined arm, proven at the child on both sides.

    The ON arm's recorded shim env must NOT contain `SHIM_NO_STRIP` -- not
    empty, not "0", absent -- on the shell side (`env -u`) and the port side
    (`unset=`). The OFF arm must set it to 1 on both. This is the channel a
    merged dict cannot express, and the whole point of the port's `unset`.
    """
    out = tmp_path / "rec.jsonl"
    shim_dir = _fake_uv_dir(tmp_path, out)
    shell = _shell_shim_envs(tmp_path, out, shim_dir)
    port = _port_shim_envs(tmp_path, out, shim_dir, monkeypatch)

    for side, envs in (("shell", shell), ("port", port)):
        assert equiv.env_key_state(envs["on"], SHIM_VAR) == "absent", (
            f"{side} ON arm left {SHIM_VAR} {equiv.env_key_state(envs['on'], SHIM_VAR)}"
        )
        assert equiv.env_key_state(envs["off"], SHIM_VAR) == "set:1", (
            f"{side} OFF arm left {SHIM_VAR} "
            f"{equiv.env_key_state(envs['off'], SHIM_VAR)}"
        )
