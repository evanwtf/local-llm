"""#82 item 4: a memory-killed oracle must exclude the row, not fail it.

Before this fix a `run_capped` kill produced `passed=False` and no exclusion
flag, so it counted as a model failure in every pass rate. That is the "the
code was wrong" vs "the code could not run" ambiguity #82 exists to remove.

The `gemma426` trial that motivated the issue was manually excluded after the
fact; the harness itself must do that.
"""

from __future__ import annotations

import results
import run


class FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_tests_pass_flags_a_killed_oracle(monkeypatch, tmp_path) -> None:
    """The killed bool must reach the caller. A string match would be fragile.

    Simulates a memcap kill by having run_capped return killed=True at 8.1 GiB.
    The former return signature dropped that information at the callee boundary;
    the caller then wrote passed=False and nothing said the kill happened.
    """
    monkeypatch.setattr(
        run.memcap,
        "run_capped",
        lambda cmd, cwd, timeout, cap_gib, env=None: (
            FakeCompleted(-9, "", "Killed"),
            8.1,
            True,
        ),
    )
    ret = run.tests_pass(tmp_path, [], 300)
    assert len(ret) == 3, "tests_pass must return (passed, summary, killed)"
    passed, summary, killed = ret
    assert killed is True
    assert passed is False
    assert "killed" in summary.lower()


def test_tests_pass_flags_an_ordinary_pass(monkeypatch, tmp_path) -> None:
    """The killed bool must be False on a normal pass, not merely absent."""
    monkeypatch.setattr(
        run.memcap,
        "run_capped",
        lambda cmd, cwd, timeout, cap_gib, env=None: (
            FakeCompleted(0, "3 passed in 0.1s", ""),
            0.05,
            False,
        ),
    )
    passed, summary, killed = run.tests_pass(tmp_path, [], 300)
    assert passed is True
    assert killed is False
    assert "passed" in summary.lower()


def test_tests_pass_flags_an_ordinary_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        run.memcap,
        "run_capped",
        lambda cmd, cwd, timeout, cap_gib, env=None: (
            FakeCompleted(1, "1 failed, 2 passed", ""),
            0.05,
            False,
        ),
    )
    passed, _summary, killed = run.tests_pass(tmp_path, [], 300)
    assert passed is False
    assert killed is False


def test_a_memkill_row_is_excluded_via_results_module() -> None:
    """A row stamped by the harness on memkill must survive round-trip."""
    row = {
        "task": "mbox-scan",
        "backend": "gemma426",
        "client": "opencode",
        "trial": 3,
        "passed": False,
        "excluded": True,
        "exclusion_reason": "oracle memory-killed at 8.1 GiB (cap 8.0 GiB)",
        "oracle_killed": True,
    }
    assert results.is_excluded(row) is True


def test_a_normal_failure_row_is_not_excluded() -> None:
    """A model that wrote wrong code must still count as a failure."""
    row = {
        "task": "mbox-scan",
        "backend": "somebackend",
        "trial": 1,
        "passed": False,
    }
    assert results.is_excluded(row) is False


def test_the_exclusion_reason_carries_the_peak_and_cap() -> None:
    """The row has to say enough for someone to know what happened.

    A row that only says "excluded" is one #29 explicitly warned about -- five
    different keys have meant untrustworthy, and a hand-rolled filter that
    checks only one silently counts bad rows as good. The reason must name the
    kill peak and the cap so the row is self-describing without a code lookup.
    """
    row = {
        "task": "mbox-scan",
        "passed": False,
        "excluded": True,
        "exclusion_reason": "oracle memory-killed at 8.1 GiB (cap 8.0 GiB)",
        "oracle_killed": True,
    }
    reason = row["exclusion_reason"]
    assert "GiB" in reason
    assert "cap" in reason
    assert "killed" in reason.lower()
