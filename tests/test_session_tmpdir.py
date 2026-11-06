"""#1303: each pytest session gets its own temp root.

On 2026-10-28 several agents ran the suite at once on one host. They shared
`$TMPDIR/agent-bench/trial-tmp`, and `run.agent_env` deletes and recreates
the trial's directory there. One suite's `rmtree` raced the other's `mkdir`,
and `test_the_agent_is_handed_a_working_environment` failed with
`FileExistsError`. It passed alone.

The root conftest points `TMPDIR` (and `tempfile`) at a directory inside this
session's pytest base temp. These tests pin that, with paths, not a
relationship between two calls.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import run


def _inside(path: pathlib.Path, parent: pathlib.Path) -> bool:
    return pathlib.Path(os.path.realpath(path)).is_relative_to(os.path.realpath(parent))


def test_tmpdir_is_inside_this_sessions_base_temp(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    base = tmp_path_factory.getbasetemp()
    assert _inside(pathlib.Path(os.environ["TMPDIR"]), base)
    assert _inside(pathlib.Path(tempfile.gettempdir()), base)


def test_the_trial_tmp_root_is_private_to_this_session(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = run.TRIAL_TMP_ROOT
    assert root.parts[-2:] == ("agent-bench", "trial-tmp")
    assert _inside(root, tmp_path_factory.getbasetemp())


def test_a_child_process_inherits_the_session_tmpdir(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """`run.py` builds its workdir from the TMPDIR it inherits."""
    import subprocess

    got = subprocess.run(
        [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert _inside(pathlib.Path(got), tmp_path_factory.getbasetemp())
