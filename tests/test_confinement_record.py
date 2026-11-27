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
import subprocess
import sys

import pytest

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
    assert record["paths"] == "home-allow-list"
    assert record["denied_count"] == 2


def test_bwrap_still_records_a_deny_list():
    """#780 changed only the macOS profile. A Linux row must not claim the
    home allow-list, or it would pool with rows taken under a stricter policy."""
    record = run.confinement_record("bwrap", ["/home/x/bench-solutions"], 24)
    assert record["paths"] == "deny-list"


def test_an_unconfined_trial_says_so_explicitly():
    """Absence of the field means "an older harness", which is a different
    claim from "this run was unconfined". Both must be distinguishable."""
    record = run.confinement_record("none", [], 24)
    assert record["mechanism"] == "none"
    assert record["paths"] == "none"
    assert record["denied_count"] == 0


def test_network_is_recorded_as_unenforced_today():
    """The network is reachable on both machines right now. Recording
    "unenforced" is what stops a later, stricter row being pooled with this
    one. `tmp` was unenforced on macOS too until #780 made it private."""
    record = run.confinement_record("sandbox-exec", ["/x"], 24)
    assert record["tmp"] == "private"
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
    assert run.confinement_record("sandbox-exec", ["/x"], 24)["tmp"] == "private"
    assert run.confinement_record("none", [], 24)["tmp"] == "unenforced"
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


# --- The harness checkout is read-only inside a trial, its .git hidden --------
#
# bwrap bound `/` read-write. On a bare-metal host a trial could write the
# live ledger and the harness's code; in the two-node client container only
# file ownership stopped the write. In both, `git show HEAD:<path>` read
# `tasks.toml` past the /dev/null cover on the working file (#477).


def _harness(tmp_path, git="dir"):
    root = tmp_path / "harness"
    (root / "benchmarks" / "agent").mkdir(parents=True)
    if git == "dir":
        (root / ".git").mkdir()
    elif git == "file":
        main = tmp_path / "main-checkout"
        common = main / ".git"
        (common / "worktrees" / "harness").mkdir(parents=True)
        (main / "benchmarks" / "agent").mkdir(parents=True)
        (root / ".git").write_text(f"gitdir: {common / 'worktrees' / 'harness'}\n")
    return root


def _answers(tree):
    """A checkout's answer files, as `harness_answer_paths` should find them."""
    tasks = tree / "benchmarks" / "agent" / "tasks.toml"
    tasks.parent.mkdir(parents=True, exist_ok=True)
    tasks.write_text("'Benchmarking' -> 'gnikramhcneB'\n")
    out = [tasks]
    for machine, name in (("a", "results.jsonl"), ("b", "screening-aborts.jsonl")):
        f = tree / "hardware" / machine / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}\n")
        out.append(f)
    sandbox = tree / "sandbox"
    sandbox.mkdir(exist_ok=True)
    return [*out, sandbox]


def test_the_harness_checkout_is_bound_read_only_with_its_git_hidden(tmp_path):
    root = _harness(tmp_path)
    argv = run.bwrap_argv(["true"], tmp_path / "wt", "/x/repo", [], harness_root=root)
    triples = list(zip(argv, argv[1:], argv[2:], strict=False))
    assert ("--ro-bind", str(root), str(root)) in triples
    assert ("--tmpfs", str(root / ".git")) in list(itertools.pairwise(argv))
    assert argv.index("--ro-bind") < argv.index(str(root / ".git"))


def test_the_read_only_bind_comes_before_the_deny_covers(tmp_path):
    """A bind of the root re-exposes whatever was mounted below it, so the
    answer covers inside the checkout must come after it, not before."""
    root = _harness(tmp_path)
    ledger = root / "results.jsonl"
    ledger.write_text("{}\n")
    sandbox = root / "sandbox"
    sandbox.mkdir()
    argv = run.bwrap_argv(
        ["true"],
        tmp_path / "wt",
        "/x/repo",
        [str(sandbox), str(ledger)],
        harness_root=root,
    )
    ro = argv.index(str(root))
    assert ro < argv.index(str(sandbox))
    assert ro < argv.index(str(ledger))


def test_a_worktree_checkout_hides_its_git_file_and_the_main_git_dir(tmp_path):
    """In a `git worktree`, .git is a file that points at the main checkout's
    object store. Hide both, or `git show` still reads tasks.toml."""
    root = _harness(tmp_path, git="file")
    argv = run.bwrap_argv(["true"], tmp_path / "wt", "/x/repo", [], harness_root=root)
    triples = list(zip(argv, argv[1:], argv[2:], strict=False))
    assert ("--ro-bind", "/dev/null", str(root / ".git")) in triples
    common = (tmp_path / "main-checkout" / ".git").resolve()
    assert ("--tmpfs", str(common)) in list(itertools.pairwise(argv))


def test_a_worktree_checkout_binds_the_main_working_tree_read_only_too(tmp_path):
    """L1: the main working tree beside a worktree holds the same answers.
    It is bound read-only first, since the worktree may lie inside it."""
    root = _harness(tmp_path, git="file")
    main = (tmp_path / "main-checkout").resolve()
    assert run.harness_trees(root) == [main, root.resolve()]
    argv = run.bwrap_argv(["true"], tmp_path / "wt", "/x/repo", [], harness_root=root)
    triples = list(zip(argv, argv[1:], argv[2:], strict=False))
    assert ("--ro-bind", str(main), str(main)) in triples
    assert argv.index(str(main)) < argv.index(str(root.resolve()))
    # Both binds come before the .git covers.
    assert argv.index(str(root.resolve())) < argv.index(str(main / ".git"))


def test_a_main_checkout_is_its_own_only_tree(tmp_path):
    root = _harness(tmp_path)
    assert run.harness_trees(root) == [root.resolve()]


def test_the_worktree_stays_writable_when_it_lies_inside_the_harness(tmp_path):
    root = _harness(tmp_path)
    wt = root / "sandbox" / "wt"
    wt.mkdir(parents=True)
    argv = run.bwrap_argv(["true"], wt, "/x/repo", [], harness_root=root)
    tail = argv[argv.index("true") - 3 : argv.index("true")]
    assert tail == ["--bind", str(wt.resolve()), str(wt.resolve())]


def test_the_default_harness_root_is_this_checkout():
    """A caller that forgets the argument still gets the read-only bind."""
    assert run.HARNESS_ROOT == pathlib.Path(run.__file__).resolve().parents[2]
    argv = run.bwrap_argv(["true"], "/tmp/wt", "/x/repo", [])
    root = str(run.HARNESS_ROOT)
    assert argv[argv.index(root) - 1] == "--ro-bind"


_BWRAP_RUNS = (
    not pathlib.Path("/usr/bin/sandbox-exec").exists()
    and run.BWRAP.exists()
    and run.bwrap_works()
)


@pytest.mark.skipif(not _BWRAP_RUNS, reason="needs a bwrap that can make namespaces")
def test_a_real_trial_cannot_write_the_harness_or_read_its_git(tmp_path):
    """The kernel, not the argv: run bwrap and try both escapes."""
    root = _harness(tmp_path)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    # pytest's tmp_path is under /tmp, which bwrap replaces with a tmpfs. The
    # marker proves the checkout is visible inside, so a refused write is the
    # read-only bind at work and not a directory that is simply missing.
    (root / "marker").write_text("harness-visible\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    script = (
        f"cat {root}/marker; "
        f"printf x > {root}/PROBE; w=$?; "
        f"ls -A {root}/.git; "
        f"printf y > {wt}/ok; "
        f'echo "write=$w"'
    )
    argv = run.bwrap_argv(["sh", "-c", script], wt, "/x/repo", [], harness_root=root)
    got = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    assert got.returncode == 0, got.stderr
    assert "harness-visible" in got.stdout
    assert "Read-only file system" in got.stderr
    assert not (root / "PROBE").exists()
    assert "write=0" not in got.stdout
    assert "HEAD" not in got.stdout
    assert (wt / "ok").read_text() == "y"


# --- The live ledger is the one denied (#477) ----------------------------------


def test_the_profile_denies_the_ledger_it_is_given(tmp_path):
    """A remote run writes the server's ledger, not the client's. The deny
    list must name the file this run writes, not `results.default_path()`."""
    root = _harness(tmp_path)
    ledger = tmp_path / "elsewhere" / "results.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{}\n")
    _, denied = run.sandbox_profile(
        tmp_path / "wt", "/x/repo", ledger=ledger, harness_root=root
    )
    assert str(ledger.resolve()) in denied
    assert str(run.RESULTS.resolve()) not in denied


def test_the_profile_denies_every_ledger_in_the_checkout(tmp_path):
    """M2: every machine's ledger quotes failing checks, and every one is in
    the readable checkout -- not only the ledger this run writes."""
    root = _harness(tmp_path)
    answers = _answers(root)
    _, denied = run.sandbox_profile(tmp_path / "wt", "/x/repo", harness_root=root)
    for path in answers:
        assert str(path.resolve()) in denied, path


def test_a_worktree_layout_denies_the_main_trees_answers_too(tmp_path):
    """L1: the main working tree's tasks.toml, ledgers and sandbox clones."""
    root = _harness(tmp_path, git="file")
    main_answers = _answers(tmp_path / "main-checkout")
    _, denied = run.sandbox_profile(tmp_path / "wt", "/x/repo", harness_root=root)
    for path in main_answers:
        assert str(path.resolve()) in denied, path


def test_sandboxed_and_client_launch_pass_the_ledger_through(tmp_path, monkeypatch):
    ledger = tmp_path / "results.jsonl"
    ledger.write_text("{}\n")
    seen = []
    real = run.sandbox_profile

    def spy(worktree, repo, *a, **kw):
        seen.append(kw.get("ledger"))
        return real(worktree, repo, *a, **kw)

    # Hermetic (M1): without a working bwrap, sandboxed() returns before it
    # builds a deny list, and the spy would see nothing.
    monkeypatch.setattr(run, "BWRAP", ledger)
    monkeypatch.setattr(run, "bwrap_works", lambda: True)
    monkeypatch.setattr(run, "sandbox_profile", spy)
    run.client_launch_argv(
        lambda task, backend, wt: ["true"],
        {},
        {},
        tmp_path / "wt",
        "/x/repo",
        ledger=ledger,
    )
    assert seen and seen[-1] == ledger


# --- New facts go under new keys; pooled keys keep their values ---------------


def test_the_row_records_the_harness_posture_under_its_own_key(monkeypatch):
    def checkout(mechanism):
        return run.confinement_record(mechanism, ["/x"], None)["harness_checkout"]

    assert checkout("bwrap") == "ro-nogit"
    assert checkout("none") == "unenforced"
    # sandbox-exec refuses writes under $HOME only (#780), so `ro` holds only
    # while the checkout is under $HOME.
    monkeypatch.setattr(run, "HARNESS_ROOT", pathlib.Path.home() / "git" / "x")
    assert checkout("sandbox-exec") == "ro"
    monkeypatch.setattr(run, "HARNESS_ROOT", pathlib.Path("/opt/elsewhere/x"))
    assert checkout("sandbox-exec") == "unenforced"
    # `writes` keeps its meaning: rows stay comparable with the leaders.
    assert run.confinement_record("bwrap", ["/x"], None)["writes"] == "unenforced"


def test_the_container_cgroup_limit_is_recorded_beside_memory():
    """The kernel's ceiling on the client container (#680). `memory` keeps
    describing the harness's own cap, so its value does not change."""
    record = run.confinement_record("bwrap", ["/x"], None, cgroup_gib=12.0)
    assert record["memory_cgroup_gib"] == 12.0
    assert record["memory"] == "none"
    assert run.confinement_record("bwrap", ["/x"], None)["memory_cgroup_gib"] is None


@pytest.mark.skipif(not _BWRAP_RUNS, reason="needs a bwrap that can make namespaces")
def test_a_real_worktree_trial_cannot_write_the_main_tree_or_read_its_git(tmp_path):
    """L7: the worktree layout, under the kernel."""
    root = _harness(tmp_path, git="file")
    main = tmp_path / "main-checkout"
    (main / "marker").write_text("main-visible\n")
    (main / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    script = (
        f"cat {main}/marker; "
        f"printf x > {main}/PROBE; "
        f"printf x > {root}/PROBE; "
        f"ls -A {main}/.git; "
        "echo end"
    )
    argv = run.bwrap_argv(["sh", "-c", script], wt, "/x/repo", [], harness_root=root)
    got = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    assert got.returncode == 0, got.stderr
    assert "main-visible" in got.stdout
    assert got.stderr.count("Read-only file system") == 2, got.stderr
    assert not (main / "PROBE").exists()
    assert not (root / "PROBE").exists()
    assert "HEAD" not in got.stdout


@pytest.mark.skipif(not _BWRAP_RUNS, reason="needs a bwrap that can make namespaces")
def test_a_real_deny_cover_inside_the_checkout_survives_the_read_only_bind(tmp_path):
    """L7: the covers come after the bind, so the bind cannot undo them."""
    root = _harness(tmp_path)
    tasks, _ledger, _aborts, sandbox = _answers(root)
    (sandbox / "clone-with-history").write_text("answer\n")
    (root / "marker").write_text("harness-visible\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    script = f"cat {root}/marker; cat {tasks}; ls -A {sandbox}; echo end"
    argv = run.bwrap_argv(
        ["sh", "-c", script],
        wt,
        "/x/repo",
        [str(sandbox), str(tasks)],
        harness_root=root,
    )
    got = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    assert "harness-visible" in got.stdout
    assert "gnikramhcneB" not in got.stdout
    # bwrap's --ro-bind mounts nodev, so the /dev/null cover reads as
    # "Permission denied", not as an empty file.
    assert "Permission denied" in got.stderr
    assert "clone-with-history" not in got.stdout
    assert "end" in got.stdout
