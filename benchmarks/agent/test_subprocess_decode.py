"""Agent output must never crash a batch with a decode error (#1189).

On 2026-10-20 an agent's edit held the byte 0xfa. A `text=True` capture
decoded it strictly, raised UnicodeDecodeError out of the trial loop, and
ended a 42-trial batch at trial 24 (#1091). The error happens inside
`communicate()`, before any `except` near the call can help.

These tests feed the byte 0xfa through each path that captures
agent-influenced output and read back U+FFFD. The contract guard that refuses
a strict decode is in test_subprocess_decode_contract.py (`pytest -m fast`).
"""

from __future__ import annotations

import sys

import memcap
import run
import timeout_policy

#: A child that writes a byte that is not UTF-8 to stdout and stderr.
BAD_BYTES = (
    "import sys; sys.stdout.buffer.write(b'ok \\xfa end\\n');"
    " sys.stderr.buffer.write(b'err \\xfa\\n')"
)


def test_run_helper_survives_bad_bytes(tmp_path):
    # run() captures the agent client when the watchdog is off, and the
    # contents of files the agent wrote (`git show`).
    r = run.run([sys.executable, "-c", BAD_BYTES], cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout == "ok \ufffd end\n"
    assert "\ufffd" in r.stderr


def test_watchdog_client_survives_bad_bytes():
    wd = timeout_policy.IdleStallWatchdog(
        lambda: 100.0, idle_floor_watts=20.0, idle_stall_secs=600.0
    )
    got = timeout_policy.run_client_with_watchdog(
        [sys.executable, "-c", BAD_BYTES],
        cwd=None,
        env=None,
        timeout=30.0,
        watchdog=wd,
        poll_secs=5.0,
    )
    assert got.returncode == 0
    assert got.stdout == "ok \ufffd end\n"


def test_oracle_survives_bad_bytes(tmp_path):
    # The test-runner path: the oracle's output is the agent's code's output.
    script = tmp_path / "fake_pytest.py"
    script.write_text(BAD_BYTES + "\n")
    passed, summary, killed = run.tests_pass(
        tmp_path, [], timeout=30, command=f"{sys.executable} {script}"
    )
    assert passed is True
    assert killed is False
    assert isinstance(summary, str)


def test_memcap_run_capped_survives_bad_bytes(tmp_path):
    r, _peak, killed = memcap.run_capped(
        [sys.executable, "-c", BAD_BYTES], cwd=tmp_path, timeout=30, cap_gib=None
    )
    assert killed is False
    assert r.stdout == "ok \ufffd end\n"


def test_cli_check_survives_bad_bytes(tmp_path):
    # Script tasks run the agent's entrypoint and compare its stdout.
    entry = tmp_path / "tool.py"
    entry.write_text(BAD_BYTES + "\n")
    ok, detail = run.script_checks(tmp_path, "tool.py", [("x", "ok")], 30)
    assert ok is False
    assert "\ufffd" in detail
