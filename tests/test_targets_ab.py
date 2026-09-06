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

import json
import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "targets_ab.sh"


def run_script(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
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
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        check=False,
    )


def test_cutoff_reached_voids_batch(tmp_path):
    """A cutoff already past exits non-zero and writes the VOID row."""
    proc = run_script(tmp_path, "4", "0000")
    assert proc.returncode != 0
    manifest = tmp_path / "results-manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["arm"] == "VOID"
    assert rows[0]["reason"] == "past-until"
    assert rows[0]["until"] == "0000"


def test_no_cutoff_runs_all_four(tmp_path):
    """With no cutoff, `targets_ab.sh 4` runs all four arms in order."""
    proc = run_script(tmp_path, "4")
    assert proc.returncode == 0
    manifest = tmp_path / "results-manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert [row["arm"] for row in rows] == ["legacy", "sandbox", "sandbox", "legacy"]


def test_cutoff_after_all_runs_does_not_void(tmp_path):
    """A cutoff in the future leaves the batch intact."""
    proc = run_script(tmp_path, "4", "2359")
    assert proc.returncode == 0
    manifest = tmp_path / "results-manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert len(rows) == 4
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
