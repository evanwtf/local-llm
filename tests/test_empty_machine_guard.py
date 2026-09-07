"""A model test runs at empty, and preflight must refuse when it does not.

Two ways a run measures a machine that was busy doing something else, and the
second is the one that went unnoticed on 2026-09-07:

1. Something foreign is resident. `Report.warnings()` has named these since
   #132, but naming is not refusing -- the line scrolled past and the batch ran.
2. The run's **own plan** needs two models resident at once. A `run.py` naming
   two backends on two engines alternates between them per task, so both stay
   loaded for the whole batch. No server is stale in that case -- both ports
   are expected -- so check (1) sees a clean machine and says so.

The negative cases carry this file. A guard that refuses a legitimate run is
worse than no guard, because the override goes on permanently the first time
it fires wrongly. Three shapes must NOT refuse: an empty machine, two backends
sharing one engine, and a shim-backed backend whose weights live on a
different port than the one the backend names (#211).
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import preflight  # noqa: E402


def proc(pid: int, gib: float, command: str = "/opt/llama-server -m x.gguf", **kw):
    return preflight.Proc(pid=pid, rss_gib=gib, command=command, **kw)


def empty_report(**kw) -> preflight.Report:
    """A machine holding nothing foreign: the case that must pass."""
    return preflight.Report(kw.get("stale", []), kw.get("unmatched", []), 84.0, 28.0)


# --- the machine is empty: no refusal ------------------------------------


def test_empty_machine_one_backend_is_allowed():
    backends = {"qwen38fnq3": {"base_url": "http://127.0.0.1:11500"}}
    assert preflight.refuse_unless_empty(empty_report(), backends) is None


def test_no_backends_at_all_is_allowed():
    """`inspect()` is also used standalone, with no run being planned."""
    assert preflight.refuse_unless_empty(empty_report(), None) is None


def test_empty_backends_dict_is_allowed():
    assert preflight.refuse_unless_empty(empty_report(), {}) is None


# --- something foreign is resident: refuse -------------------------------


def test_a_stale_server_refuses():
    report = empty_report(stale=[proc(4242, 29.3, "/usr/local/bin/ollama serve")])
    msg = preflight.refuse_unless_empty(report, {"a": {"base_url": "u"}})
    assert msg is not None
    assert "REFUSING" in msg
    assert "4242" in msg and "29.3" in msg and "ollama" in msg


def test_an_unmatched_server_refuses():
    """Holding memory and not listening yet is still holding memory."""
    report = empty_report(unmatched=[proc(77, 30.0)])
    msg = preflight.refuse_unless_empty(report, {"a": {"base_url": "u"}})
    assert msg is not None and "REFUSING" in msg and "77" in msg


def test_a_foreign_server_refuses_even_with_one_backend():
    """The two checks are independent; a single-backend run is not exempt."""
    report = empty_report(stale=[proc(9, 40.0)])
    assert preflight.refuse_unless_empty(report, None) is not None


def test_the_refusal_names_every_foreign_process():
    report = empty_report(
        stale=[proc(1, 20.0, "/a/ds4-server --metal")],
        unmatched=[proc(2, 30.0, "/b/llama-server -m y")],
    )
    msg = preflight.refuse_unless_empty(report, None)
    assert "ds4-server" in msg and "llama-server" in msg
    assert "(pid 1)" in msg and "(pid 2)" in msg


# --- the run's own plan needs two engines: refuse ------------------------


def test_two_backends_on_two_engines_refuses():
    backends = {
        "qwen38fnds4kimat": {"base_url": "http://127.0.0.1:8101"},
        "qwen38fnq3": {"base_url": "http://127.0.0.1:11500"},
    }
    msg = preflight.refuse_unless_empty(empty_report(), backends)
    assert msg is not None
    assert "REFUSING" in msg and "2 engines" in msg
    assert "qwen38fnds4kimat" in msg and "qwen38fnq3" in msg


def test_the_two_engine_refusal_names_the_sequential_alternative():
    """An operator who is refused must be told what to run instead."""
    backends = {"a": {"base_url": "http://h:1"}, "b": {"base_url": "http://h:2"}}
    msg = preflight.refuse_unless_empty(empty_report(), backends)
    assert "stack_agent_ab.sh" in msg


def test_three_engines_refuses_and_counts_them():
    backends = {
        "a": {"base_url": "http://h:1"},
        "b": {"base_url": "http://h:2"},
        "c": {"base_url": "http://h:3"},
    }
    msg = preflight.refuse_unless_empty(empty_report(), backends)
    assert "3 engines" in msg


# --- two backends, ONE engine: allowed -----------------------------------


def test_two_backends_sharing_one_engine_is_allowed():
    """Two ollama models on one server interleave fine; only one is resident."""
    backends = {
        "a": {"base_url": "http://127.0.0.1:11434"},
        "b": {"base_url": "http://127.0.0.1:11434"},
    }
    assert preflight.refuse_unless_empty(empty_report(), backends) is None


def test_engine_url_is_what_counts_not_base_url():
    """#211: behind a shim, base_url is the shim and engine_url holds weights.

    Two backends whose shims differ but whose engine is one server are ONE
    resident model. Judging by base_url would refuse a legitimate run.
    """
    backends = {
        "a": {
            "base_url": "http://127.0.0.1:8101",
            "engine_url": "http://127.0.0.1:8000",
        },
        "b": {
            "base_url": "http://127.0.0.1:8102",
            "engine_url": "http://127.0.0.1:8000",
        },
    }
    assert preflight.refuse_unless_empty(empty_report(), backends) is None


def test_shared_base_url_but_different_engines_refuses():
    """The mirror image: one shim fronting two servers is still two models."""
    backends = {
        "a": {
            "base_url": "http://127.0.0.1:8101",
            "engine_url": "http://127.0.0.1:8000",
        },
        "b": {
            "base_url": "http://127.0.0.1:8101",
            "engine_url": "http://127.0.0.1:8020",
        },
    }
    assert preflight.refuse_unless_empty(empty_report(), backends) is not None


def test_a_hosted_backend_with_no_url_does_not_crash():
    """A hosted backend has no base_url at all; it holds no local memory."""
    backends = {"sonnet": {}, "opus": {}}
    # Both key to the same "?" bucket: neither loads a model on this machine.
    assert preflight.refuse_unless_empty(empty_report(), backends) is None


# --- engines_a_run_would_need, directly ----------------------------------


def test_engines_groups_by_host_and_port():
    got = preflight.engines_a_run_would_need(
        {
            "a": {"base_url": "http://127.0.0.1:8000"},
            "b": {"base_url": "http://127.0.0.1:8000"},
            "c": {"base_url": "http://127.0.0.1:8020"},
        }
    )
    assert got == {"127.0.0.1:8000": ["a", "b"], "127.0.0.1:8020": ["c"]}


def test_engines_distinguishes_hosts_on_the_same_port():
    got = preflight.engines_a_run_would_need(
        {
            "a": {"base_url": "http://127.0.0.1:8000"},
            "b": {"base_url": "http://otherbox:8000"},
        }
    )
    assert len(got) == 2


# --- the exit-code contract ----------------------------------------------


def test_main_never_returns_an_exit_code():
    """`run.py` ends with a bare `main()`, so a returned int is discarded.

    A refusal that `return 1`s prints its error and exits 0, and every drive
    script tests `rc -eq 0` -- a refused sweep would be logged as done. Every
    refusal in run.py must raise SystemExit instead. This is an AST check
    rather than a comment because the stashed first draft of this very guard
    returned 1, and reviewing it by eye is what nearly shipped it.
    """
    tree = ast.parse((ROOT / "benchmarks" / "agent" / "run.py").read_text())
    main = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    nested = {
        id(x)
        for fn in ast.walk(main)
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and fn is not main
        for x in ast.walk(fn)
    }
    offenders = [
        node.lineno
        for node in ast.walk(main)
        if isinstance(node, ast.Return)
        and id(node) not in nested
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int)
    ]
    assert not offenders, (
        f"run.py main() returns an exit code at line(s) {offenders}; main() is "
        "called bare, so the value is discarded and the process exits 0. "
        "Raise SystemExit instead."
    )


def test_run_py_wires_the_guard_to_systemexit():
    src = (ROOT / "benchmarks" / "agent" / "run.py").read_text()
    assert "refuse_unless_empty" in src, "the guard is not called by run.py"
    assert "--allow-contended" in src, "the documented override does not exist"
