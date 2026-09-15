"""#208: an A/B must not run into a directory that already holds reps."""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib"))

import outdir_guard as guard


def test_a_missing_directory_passes(tmp_path):
    guard.refuse_reused_outdir(tmp_path / "new")


def test_a_directory_without_csvs_passes(tmp_path):
    """decode_ab_repeat.py writes start-state.txt before it calls a driver."""
    out = tmp_path / "run1"
    out.mkdir()
    (out / "start-state.txt").write_text("# started\n")
    guard.refuse_reused_outdir(out)


def test_a_directory_with_reps_is_refused_and_names_the_newest(tmp_path):
    out = tmp_path / "run2"
    out.mkdir()
    old = out / "on-rep2.csv"
    new = out / "off-rep3.csv"
    old.write_text("x\n")
    new.write_text("x\n")
    os.utime(old, (1_000_000_000, 1_000_000_000))
    os.utime(new, (1_000_000_600, 1_000_000_600))
    with pytest.raises(guard.ReusedOutdir) as caught:
        guard.refuse_reused_outdir(out)
    text = str(caught.value)
    assert "2 CSV file(s)" in text
    assert "off-rep3.csv" in text
    assert guard.REUSE_FLAG in text
    assert "VOID" not in text


def test_a_void_marker_beside_the_directory_is_named(tmp_path):
    out = tmp_path / "knob-gathered-heads-run2"
    out.mkdir()
    (out / "on-rep1.csv").write_text("x\n")
    (tmp_path / "knob-gathered-heads-run2-VOID.md").write_text("void\n")
    with pytest.raises(guard.ReusedOutdir, match="knob-gathered-heads-run2-VOID.md"):
        guard.refuse_reused_outdir(out)


def test_reuse_lets_a_resumed_run_through(tmp_path):
    out = tmp_path / "run3"
    out.mkdir()
    (out / "on-rep1.csv").write_text("x\n")
    guard.refuse_reused_outdir(out, reuse=True)
