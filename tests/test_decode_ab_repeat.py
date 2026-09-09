"""The #136 repeat runner, ported from shell (#235).

One run of a decode A/B is not a measurement: four runs of the #118
comparison returned +16.5%, +21.2%, +17.6% and +17.7% on identical inputs.
This runs the same harness N times into numbered directories, never silently
overwriting a completed one. It does not hold the run lock itself: each
harness invocation takes and releases it in turn (#133).
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import decode_ab_repeat as rep
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
