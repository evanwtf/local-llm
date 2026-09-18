"""What a row says about its own isolation. #477

A boolean cannot carry this. The mechanisms do not correspond across the two
machines -- macOS has `sandbox-exec` and nothing cgroup-like, Linux has
namespaces and cgroups and no `sandbox-exec` -- so the row records which
dimensions were enforced and by what. The failure this prevents is pooling a
confined row with an unconfined one on a task where the sandbox is the thing
that moved (`script-transform`, 12/20, #476).
"""

from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import run


def test_every_declared_dimension_is_recorded():
    """A dimension missing from the record is a guarantee nobody can check."""
    record = run.confinement_record("sandbox-exec", ["/home/x/bench-solutions"], 24)
    for dimension in run.CONFINEMENT_DIMENSIONS:
        assert dimension in record, dimension


def test_a_confined_trial_names_its_mechanism_and_deny_list():
    record = run.confinement_record(
        "sandbox-exec", ["/home/x/bench-solutions", "/home/x/git/gmail-archive"], 24
    )
    assert record["mechanism"] == "sandbox-exec"
    assert record["paths"] == "deny-list"
    assert record["denied_count"] == 2


def test_an_unconfined_trial_says_so_explicitly():
    """Absence of the field means "an older harness", which is a different
    claim from "this run was unconfined". Both must be distinguishable."""
    record = run.confinement_record("none", [], 24)
    assert record["mechanism"] == "none"
    assert record["paths"] == "none"
    assert record["denied_count"] == 0


def test_tmp_and_network_are_recorded_as_unenforced_today():
    """Both are reachable on both machines right now, and the two #389
    failures were a `/tmp` write nothing refused. Recording "unenforced" is
    what stops a later, stricter row being pooled with this one."""
    record = run.confinement_record("sandbox-exec", ["/x"], 24)
    assert record["tmp"] == "unenforced"
    assert record["network"] == "unenforced"


def test_the_memory_dimension_names_its_enforcer_and_cap():
    """`run.py`'s memcap polls and kills on both platforms (#380). A Linux
    cgroup ceiling on top would read differently, and that is a later step."""
    assert run.confinement_record("none", [], 24)["memory"] == "harness:24GiB"
    assert run.confinement_record("none", [], None)["memory"] == "none"
    assert run.confinement_record("none", [], 0)["memory"] == "none"


def test_bwrap_gives_the_trial_a_private_tmp_and_shm():
    """Operator decision 2026-09-17: private /tmp, not read-only. The trial
    cannot see or pollute the host's /tmp, and vLLM's POSIX segments in
    /dev/shm die with the namespace instead of outliving the trial."""
    argv = run.bwrap_argv(["true"], "/tmp/wt", "/home/x/git/gmail-archive", [])
    assert argv[:1] == [str(run.BWRAP)]
    assert "--die-with-parent" in argv
    pairs = list(itertools.pairwise(argv))
    assert ("--tmpfs", "/tmp") in pairs
    assert ("--tmpfs", "/dev/shm") in pairs


def test_a_denied_directory_becomes_empty_and_a_denied_file_becomes_dev_null(tmp_path):
    """The Mac returns EPERM; here the answers are simply not there. Both hide
    them, and neither can be defeated by the `../...` path shapes that made
    OpenCode's own permission layer useless (#54)."""
    answers_dir = tmp_path / "bench-solutions"
    answers_dir.mkdir()
    answers_file = tmp_path / "tasks.toml"
    answers_file.write_text("the prompts and the checks")
    argv = run.bwrap_argv(
        ["true"],
        tmp_path / "wt",
        "/home/x/git/gmail-archive",
        [str(answers_dir), str(answers_file)],
    )
    triples = list(zip(argv, argv[1:], argv[2:], strict=False))
    assert ("--tmpfs", str(answers_dir)) in list(itertools.pairwise(argv))
    assert ("--ro-bind", "/dev/null", str(answers_file)) in triples


def test_the_worktree_bind_comes_last_so_nothing_shadows_it(tmp_path):
    """It is the one path the trial must be able to write."""
    wt = tmp_path / "wt"
    wt.mkdir()
    argv = run.bwrap_argv(
        ["true"], wt, "/home/x/git/gmail-archive", ["/home/x/bench-logs"]
    )
    tail = argv[argv.index("true") - 3 : argv.index("true")]
    assert tail == ["--bind", str(wt.resolve()), str(wt.resolve())]


def test_the_bwrap_row_records_a_private_tmp():
    """The stamp must distinguish this from the Mac's shared /tmp, or rows
    taken under the two policies get pooled (#477)."""
    assert run.confinement_record("bwrap", ["/x"], 24)["tmp"] == "private"
    assert run.confinement_record("sandbox-exec", ["/x"], 24)["tmp"] == "unenforced"
    # Shared on every mechanism today: the server is outside the sandbox.
    assert run.confinement_record("bwrap", ["/x"], 24)["network"] == "unenforced"


def test_the_apparmor_profile_is_in_the_repo():
    """Ubuntu 24.04 refuses unprivileged user namespaces without it, so the
    sandbox is inert on a fresh box until this file is installed."""
    body = (
        pathlib.Path(__file__).resolve().parents[1] / "apparmor" / "bwrap"
    ).read_text()
    assert "profile bwrap /usr/bin/bwrap" in body
    assert "userns," in body
