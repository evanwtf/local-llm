"""The #136 repeat runner, ported from shell (#235).

One run of a decode A/B is not a measurement: four runs of the #118
comparison returned +16.5%, +21.2%, +17.6% and +17.7% on identical inputs.
This runs the same harness N times into numbered directories, never silently
overwriting a completed one. It does not hold the run lock itself: each
harness invocation takes and releases it in turn (#133).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import decode_ab_repeat as rep
import equiv
import post_ab_run


def fake_harness(tmp_path: pathlib.Path) -> pathlib.Path:
    harness = tmp_path / "harness.sh"
    harness.write_text("#!/bin/sh\nexit 0\n")
    harness.chmod(0o755)
    return harness


def fake_reading(**over):
    base = {"die_max_c": 60.0, "sensors": 52, "utc": "2026-01-01T00:00:00Z"}
    base.update(over)
    return base


# ------------------------------------------------------------ is_complete


def test_is_complete_delegates_to_post_ab_run(tmp_path, monkeypatch) -> None:
    """One owner for 'complete': the runner and the poster must not drift."""
    calls: list[pathlib.Path] = []

    def fake(run):
        calls.append(run)
        return True

    monkeypatch.setattr(post_ab_run, "is_complete", fake)
    d = tmp_path / "r"
    d.mkdir()
    assert rep.is_complete(d) is True
    assert calls == [d]


def test_a_completed_run_is_skipped_not_clobbered(tmp_path, monkeypatch) -> None:
    """The whole point of the script is accumulating runs."""
    harness = fake_harness(tmp_path)
    out = tmp_path / "x-run1"
    out.mkdir()
    (out / "seed.csv").write_text("x")

    monkeypatch.setattr(post_ab_run, "is_complete", lambda run: True)
    monkeypatch.setattr(rep.child, "run", lambda *a, **k: 0)
    rep.repeat(1, tmp_path / "x", harness.name, (), owner_pid=1)
    assert "seed.csv" in {p.name for p in out.iterdir()}


# ------------------------------------------------------ the harness child


def test_the_harness_child_goes_through_child_run(tmp_path, monkeypatch) -> None:
    """#268: a driver stopped mid-repeat must take the harness with it."""
    harness = fake_harness(tmp_path)
    seen: list[tuple[list[str], pathlib.Path]] = []

    def capture(argv, *, cwd, log, **k):
        seen.append((argv, cwd, log))
        return 0

    monkeypatch.setattr(rep.child, "run", capture)
    monkeypatch.setattr(post_ab_run, "is_complete", lambda run: False)
    monkeypatch.setattr(rep.thermals, "reading", lambda: fake_reading())
    rep.repeat(2, tmp_path / "x", str(harness), ("q4",), owner_pid=1)
    assert len(seen) == 2
    (argv1, cwd1, log1), (argv2, _, log2) = seen
    assert argv1 == ["bash", str(harness), "q4", str(tmp_path / "x-run1")]
    assert argv2 == ["bash", str(harness), "q4", str(tmp_path / "x-run2")]
    assert cwd1 == ROOT
    assert log1 == tmp_path / "x-run1" / "harness.log"
    assert log2 == tmp_path / "x-run2" / "harness.log"


def test_a_python_harness_gets_uv_run(tmp_path) -> None:
    harness = tmp_path / "h.py"
    harness.write_text("")
    assert rep.harness_argv(harness.name, (tmp_path / "x",), tmp_path / "o")[:4] == [
        "uv",
        "run",
        "python",
        harness.name,
    ]


# ------------------------------------------------------------ start state


def test_start_state_captures_fans_and_thermals(tmp_path, monkeypatch) -> None:
    """#118's run 2 outlier could only be tested because run 4 captured this."""
    monkeypatch.setattr(
        rep.thermals,
        "reading",
        lambda: fake_reading(fan_rpm_max=3400, fan0_rpm=3400),
    )
    text = rep.start_state_text(1, 4, "h.sh", ("q4", "q8"))
    assert "# run 1 of 4" in text
    assert "# harness: h.sh q4 q8" in text
    assert '"fan0_rpm": 3400' in text
    assert '"die_max_c": 60.0' in text


def test_repeat_refuses_a_bad_harness(tmp_path, monkeypatch) -> None:
    rc = rep.main(["2", str(tmp_path / "x"), str(tmp_path / "missing.sh"), "extra"])
    assert rc != 0


def test_repeat_is_sequential_and_holds_no_lock_of_its_own(tmp_path) -> None:
    """#133: each harness invocation takes and releases the lock, not this."""
    source = (ROOT / "scripts" / "decode_ab_repeat.py").read_text()
    assert "run_lock" not in source and "preflight" not in source


# ------------------------------------------------- the #235 retirement differential
#
# The port's claim is that, under identical inputs, it hands the harness the
# same command line the shell did. A fake `uv` no-ops the completeness check
# and thermals, and a fake harness records the shell's real argv offline.


def test_the_shell_and_the_port_hand_the_harness_the_same_command(tmp_path) -> None:
    harness = tmp_path / "harness.sh"
    # The shell runs `bash "$HARNESS"`, so the fake must be bash, not python.
    # It records argv as JSON via a python heredoc, then exits 0.
    harness.write_text(
        "#!/bin/bash\n"
        'python3 - "$EQUIV_OUT" "$EQUIV_PROGRAM" "$EQUIV_ARM" "$@" <<\'PYEOF\'\n'
        "import json, os, sys\n"
        "out, program, arm = sys.argv[1], sys.argv[2], sys.argv[3]\n"
        "argv = sys.argv[4:]\n"
        'line = {"program": program, "arm": arm, "argv": argv, "env": dict(os.environ)}\n'
        'with open(out, "a") as h:\n'
        '    h.write(json.dumps(line, separators=(",", ":")) + "\\n")\n'
        "PYEOF\n"
    )
    harness.chmod(0o755)
    shim = tmp_path / "shim"
    shim.mkdir()
    probe = tmp_path / "probe.jsonl"
    equiv.write_uv_fake(shim / "uv", probe)

    prefix = tmp_path / "runs" / "ab"
    prefix.parent.mkdir()  # the shell cds into dirname(PREFIX) before mkdir
    args = ["q4", str(tmp_path / "q4.gguf"), "q8", str(tmp_path / "q8.gguf")]
    env = {
        "PATH": f"{shim}:{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "EQUIV_OUT": str(probe),
        "EQUIV_PROGRAM": "harness",
        "EQUIV_ARM": "shell",
    }
    got = subprocess.run(
        [
            "bash",
            str(ROOT / "vault" / "decode_ab_repeat.sh"),
            "2",
            str(prefix),
            str(harness),
            *args,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert got.returncode == 0, (
        f"shell exited {got.returncode}:\n{got.stderr}\n{got.stdout}"
    )

    invs = equiv.by_program(equiv.load(probe), "harness")
    assert len(invs) == 2, f"N=2 should record 2 harness calls, got {len(invs)}"
    for i, inv in enumerate(invs, start=1):
        out = prefix.resolve().parent / f"{prefix.name}-run{i}"
        expected = rep.harness_argv(str(harness), args, out)
        # The recording drops argv[0] (the interpreter), so compare the tail.
        shell_only, py_only = equiv.argv_difference(inv.argv, expected[1:], frozenset())
        assert shell_only == set(), f"{inv.argv} vs {expected}: shell-only {shell_only}"
        assert py_only == set(), f"{inv.argv} vs {expected}: port-only {py_only}"
