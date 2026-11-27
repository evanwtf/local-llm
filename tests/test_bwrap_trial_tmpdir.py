"""The trial's own TMPDIR exists inside bwrap. #1541

`agent_env` points TMPDIR at a private directory under `TRIAL_TMP_ROOT` (#780)
and creates it on the host. `bwrap_argv` then mounts a fresh tmpfs over /tmp,
so the directory was missing inside the namespace, and a bare `mktemp` failed
with "No such file or directory". Real trials passed only because OpenCode
creates `$TMPDIR/opencode` itself.
"""

from __future__ import annotations

import itertools
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import run

_HAS_SANDBOX_EXEC = pathlib.Path("/usr/bin/sandbox-exec").exists()

_BWRAP_RUNS = not _HAS_SANDBOX_EXEC and run.BWRAP.exists() and run.bwrap_works()

#: A local backend, so `agent_env` builds the reduced trial env (#780).
_BACKEND = {
    "base_url": "http://127.0.0.1:1",
    "auth_token": "local",
    "model": "m",
    "context_tokens": 1,
}


def _covers(argv):
    """The paths that a deny cover mounts over, in argv order."""
    out = []
    for i, arg in enumerate(argv[:-2]):
        if arg == "--tmpfs" and argv[i + 1] not in ("/tmp", "/dev/shm"):
            out.append(i + 1)
        elif arg == "--ro-bind" and argv[i + 1] == "/dev/null":
            out.append(i + 2)
    return out


def test_the_trial_tmpdir_is_created_inside_the_private_tmp(tmp_path):
    """After `--tmpfs /tmp` and every cover, before the worktree bind."""
    answers = tmp_path / "bench-solutions"
    answers.mkdir()
    ledger = tmp_path / "results.jsonl"
    ledger.write_text("{}\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    private = "/tmp/agent-bench/trial-tmp/wt"
    argv = run.bwrap_argv(
        ["true"],
        wt,
        "/x/repo",
        [str(answers), str(ledger)],
        trial_tmpdir=private,
    )
    pairs = list(itertools.pairwise(argv))
    assert ("--dir", private) in pairs
    at = argv.index(private)
    assert argv.index("/tmp") < at
    assert all(cover < at for cover in _covers(argv))
    assert at < argv.index(str(wt.resolve()))
    # The worktree stays last.
    tail = argv[argv.index("true") - 3 : argv.index("true")]
    assert tail == ["--bind", str(wt.resolve()), str(wt.resolve())]


def test_the_trial_tmpdir_is_created_not_bound(tmp_path):
    """A bind would show the host's copy; --dir only makes an empty directory,
    so it cannot undo a cover or open a path. Nothing one trial leaves reaches
    the next (#780)."""
    private = "/tmp/agent-bench/trial-tmp/wt"
    argv = run.bwrap_argv(
        ["true"], tmp_path / "wt", "/x/repo", [], trial_tmpdir=private
    )
    assert argv.count(private) == 1
    assert argv[argv.index(private) - 1] == "--dir"


def test_no_trial_tmpdir_adds_nothing(tmp_path):
    argv = run.bwrap_argv(["true"], tmp_path / "wt", "/x/repo", [])
    assert "--dir" not in argv
    argv = run.bwrap_argv(["true"], tmp_path / "wt", "/x/repo", [], trial_tmpdir=None)
    assert "--dir" not in argv


@pytest.mark.parametrize("where", ["/var/tmp/agent-bench/trial-tmp/wt", "/tmpx/wt"])
def test_a_trial_tmpdir_outside_the_private_tmp_adds_nothing(tmp_path, where):
    """The host directory already shows through the `/` bind. A --dir there
    would write a missing directory through to the host."""
    argv = run.bwrap_argv(["true"], tmp_path / "wt", "/x/repo", [], trial_tmpdir=where)
    assert "--dir" not in argv
    assert where not in argv


@pytest.mark.skipif(
    _HAS_SANDBOX_EXEC, reason="sandbox-exec present; this targets the Linux bwrap path"
)
def test_sandboxed_creates_the_tmpdir_that_agent_env_names(tmp_path, monkeypatch):
    """One path, from both sides: what `agent_env` sets is what bwrap makes."""
    wt = tmp_path / "wt"
    wt.mkdir()
    fake_bwrap = tmp_path / "bwrap"
    fake_bwrap.write_text("")
    monkeypatch.setattr(run, "BWRAP", fake_bwrap)
    monkeypatch.setattr(run, "bwrap_works", lambda: True)
    wrapped, _denied, mechanism = run.sandboxed(["true"], wt, "/x/repo", tmp_path)
    assert mechanism == "bwrap"
    tmpdir = run.agent_env(_BACKEND, wt)["TMPDIR"]
    if pathlib.Path(tmpdir).is_relative_to("/tmp"):
        assert ("--dir", tmpdir) in list(itertools.pairwise(wrapped))
    else:
        assert "--dir" not in wrapped


@pytest.mark.skipif(not _BWRAP_RUNS, reason="needs a bwrap that can make namespaces")
def test_a_real_trial_can_mktemp_in_its_own_tmpdir(tmp_path):
    """The kernel, not the argv: a bare `mktemp` under the trial env."""
    wt = tmp_path / "wt"
    wt.mkdir()
    script = 'f=$(mktemp) || exit 3; echo "made=$f"; echo "tmpdir=$TMPDIR"'
    argv, _denied, mechanism = run.sandboxed(
        ["sh", "-c", script], wt, "/x/repo", tmp_path
    )
    assert mechanism == "bwrap"
    env = run.agent_env(_BACKEND, wt)
    got = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=30, check=False
    )
    assert got.returncode == 0, got.stderr
    lines = dict(line.split("=", 1) for line in got.stdout.splitlines())
    assert lines["tmpdir"] == env["TMPDIR"]
    assert os.path.dirname(lines["made"]) == env["TMPDIR"]
    if pathlib.Path(env["TMPDIR"]).is_relative_to("/tmp"):
        # Created inside the private /tmp, so the file died with it.
        assert list(pathlib.Path(env["TMPDIR"]).iterdir()) == []
