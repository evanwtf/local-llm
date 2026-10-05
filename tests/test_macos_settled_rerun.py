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


def test_the_ds4_stack_names_its_tree_for_the_route_gate() -> None:
    """run.py's #149 gate reads DS4_TREE and DS4_TEST_MODEL, not the backend's
    engine_tree. Without them it checked ~/git/ds4-metal and refused the
    stack as "stale" on 2026-09-29."""
    ds4 = next(s for s in m.PLAN if s.backend == "qwen38fnds4main")
    server = ds4.units[0]
    assert ds4.env["DS4_TREE"] == str(server.cwd)
    assert ds4.env["DS4_TEST_MODEL"] == server.argv[server.argv.index("-m") + 1]


def test_llama_server_waits_for_health_not_the_port() -> None:
    """llama-server listens while it loads and answers 503; on 2026-09-29 the
    smoke gate hit that 503 and the stack never ran."""
    llama = next(u for s in m.PLAN for u in s.units if u.name == "834-llama-server")
    assert llama.ready_path == "/health"


def test_ready_needs_a_200(monkeypatch) -> None:
    import email.message
    import urllib.error

    def refuse(url, timeout):
        raise urllib.error.HTTPError(
            url, 503, "Loading model", email.message.Message(), None
        )

    monkeypatch.setattr(m.urllib.request, "urlopen", refuse)
    assert m.answering(8020, "/health") is False
