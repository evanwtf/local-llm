"""Tests for scripts/macos_settled_rerun.py (#834).

The rerun is only comparable with #499's rows if it runs the same 15 tasks in
the same order, so those are pinned here. The builds are the fresh ones the
operator asked for on 2026-09-29.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import macos_settled_rerun as m


def test_the_plan_runs_499s_stacks_in_499s_order() -> None:
    assert [s.backend for s in m.PLAN] == [
        "qwen38fnq3",
        "qwen38fnmlxserve",
        "qwen38fnds4main",
    ]


def test_the_fresh_builds_are_used() -> None:
    argv = {u.name: " ".join(u.argv) for s in m.PLAN for u in s.units}
    assert "mlx-serve-26.9.6" in argv["834-mlx-serve"]
    assert "ds4-mainline-0aaea5a2" in argv["834-ds4-server"]
    ds4 = next(u for s in m.PLAN for u in s.units if u.name == "834-ds4-server")
    assert ds4.cwd.name == "ds4-mainline-0aaea5a2"


def test_run_argv_carries_every_task_and_no_early_stop() -> None:
    argv = m.run_argv(m.PLAN[0], "b")
    assert len(m.TASKS) == 15
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--task"] == list(m.TASKS)
    assert "--no-early-stop" in argv
    assert argv[argv.index("--trials") + 1] == "3"
    assert argv[argv.index("--backend") + 1] == "qwen38fnq3"


def test_every_backend_exists_in_tasks_toml() -> None:
    import tomllib

    toml = pathlib.Path(__file__).resolve().parents[1] / "benchmarks/agent/tasks.toml"
    backends = tomllib.loads(toml.read_text())["backend"]
    for stack in m.PLAN:
        assert stack.backend in backends
