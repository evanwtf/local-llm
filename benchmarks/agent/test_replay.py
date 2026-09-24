"""Replay tasks (#714): the revert, the sandbox keying, the gate, and the row.

Every case builds a small real git repository in tmp_path, so the revert is
exercised against git's own object store rather than a mock of it.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tomllib

import pytest
import replay
import results
import run

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import sync_sandbox_targets as sync

CHECK = """\
import pathlib, sys
sys.exit(0 if pathlib.Path("src/a.py").read_text() == "x = 2\\n" else 1)
"""


def _commit(repo: pathlib.Path, message: str) -> str:
    run.git(["add", "-A"], repo)
    run.git(
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message], repo
    )
    return run.git(["rev-parse", "HEAD"], repo)


@pytest.fixture
def history(tmp_path):
    """P: a.py is `x = 1`. C: a.py becomes `x = 2`, pkg/new.py appears, and a
    test (check.py, standing in for the oracle) that needs both is added."""
    repo = tmp_path / "gmail-archive"
    (repo / "src").mkdir(parents=True)
    (repo / "src/a.py").write_text("x = 1\n")
    (repo / "keep.txt").write_text("untouched\n")
    run.git(["init", "-q", "-b", "main"], repo)
    parent = _commit(repo, "parent")
    (repo / "src/a.py").write_text("x = 2\n")
    (repo / "src/pkg").mkdir()
    (repo / "src/pkg/new.py").write_text("def f():\n    return 3\n")
    (repo / "tests").mkdir()
    (repo / "tests/check.py").write_text(CHECK)
    commit = _commit(repo, "feature")
    run.git(["update-ref", "refs/remotes/origin/main", "HEAD"], repo)
    return repo, parent, commit


def _export(repo, commit, dest):
    run.build_checkout(repo, commit, dest)
    return dest


# --- the revert ---------------------------------------------------------------


def test_revert_restores_the_parent_and_deletes_what_the_commit_created(
    history, tmp_path
):
    repo, _parent, commit = history
    tree = _export(repo, commit, tmp_path / "tree")
    done = replay.revert(repo, commit, ["src/a.py", "src/pkg/new.py"], tree)
    assert (tree / "src/a.py").read_text() == "x = 1\n"
    assert not (tree / "src/pkg/new.py").exists()
    # git does not track directories, so the parent had no empty package; the
    # export must not either, or the empty directory is a hint.
    assert not (tree / "src/pkg").exists()
    assert (tree / "src").is_dir(), "pruning stops at a directory with files"
    assert (tree / "tests/check.py").read_text() == CHECK, "tests stay at C"
    assert (tree / "keep.txt").read_text() == "untouched\n"
    assert done == [
        {"path": "src/a.py", "action": "restored"},
        {"path": "src/pkg/new.py", "action": "deleted"},
    ]


def test_revert_reads_the_object_store_not_the_working_tree(history, tmp_path):
    """A dirty source checkout must not leak into the export."""
    repo, _parent, commit = history
    (repo / "src/a.py").write_text("dirty\n")
    tree = _export(repo, commit, tmp_path / "tree")
    replay.revert(repo, commit, ["src/a.py"], tree)
    assert (tree / "src/a.py").read_text() == "x = 1\n"


def test_revert_accepts_a_short_sha(history, tmp_path):
    repo, _parent, commit = history
    tree = _export(repo, commit, tmp_path / "tree")
    replay.revert(repo, commit[:7], ["src/a.py"], tree)
    assert (tree / "src/a.py").read_text() == "x = 1\n"


def test_commit_size_counts_the_reverted_paths_only(history):
    repo, _parent, commit = history
    assert replay.commit_size(repo, commit, ["src/a.py", "src/pkg/new.py"]) == {
        "added": 3,
        "removed": 1,
    }


def test_recall_is_verbatim_only_when_every_file_matches_the_commit(history, tmp_path):
    repo, _parent, commit = history
    paths = ["src/a.py", "src/pkg/new.py"]
    tree = _export(repo, commit, tmp_path / "tree")
    replay.revert(repo, commit, paths, tree)

    before = replay.recall(tree, repo, commit, paths)
    assert before["verbatim"] is False
    a, new = before["files"]
    assert a == {
        "path": "src/a.py",
        "verbatim": False,
        "lines_vs_commit": 2,
        "lines_vs_parent": 0,
    }
    assert new["verbatim"] is False and new["lines_vs_commit"] == 2

    (tree / "src/a.py").write_text("x = 2\n")
    (tree / "src/pkg").mkdir()
    (tree / "src/pkg/new.py").write_text("def f():\n    return 3\n")
    after = replay.recall(tree, repo, commit, paths)
    assert after["verbatim"] is True
    assert after["files"][0]["lines_vs_parent"] == 2
    assert after["files"][1]["lines_vs_commit"] == 0


# --- task validation ----------------------------------------------------------

GOOD = {
    "name": "replay-x",
    "kind": "replay",
    "base_commit": "abc1234",
    "revert": ["src/a.py"],
    "tests": ["tests/test_a.py"],
    "prompt": "Do it. Do not modify any test.",
}


def test_a_complete_replay_task_validates():
    assert replay.validate(GOOD) == []


@pytest.mark.parametrize("missing", ["revert", "tests", "prompt", "base_commit"])
def test_a_replay_task_missing_a_field_is_refused(missing):
    task = {k: v for k, v in GOOD.items() if k != missing}
    assert any(f"`{missing}`" in e for e in replay.validate(task))
    assert any(f"`{missing}`" in e for e in replay.validate({**GOOD, missing: []}))


def test_a_replay_task_may_not_revert_a_test():
    """Reverting the oracle would leave nothing to judge by."""
    errors = replay.validate({**GOOD, "revert": ["src/a.py", "tests/test_a.py"]})
    assert any("test file" in e for e in errors)


def test_a_replay_task_may_not_also_excise():
    errors = replay.validate({**GOOD, "file": "src/a.py", "symbol": "f"})
    assert any("excision" in e for e in errors)


def test_a_revert_path_must_stay_inside_the_repo():
    errors = replay.validate({**GOOD, "revert": ["../outside.py"]})
    assert any("repo-relative" in e for e in errors)


def _args(**kw):
    base = {"task": None, "replay": False, "targets": "sandbox"}
    return argparse.Namespace(**{**base, **kw})


CFG = {
    "repo": "~/git/gmail-archive",
    "base_commit": "56e55cc",
    "task": [
        {"name": "excision", "file": "a.py", "symbol": "f", "tests": []},
        GOOD,
    ],
}


def test_replay_tasks_are_out_of_the_default_matrix():
    got = run.select_replay(list(CFG["task"]), CFG, _args())
    assert [t["name"] for t in got] == ["excision"]


def test_the_replay_flag_selects_the_replay_tasks_not_the_matrix():
    got = run.select_replay(list(CFG["task"]), CFG, _args(replay=True))
    assert [t["name"] for t in got] == ["replay-x"]


def test_naming_a_replay_task_runs_it():
    got = run.select_replay([GOOD], CFG, _args(task=["replay-x"]))
    assert got == [GOOD]


def test_a_replay_task_under_the_legacy_layout_is_refused():
    """Legacy resets the operator's one checkout to the pinned commit; it
    cannot be at the matrix's commit and a replay commit at once."""
    with pytest.raises(SystemExit, match="--targets sandbox"):
        run.select_replay([GOOD], CFG, _args(task=["replay-x"], targets="legacy"))


def test_a_malformed_replay_task_stops_the_batch():
    bad = {**GOOD, "revert": []}
    with pytest.raises(SystemExit, match="revert"):
        run.select_replay([bad], CFG, _args(task=["replay-x"]))


# --- sandbox keying: one repo at two commits ---------------------------------


def test_a_replay_task_gets_a_clone_keyed_by_its_commit():
    ordinary = run.task_target(CFG, CFG["task"][0])
    replayed = run.task_target(CFG, GOOD)
    assert ordinary["sandbox"] == "gmail-archive", "existing clones keep their name"
    assert replayed["sandbox"] == "gmail-archive@abc1234"


TASKS = """
repo = "~/git/gmail-archive"
base_commit = "56e55cc"

[[task]]
name = "excision"

[[task]]
name = "replay-one"
kind = "replay"
base_commit = "0ce03e6"

[[task]]
name = "replay-two"
kind = "replay"
base_commit = "ecca282deadbeef"
"""


def test_sync_plans_one_clone_per_repo_and_commit(tmp_path):
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(TASKS)
    assert sync.targets(tasks) == {
        "gmail-archive": ("~/git/gmail-archive", "56e55cc"),
        "gmail-archive@0ce03e6": ("~/git/gmail-archive", "0ce03e6"),
        "gmail-archive@ecca282": ("~/git/gmail-archive", "ecca282deadbeef"),
    }


def _clone(source, name, commit):
    clone = run.SANDBOX_ROOT / name
    clone.parent.mkdir(parents=True, exist_ok=True)
    run.git(["clone", "-q", "--no-hardlinks", str(source), str(clone)], source.parent)
    run.git(["checkout", "-q", "--detach", commit], clone)
    return clone


def test_the_same_repo_at_two_commits_validates_as_two_clones(history):
    repo, parent, commit = history
    _clone(repo, repo.name, parent)
    _clone(repo, f"{repo.name}@{commit[:7]}", commit)
    run.ensure_sandbox_targets(
        [
            (str(repo), parent, repo.name),
            (str(repo), commit, f"{repo.name}@{commit[:7]}"),
        ]
    )
    # Two-element pairs still mean the repository's own clone.
    run.ensure_sandbox_targets([(str(repo), parent)])


def test_a_missing_replay_clone_names_the_sync_script(history):
    repo, parent, commit = history
    _clone(repo, repo.name, parent)
    with pytest.raises(SystemExit, match=r"@.*sync_sandbox_targets"):
        run.ensure_sandbox_targets([(str(repo), commit, f"{repo.name}@{commit[:7]}")])


# --- a whole trial, and its row -----------------------------------------------


def _task(commit):
    return {
        "name": "replay-toy",
        "kind": "replay",
        "base_commit": commit,
        "revert": ["src/a.py", "src/pkg/new.py"],
        "tests": ["tests/check.py"],
        "test_command": "python3",
        "prompt": "Rebuild it. tests/check.py fails. Do not modify any test.",
    }


def _trial(repo, commit, tmp_path, **kw):
    return run.one_trial(
        {"repo": str(repo), "base_commit": commit},
        _task(commit),
        "stub",
        {"model": "stub", "context_tokens": 1},
        trial=1,
        workdir=tmp_path / "work",
        timeout=60,
        prepare_env_first=False,
        gates=False,
        idle_watchdog=False,
        **kw,
    )


def test_a_dry_run_in_the_sandbox_layout_builds_from_the_commit_clone(
    history, tmp_path
):
    repo, _parent, commit = history
    _clone(repo, f"{repo.name}@{commit[:7]}", commit)
    row = _trial(repo, commit, tmp_path, dry_run=True, target_layout="sandbox")
    assert row["control_fails_as_expected"] is True, "the revert must break check"
    assert row["task_kind"] == "replay"
    assert row["replay"]["commit"] == commit
    assert row["replay"]["reverted"][1] == {
        "path": "src/pkg/new.py",
        "action": "deleted",
    }
    assert row["removed_lines"] == 3
    assert "keep_docstring" not in row


def test_a_solved_replay_trial_records_the_verdict_and_recall(
    history, tmp_path, monkeypatch
):
    repo, parent, commit = history

    def argv(task, backend, worktree=None):
        script = (
            "import pathlib;"
            "pathlib.Path('src/a.py').write_text('x = 2\\n');"
            "pathlib.Path('src/pkg').mkdir();"
            "pathlib.Path('src/pkg/new.py').write_text('def f():\\n    return 3\\n')"
        )
        return ["python3", "-c", script]

    monkeypatch.setitem(run.CLIENTS, "scripted", (argv, lambda _out, **_: {}))
    row = _trial(
        repo,
        commit,
        tmp_path,
        dry_run=False,
        client="scripted",
        sandbox=False,
        solutions=tmp_path / "solutions",
    )
    assert results.verdict(row) is True
    assert row["touched_tests"] is False
    assert row["restored_verbatim"] is True
    assert row["replay"]["parent"] == parent
    assert [f["verbatim"] for f in row["replay"]["recall"]] == [True, True]
    assert "src/pkg/new.py" in pathlib.Path(row["solution_patch"]).read_text()
    # `finished` is stamped by write_row, not by the trial.
    assert results.validate({**row, "finished": results.now()}) == []


def test_a_replay_trial_that_edits_the_test_is_flagged(history, tmp_path, monkeypatch):
    repo, _parent, commit = history
    script = "import pathlib;pathlib.Path('tests/check.py').write_text('pass\\n')"
    monkeypatch.setitem(
        run.CLIENTS,
        "cheat",
        (lambda t, b, w=None: ["python3", "-c", script], lambda _o, **_: {}),
    )
    row = _trial(repo, commit, tmp_path, dry_run=False, client="cheat", sandbox=False)
    assert row["touched_tests"] is True
    assert results.verdict(row) is False


def test_the_row_schema_rejects_a_mistyped_replay_field():
    row = {"replay": "0ce03e6", "task_kind": "replay"}
    assert "wrong type for replay: str" in results.validate(row)


def test_the_real_replay_tasks_all_validate():
    cfg = tomllib.loads((run.HERE / "tasks.toml").read_text())
    tasks = [t for t in cfg["task"] if replay.is_replay(t)]
    assert len(tasks) >= 5
    for task in tasks:
        assert replay.validate(task) == [], task["name"]


def test_a_new_file_under_tests_counts_as_touching_them(history, tmp_path, monkeypatch):
    """`git diff HEAD` does not list untracked files; a conftest.py the agent
    adds can rewrite what the oracle runs, so it must count."""
    repo, _parent, commit = history
    script = (
        "import pathlib;"
        "pathlib.Path('src/a.py').write_text('x = 2\\n');"
        "pathlib.Path('tests/conftest.py').write_text('')"
    )
    monkeypatch.setitem(
        run.CLIENTS,
        "planter",
        (lambda t, b, w=None: ["python3", "-c", script], lambda _o, **_: {}),
    )
    row = _trial(repo, commit, tmp_path, dry_run=False, client="planter", sandbox=False)
    assert row["passed"] is True
    assert row["touched_tests"] is True
    assert results.verdict(row) is False
