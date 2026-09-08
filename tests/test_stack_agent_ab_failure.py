"""#227 defect 1 and defect 3: the wrapper must not exit 0 when a sweep refused
(#227).

Defect 1: a logs-only commit moved HARNESS_HEAD mid-run, every remaining sweep
refused in ~1s, and the wrapper still printed "all 8 sweeps complete" and
exited 0. The fix makes sweep() report failure and the main loop accumulate
it, so the exit status and the "complete" message both mean completed, not
attempted.

Defect 3: a non-data path changed mid-run (a .py edit, or a new file git has
never seen) must refuse that sweep loudly, not void the whole run hours later
at read-out. sweep() now guards on worktree_code_dirty, whose leaf is
provenance.code_is_dirty -- the report's own authority -- so this guard and the
read-out void can never disagree.

A guard that greps for the accumulation line would be satisfied by a pattern
written from the broken code, so these tests run the REAL function text -- the
same treatment test_stack_agent_ab_engines.py gives server_argv. All that is
stubbed is sweep()'s leaves (uv, the transcript move, worktree_code_dirty), so
the refusal logic under test is the file's own.
"""

from __future__ import annotations

import importlib
import pathlib
import shlex
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
AB = ROOT / "scripts" / "stack_agent_ab.sh"
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
provenance = importlib.import_module("provenance")


def _run_bash(script: str) -> subprocess.CompletedProcess:
    """Run harness text under the interpreter the file itself uses."""
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )


def _last_line(done: subprocess.CompletedProcess) -> str:
    """sweep()'s return code, as the script reported it.

    Assert on the LAST line, never on the whole of stdout: sweep() echoes its
    own progress ("=== tag ===", "done, N transcripts") and a refusal line
    before the probe prints sweep_rc. An equality check against the whole
    buffer therefore fails on the correct behaviour -- which is what it did
    the first time these tests were ever executed, having been written but
    never run.
    """
    return done.stdout.strip().splitlines()[-1]


# --- sweep() return contract ------------------------------------------------
#
# sweep() must return non-zero when run.py failed OR its transcript dir is
# empty, and zero only when run.py survived AND transcripts exist.


def _run_sweep(
    tmp_path: pathlib.Path, uv_rc: int, transcripts: int, fake_wt_rc: int = 0
) -> subprocess.CompletedProcess:
    """Execute the real sweep() with stubbed uv, transcript move, and dirty check.

    `uv` is a fake that exits with `uv_rc`, so the subshell's status -- and
    therefore run_rc -- is whatever the caller says. `move_transcripts_since`
    is a fake that populates $OUT/<tag>/ when transcripts=1 and leaves it empty
    otherwise, so transcripts is decided independently of run.py's exit code,
    exactly the two inputs sweep() must combine. `worktree_code_dirty` is a
    fake that exits with fake_wt_rc -- 0 clean, 1 dirty, 2+ could-not-check, the
    real function's three-way verdict (the caller of _run_sweep sets the one it
    wants to exercise) -- so the defect-3 guard is driven without any
    dependency on the real repo's state.
    """
    out = tmp_path / "out"
    out.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    bench = tmp_path / "bench"
    bench.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_uv = bindir / "uv"
    fake_uv.write_text(f"#!/bin/sh\nexit {uv_rc}\n")
    fake_uv.chmod(0o755)

    body = AB.read_text()
    start = body.index("sweep() {")
    end = body.index("\n}", start) + 2
    fn = body[start:end]

    script = "\n".join(
        [
            "set -euo pipefail",
            f"OUT={shlex.quote(str(out))}",
            f"BENCH_LOGS={shlex.quote(str(bench))}",
            f"REPO={shlex.quote(str(repo))}",
            "HARNESS_HEAD=abc",
            "export OUT BENCH_LOGS REPO HARNESS_HEAD",
            f"FAKE_UV_RC={uv_rc} FAKE_TRANSCRIPTS={transcripts} FAKE_WT_RC={fake_wt_rc}",
            "export FAKE_UV_RC FAKE_TRANSCRIPTS FAKE_WT_RC",
            f'PATH={shlex.quote(str(bindir))}:"$PATH"',
            "export PATH",
            "move_transcripts_since() {",
            "  local tag=$3",
            '  if [ "$FAKE_TRANSCRIPTS" = "1" ]; then mkdir -p "$OUT/$tag"; : > "$OUT/$tag/fake.txt"; fi',
            "}",
            # worktree_code_dirty() exits with FAKE_WT_RC: 0 clean, 1 dirty, 2+ could-not-check.
            "worktree_code_dirty() {",
            '  return "${FAKE_WT_RC:-0}"',
            "}",
            fn,
            'if sweep new 1 mybackend "" ds4; then rc=0; else rc=$?; fi',
            'printf "sweep_rc=%s\\n" "$rc"',
        ]
    )
    return _run_bash(script)


@pytest.mark.parametrize(
    ("uv_rc", "transcripts", "expected"),
    [
        (0, 1, 0),  # healthy: run.py ok and evidence present
        (1, 1, 1),  # run.py refused even though evidence remains
        (0, 0, 1),  # run.py silent and no evidence: the green-but-empty trap
    ],
)
def test_sweep_return_contract(uv_rc, transcripts, expected, tmp_path) -> None:
    done = _run_sweep(tmp_path, uv_rc, transcripts)
    assert done.returncode == 0, done.stderr
    assert _last_line(done) == f"sweep_rc={expected}", done.stdout


# --- the wrapper accumulates and exits on the count --------------------------
#
# The real main loop, executed with restart_server and sweep stubbed. The stub
# sweep fails exactly the tagged arm, so SWEEPS=4 with FAKE_FAIL_TAG=old makes
# exactly the four old sweeps fail.


def _run_main_loop(
    tmp_path: pathlib.Path, fail_tag: str
) -> subprocess.CompletedProcess:
    out = tmp_path / "out"
    out.mkdir()
    body = AB.read_text()
    start = body.index("failures=0")
    end = body.index('exit "$failures"') + len('exit "$failures"')
    loop = body[start:end]

    script = "\n".join(
        [
            "set -euo pipefail",
            f"OUT={shlex.quote(str(out))}",
            "export OUT",
            "SWEEPS=4",
            "NEW_BACKEND=nb OLD_BACKEND=ob",
            "NEW_RUN_FLAGS= OLD_RUN_FLAGS=",
            "NEW_TREE=/t NEW_GGUF=/g NEW_PLE=/p NEW_KV=/kv NEW_FLAGS= NEW_MLX_MODEL= NEW_MLX_PORT=11234 NEW_MLX_BIN=mlx-serve",
            "OLD_TREE=/t OLD_GGUF=/g OLD_PLE=/p OLD_KV=/kv OLD_FLAGS= OLD_MLX_MODEL= OLD_MLX_PORT=11234 OLD_MLX_BIN=mlx-serve",
            "NEW_ENGINE=ds4 OLD_ENGINE=ds4",
            "export SWEEPS NEW_BACKEND OLD_BACKEND NEW_RUN_FLAGS OLD_RUN_FLAGS NEW_ENGINE OLD_ENGINE",
            "export NEW_TREE NEW_GGUF NEW_PLE NEW_KV NEW_FLAGS NEW_MLX_MODEL NEW_MLX_PORT NEW_MLX_BIN",
            "export OLD_TREE OLD_GGUF OLD_PLE OLD_KV OLD_FLAGS OLD_MLX_MODEL OLD_MLX_PORT OLD_MLX_BIN",
            f"FAKE_FAIL_TAG={shlex.quote(fail_tag)}",
            "export FAKE_FAIL_TAG",
            "restart_server() { :; }",
            "sweep() {",
            "  local arm=$1 n=$2 backend=$3 run_flags=${4:-} engine=${5:-ds4}",
            '  [ "$arm" = "$FAKE_FAIL_TAG" ] && return 1',
            "  return 0",
            "}",
            loop,
        ]
    )
    return _run_bash(script)


def test_all_sweeps_populated_is_a_clean_exit(tmp_path) -> None:
    done = _run_main_loop(tmp_path, fail_tag="")
    assert done.returncode == 0, done.stdout
    assert "all 8 sweeps complete" in done.stdout
    assert "FAILED" not in done.stdout


def test_any_empty_sweep_exits_nonzero_and_never_says_complete(tmp_path) -> None:
    """The #227 negative case: some sweeps refused, and the wrapper must not
    read green. FAKE_FAIL_TAG=old fails exactly the four old sweeps."""
    done = _run_main_loop(tmp_path, fail_tag="old")
    assert done.returncode == 4, done.stdout  # one per refused sweep
    assert "all 8 sweeps complete" not in done.stdout
    assert "4 of 8 sweeps FAILED" in done.stdout


def test_the_failure_count_lands_in_the_run_record(tmp_path) -> None:
    """An overnight session reads the run record, not the wrapper's stdout."""
    done = _run_main_loop(tmp_path, fail_tag="old")
    assert done.returncode == 4, done.stdout
    record = (tmp_path / "out" / "run-record.txt").read_text()
    assert "4 of 8 sweeps FAILED" in record


# --- defect 3: a mid-run change to a non-data path refuses that sweep ---------
#
# sweep() guards on worktree_code_dirty(), stubbed above to exit with the
# function's real three-way verdict: 0 clean, 1 dirty, 2+ could-not-check. The
# leaf of worktree_code_dirty() is provenance.code_is_dirty(), the report's own
# -dirty authority, so the predicate half -- data writes are clean, a foreign
# path is dirty -- is tested against the REAL function below, exactly the two
# inputs the peer's defect-3 spec names.


def test_a_foreign_code_file_makes_the_sweep_refuse(tmp_path) -> None:
    """A worktree carried a change to a non-data path; the sweep must refuse
    before run.py is invoked. fake_wt_rc=1 drives the stubbed guard to report
    dirty; sweep() must return 1, say so, and leave no transcripts."""
    done = _run_sweep(tmp_path, uv_rc=0, transcripts=1, fake_wt_rc=1)
    assert done.returncode == 0, done.stderr
    assert _last_line(done) == "sweep_rc=1", done.stdout
    assert "refusing: harness worktree has uncommitted code" in done.stdout
    assert "done," not in done.stdout  # run.py never ran: no evidence counted


def test_a_clean_worktree_proceeds_and_is_silent(tmp_path) -> None:
    """The path a real A/B runs 99% of the time: worktree_code_dirty returns 0
    and the sweep proceeds. Guard the success path against noise -- an error on
    every sweep would teach the next reader that errors in this log are normal.
    An uninitialized `local wtrc` (set-but-empty under bash 3.2) prints
    "integer expression expected" to stderr on exactly this path."""
    done = _run_sweep(tmp_path, uv_rc=0, transcripts=1, fake_wt_rc=0)
    assert done.returncode == 0, done.stderr
    assert _last_line(done) == "sweep_rc=0", done.stdout
    assert done.stderr == "", done.stderr


def test_a_broken_guard_refuses_with_the_could_not_check_message(tmp_path) -> None:
    """The guard could not run (uv broken, pyproject-vs-lock drift, predicate
    error). fail closed, but say what happened: sweeping on '1' would falsely
    assert uncommitted code exists, so the message names doubt, not a file."""
    done = _run_sweep(tmp_path, uv_rc=0, transcripts=1, fake_wt_rc=2)
    assert done.returncode == 0, done.stderr
    assert _last_line(done) == "sweep_rc=1", done.stdout
    assert "could not confirm a clean harness worktree" in done.stdout
    assert "uncommitted code" not in done.stdout  # cause not established, not asserted
    assert "done," not in done.stdout  # run.py still never ran


def test_the_guard_predicate_skips_data_and_catches_code(tmp_path) -> None:
    """The load-bearing half of 'a .jsonl/.log/.csv write must NOT refuse that
    sweep', against the REAL predicate: an appended data file is clean, a new
    foreign file (never committed) is dirty -- the precise gap the harness-head
    pin leaves because it reads untracked=False."""
    repo = tmp_path / "predicate-repo"
    repo.mkdir()

    def run(*a: str) -> None:
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (repo / "code.py").write_text("x = 1\n")
    (repo / "results.jsonl").write_text("{}\n")
    run("add", "-A")
    run("commit", "-qm", "first")

    (repo / "results.jsonl").write_text('{}\n{"row": 2}\n')  # the run's own data
    assert provenance.code_is_dirty(repo) is False

    (repo / "foreign.yaml").write_text("a: 1\n")  # new, never committed
    assert provenance.code_is_dirty(repo) is True
