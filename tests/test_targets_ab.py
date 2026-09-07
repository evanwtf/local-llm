"""#146: the targets_ab.sh batch loop, in dry-run mode.

The VOID-on-cutoff change is the whole point of the PR: a cutoff that would
skip a run must VOID the batch (exit non-zero, VOID row in the manifest)
rather than truncate it, because a partial batch is no result. The two
behaviors are the loop's contract, so they get tests.

The loop is exercised with TARGETS_AB_DRY_RUN=1, which skips the machine
(dirty check, sync, lock, shim, ds4-server, run.py) and writes a dry row per
run. The measurement itself is not exercised; the loop is.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "targets_ab.sh"


def epoch_of(hhmm: str) -> int:
    """Epoch of today at HH:MM, for injecting LOCAL_LLM_FAKE_NOW. The script
    resolves the cutoff against the same system clock, so the two agree."""
    h, m = map(int, hhmm.split(":"))
    return int(
        datetime.datetime.now()
        .replace(hour=h, minute=m, second=0, microsecond=0)
        .timestamp()
    )


def run_script(
    tmp_path: pathlib.Path, *args: str, now: int | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "TARGETS_AB_DRY_RUN": "1",
            "RESULTS": str(tmp_path / "results.jsonl"),
            "MANIFEST": str(tmp_path / "results-manifest.jsonl"),
            "BENCH_LOGS": str(tmp_path / "bench-logs"),
            "LOGDIR": str(tmp_path / "logs"),
        }
    )
    if now is not None:
        env["LOCAL_LLM_FAKE_NOW"] = str(now)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        check=False,
    )


def test_cutoff_reached_voids_batch(tmp_path):
    """A cutoff the clock has reached exits non-zero and writes the VOID row."""
    proc = run_script(tmp_path, "4", "12:00", now=epoch_of("12:00"))
    assert proc.returncode != 0
    manifest = tmp_path / "results-manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["arm"] == "VOID"
    assert rows[0]["reason"] == "past-until"
    assert rows[0]["until"] == "12:00"


def test_no_cutoff_runs_all_four(tmp_path):
    """With no cutoff, `targets_ab.sh 4` runs all four arms in order."""
    proc = run_script(tmp_path, "4")
    assert proc.returncode == 0
    manifest = tmp_path / "results-manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert [row["arm"] for row in rows] == ["legacy", "sandbox", "sandbox", "legacy"]


def test_cutoff_after_all_runs_does_not_void(tmp_path):
    """A cutoff in the future leaves the batch intact."""
    proc = run_script(tmp_path, "4", "23:59", now=epoch_of("12:00"))
    assert proc.returncode == 0
    manifest = tmp_path / "results-manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert len(rows) == 4
    assert all(row["arm"] != "VOID" for row in rows)


@pytest.mark.parametrize(
    "now_hhmm, cutoff, expect_void",
    [
        ("12:00", "23:59", False),  # before the cutoff today
        ("23:59", "23:59", True),  # at the cutoff
        ("00:30", "23:59", False),  # after midnight, cutoff is today 23:59
        ("23:35", "23:59", False),  # #175: hour 23, but before 23:59
        ("23:59", "00:00", False),  # past midnight cutoff means tomorrow
    ],
)
def test_cutoff_compares_epochs_not_strings(tmp_path, now_hhmm, cutoff, expect_void):
    """The cutoff must compare epochs, not strings. A string compare of
    "23:35" against "2359" is true because ':' beats '5', so the cutoff fires
    whenever the hour is 23 (#175). The clock is injected, so the outcome does
    not depend on when the test runs."""
    proc = run_script(tmp_path, "4", cutoff, now=epoch_of(now_hhmm))
    manifest = tmp_path / "results-manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    if expect_void:
        assert proc.returncode != 0
        assert rows[0]["arm"] == "VOID"
    else:
        assert proc.returncode == 0
        assert all(row["arm"] != "VOID" for row in rows)


def test_dry_run_never_touches_the_machine(tmp_path):
    """Dry-run must not acquire the lock or start the shim."""
    proc = run_script(tmp_path, "1")
    assert proc.returncode == 0
    assert "acquire-lock" not in proc.stderr
    assert "shim" not in proc.stderr


def test_dry_run_refuses_real_results_paths():
    """Dry-run writes rows to the manifest, so it must refuse to start unless
    RESULTS and MANIFEST are overridden away from the real paths. The obvious
    invocation must be impossible, not merely undocumented."""
    env = os.environ.copy()
    env["TARGETS_AB_DRY_RUN"] = "1"
    proc = subprocess.run(
        ["bash", str(SCRIPT), "1"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        check=False,
    )
    assert proc.returncode != 0
    assert "must override RESULTS and MANIFEST" in proc.stderr
