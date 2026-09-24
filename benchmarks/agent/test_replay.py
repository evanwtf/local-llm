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


# --- spans (#726) ---------------------------------------------------------------


@pytest.fixture
def span(tmp_path):
    """P: a.py `x = 1`. S1: a.py `x = 2`. S2: a.py `x = 3`, b.py created."""
    repo = tmp_path / "span-repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/a.py").write_text("x = 1\n")
    run.git(["init", "-q", "-b", "main"], repo)
    parent = _commit(repo, "parent")
    (repo / "src/a.py").write_text("x = 2\n")
    first = _commit(repo, "span start")
    (repo / "src/a.py").write_text("x = 3\n")
    (repo / "src/b.py").write_text("y = 1\nz = 2\n")
    last = _commit(repo, "span end")
    return repo, parent, first, last


def test_a_span_reverts_to_the_parent_of_its_first_commit(span, tmp_path):
    repo, _parent, first, last = span
    tree = _export(repo, last, tmp_path / "tree")
    done = replay.revert(repo, last, ["src/a.py", "src/b.py"], tree, first)
    assert (tree / "src/a.py").read_text() == "x = 1\n", "not the x = 2 of C^"
    assert not (tree / "src/b.py").exists()
    assert [d["action"] for d in done] == ["restored", "deleted"]


def test_without_a_span_the_revert_is_to_the_commits_own_parent(span, tmp_path):
    repo, _parent, _first, last = span
    tree = _export(repo, last, tmp_path / "tree")
    replay.revert(repo, last, ["src/a.py"], tree)
    assert (tree / "src/a.py").read_text() == "x = 2\n"


def test_a_spans_size_is_the_net_diff_not_the_sum_of_its_commits(span):
    """x = 1 -> 2 -> 3 is one line of work, not two."""
    repo, _parent, first, last = span
    got = replay.commit_size(repo, last, ["src/a.py", "src/b.py"], first)
    assert got == {"added": 3, "removed": 1}


def test_span_commits_counts_both_ends(span):
    repo, _parent, first, last = span
    assert replay.span_commits(repo, first, last) == 2
    assert replay.span_commits(repo, last, last) == 1


def test_a_span_that_runs_backwards_is_refused(span):
    repo, parent, _first, last = span
    with pytest.raises(RuntimeError, match="not an ancestor"):
        replay.span_commits(repo, last, parent)


def test_recall_of_a_span_measures_against_the_span_parent(span, tmp_path):
    repo, _parent, first, last = span
    tree = _export(repo, last, tmp_path / "tree")
    replay.revert(repo, last, ["src/a.py"], tree, first)
    got = replay.recall(tree, repo, last, ["src/a.py"], first)
    assert got["files"][0]["lines_vs_parent"] == 0
    assert got["files"][0]["lines_vs_commit"] == 2


def test_start_of_is_the_span_start_or_the_commit():
    assert replay.start_of({"base_commit": "c"}) == "c"
    assert replay.start_of({"base_commit": "c", "span_start": "s"}) == "s"


# --- held-out tests (#726): cutting, hiding and restoring ------------------------

TESTS = """\
import pytest


def test_kept():
    assert True


# says what the held-out test checks
@pytest.mark.parametrize("n", [1, 2])
def test_cut(n):
    assert n


class TestBox:
    def test_one(self):
        assert True

    def test_two(self):
        assert True
"""


def test_remove_test_cuts_a_function_with_its_decorators_and_comment():
    out = replay.remove_test(TESTS, ["test_cut"])
    assert "test_cut" not in out
    assert "says what" not in out
    assert "parametrize" not in out
    assert "def test_kept" in out and "class TestBox" in out


def test_remove_test_cuts_a_method_and_leaves_its_class():
    out = replay.remove_test(TESTS, ["TestBox", "test_one"])
    assert "test_one" not in out
    assert "def test_two" in out


def test_remove_test_refuses_to_empty_a_class():
    once = replay.remove_test(TESTS, ["TestBox", "test_one"])
    with pytest.raises(ValueError, match="hide the class"):
        replay.remove_test(once, ["TestBox", "test_two"])


def test_remove_test_cuts_a_whole_class():
    assert "TestBox" not in replay.remove_test(TESTS, ["TestBox"])


def test_remove_test_reports_an_absent_name():
    assert replay.remove_test(TESTS, ["test_missing"]) is None
    assert replay.remove_test(TESTS, ["test_kept", "inner"]) is None


def _tree_with_tests(tmp_path):
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests/test_a.py").write_text(TESTS)
    (tree / "tests/test_whole.py").write_text("def test_w():\n    pass\n")
    return tree


def test_hide_deletes_a_file_cuts_a_test_and_records_an_absent_one(tmp_path):
    tree = _tree_with_tests(tmp_path)
    hidden = [
        "tests/test_whole.py",
        "tests/test_a.py::test_cut",
        "tests/test_a.py::TestBox::test_one",
        "tests/test_later.py::test_regression",
    ]
    done = replay.hide(tree, hidden)
    assert [d["action"] for d in done] == ["deleted", "removed", "removed", "absent"]
    assert not (tree / "tests/test_whole.py").exists()
    assert replay.still_visible(tree, hidden) == []


def test_still_visible_names_a_test_that_was_not_cut(tmp_path):
    tree = _tree_with_tests(tmp_path)
    hidden = ["tests/test_a.py::test_cut", "tests/test_whole.py"]
    assert replay.still_visible(tree, hidden) == hidden


def test_hidden_restored_puts_the_agents_files_back(history, tmp_path):
    """The held-out files are in place only for the oracle's run."""
    repo, _parent, commit = history
    tree = _export(repo, commit, tmp_path / "tree")
    (tree / "tests/check.py").write_text("agent's copy\n")
    with replay.hidden_restored(tree, repo, commit, ["tests/check.py"]):
        assert (tree / "tests/check.py").read_text() == CHECK
    assert (tree / "tests/check.py").read_text() == "agent's copy\n"
    (tree / "tests/check.py").unlink()
    with replay.hidden_restored(tree, repo, commit, ["tests/check.py"]):
        assert (tree / "tests/check.py").exists()
    assert not (tree / "tests/check.py").exists(), "absent before, absent after"


def test_hidden_restored_refuses_a_file_the_ref_lacks(history, tmp_path):
    repo, _parent, commit = history
    tree = _export(repo, commit, tmp_path / "tree")
    with (
        pytest.raises(RuntimeError, match="not in"),
        replay.hidden_restored(tree, repo, commit, ["tests/nope.py"]),
    ):
        pass


# --- held-out tests: validation --------------------------------------------------

HIDDEN = {
    **GOOD,
    "tests": ["tests/test_a.py"],
    "hidden_tests": ["tests/test_a.py::TestA::test_b"],
    "prompt": f"Do it. Do not modify any test. {replay.HIDDEN_NOTICE}",
}


def test_a_task_with_hidden_tests_validates():
    assert replay.validate(HIDDEN) == []
    assert replay.validate({**HIDDEN, "span_start": "abc", "suite": "hard"}) == []


@pytest.mark.parametrize(
    "bad",
    [
        "src/test_a.py::test_b",  # not under tests/
        "tests/test_a.py::test_b[1]",  # one parameter case cannot be cut
        "tests/test_a.py::A::b::c",  # deeper than class::method
    ],
)
def test_a_malformed_hidden_id_is_refused(bad):
    errors = replay.validate({**HIDDEN, "hidden_tests": [bad]})
    assert any("is not tests/" in e for e in errors)


def test_a_visible_test_inside_a_hidden_one_is_refused():
    errors = replay.validate(
        {
            **HIDDEN,
            "tests": ["tests/test_a.py::TestA"],
            "hidden_tests": ["tests/test_a.py::TestA"],
        }
    )
    assert any("inside hidden" in e for e in errors)


def test_a_task_with_hidden_tests_must_say_so():
    errors = replay.validate({**HIDDEN, "prompt": "Do it. Do not modify any test."})
    assert any("must say so" in e for e in errors)


def test_hidden_tests_must_be_a_nonempty_unique_list():
    assert any(
        "non-empty" in e for e in replay.validate({**HIDDEN, "hidden_tests": []})
    )
    twice = ["tests/test_a.py::x", "tests/test_a.py::x"]
    assert any("twice" in e for e in replay.validate({**HIDDEN, "hidden_tests": twice}))


def test_a_hidden_ref_without_hidden_tests_is_refused():
    errors = replay.validate({**GOOD, "hidden_ref": "abc"})
    assert any("without `hidden_tests`" in e for e in errors)


def test_a_prompt_that_must_name_no_test_is_held_to_it():
    task = {
        **GOOD,
        "prompt_names_tests": False,
        "prompt": "tests/test_a.py fails. Do not modify any test.",
    }
    assert any("must name no test" in e for e in replay.validate(task))
    task["prompt"] = "Find the failing tests. Do not modify any test."
    assert replay.validate(task) == []


# --- held-out tests: names the agent cannot read -----------------------------------


def test_project_names_are_the_packages_definitions(tmp_path):
    pkg = tmp_path / "src/pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text("LIMIT = 3\nclass A:\n    def meth(self): ...\n")
    assert replay.project_names(tmp_path) >= {"mod", "LIMIT", "A", "meth"}
    assert "__init__" not in replay.project_names(tmp_path)


def test_unseen_api_flags_a_project_name_nothing_visible_mentions():
    text = (
        "from pkg.mod import A\n\n"
        "def test_x():\n    assert A()._private_helper() == 1\n"
    )
    project = {"A", "_private_helper", "mod"}
    got = replay.unseen_api({"tests/t.py::test_x": text}, "use A", project)
    assert got == {"tests/t.py::test_x": ["_private_helper"]}
    # named in the prompt or a visible test: nothing to report
    assert (
        replay.unseen_api({"tests/t.py::test_x": text}, "A _private_helper", project)
        == {}
    )


# --- held-out tests: a whole trial ------------------------------------------------

HIDDEN_TESTS = """\
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import a


def test_x():
    assert a.x == 2


def test_y():
    assert a.y == 3
"""


@pytest.fixture
def hidden_history(tmp_path):
    """P: a.py is `x = 1`. C: `x = 2`, `y = 3`, and a test for each."""
    repo = tmp_path / "gmail-archive"
    (repo / "src").mkdir(parents=True)
    (repo / "src/a.py").write_text("x = 1\n")
    # As in gmail-archive: pytest's bytecode under tests/ is not the agent
    # touching the tests.
    (repo / ".gitignore").write_text("__pycache__/\n")
    run.git(["init", "-q", "-b", "main"], repo)
    _commit(repo, "parent")
    (repo / "src/a.py").write_text("x = 2\ny = 3\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_a.py").write_text(HIDDEN_TESTS)
    return repo, _commit(repo, "feature")


def _hidden_trial(repo, commit, tmp_path, monkeypatch, writes):
    seen = tmp_path / "seen.txt"
    script = (
        "import pathlib;"
        f"pathlib.Path({str(seen)!r}).write_text("
        "pathlib.Path('tests/test_a.py').read_text());"
        f"pathlib.Path('src/a.py').write_text({writes!r})"
    )
    monkeypatch.setitem(
        run.CLIENTS,
        "writer",
        (lambda t, b, w=None: ["python3", "-c", script], lambda _o, **_: {}),
    )
    task = {
        "name": "replay-toy-hidden",
        "kind": "replay",
        "suite": "hard",
        "base_commit": commit,
        "revert": ["src/a.py"],
        "tests": ["tests/test_a.py"],
        "hidden_tests": ["tests/test_a.py::test_y"],
        "test_command": f"{sys.executable} -m pytest -q -p no:cacheprovider",
        "prompt": f"Rebuild it. Do not modify any test. {replay.HIDDEN_NOTICE}",
    }
    row = run.one_trial(
        {"repo": str(repo), "base_commit": commit},
        task,
        "stub",
        {"model": "stub", "context_tokens": 1},
        trial=1,
        workdir=tmp_path / "work",
        timeout=60,
        prepare_env_first=False,
        gates=False,
        idle_watchdog=False,
        dry_run=False,
        client="writer",
        sandbox=False,
        solutions=tmp_path / "solutions",
    )
    return row, seen.read_text()


def test_the_agent_never_sees_a_held_out_test(hidden_history, tmp_path, monkeypatch):
    repo, commit = hidden_history
    row, seen = _hidden_trial(repo, commit, tmp_path, monkeypatch, "x = 2\n")
    assert "def test_x" in seen
    assert "test_y" not in seen
    assert row["hidden"]["hidden"] == [
        {"test": "tests/test_a.py::test_y", "action": "removed"}
    ]


def test_fitting_the_visible_tests_passes_and_fails_the_held_out_one(
    hidden_history, tmp_path, monkeypatch
):
    repo, commit = hidden_history
    row, _ = _hidden_trial(repo, commit, tmp_path, monkeypatch, "x = 2\n")
    assert row["passed"] is True, "passed stays the visible oracle"
    guards = ("touched_tests", "source_repo_intact", "control_fails_as_expected")
    assert results.verdict(row) is True, {k: row.get(k) for k in guards}
    assert row["touched_tests"] is False, "hiding is the start state, not an edit"
    assert row["hidden_passed"] is False
    assert results.hidden_verdict(row) is False
    assert row["hidden"]["counts"]["failed"] == 1
    assert row["hidden"]["ref"] == commit
    assert row["replay"]["suite"] == "hard"
    # the held-out test came back for the oracle and went away again
    assert "test_y" not in pathlib.Path(row["solution_patch"]).read_text()
    assert results.validate({**row, "finished": results.now()}) == []


def test_the_whole_feature_passes_both(hidden_history, tmp_path, monkeypatch):
    repo, commit = hidden_history
    row, _ = _hidden_trial(repo, commit, tmp_path, monkeypatch, "x = 2\ny = 3\n")
    assert row["passed"] is True
    assert row["hidden_passed"] is True
    assert results.hidden_verdict(row) is True
    assert row["hidden"]["counts"]["passed"] == 1


def test_a_row_without_held_out_tests_has_no_hidden_verdict():
    assert results.hidden_verdict({"passed": True}) is None
    timeout = {"hidden": {"tests": ["tests/t.py"]}, "error": "timeout"}
    assert results.hidden_verdict(timeout) is False


def test_the_row_schema_rejects_a_mistyped_hidden_field():
    assert "wrong type for hidden_passed: str" in results.validate(
        {"hidden_passed": "yes"}
    )


# --- selecting the suites ----------------------------------------------------------

HARD = {**HIDDEN, "name": "replay-hard", "suite": "hard"}
SUITES = {**CFG, "task": [*CFG["task"], HARD]}


def test_the_replay_flag_keeps_to_the_714_set():
    got = run.select_replay(list(SUITES["task"]), SUITES, _args(replay=True))
    assert [t["name"] for t in got] == ["replay-x"]


def test_the_replay_hard_flag_selects_only_the_harder_set():
    got = run.select_replay(list(SUITES["task"]), SUITES, _args(replay_hard=True))
    assert [t["name"] for t in got] == ["replay-hard"]


def test_both_flags_select_both_sets():
    got = run.select_replay(
        list(SUITES["task"]), SUITES, _args(replay=True, replay_hard=True)
    )
    assert [t["name"] for t in got] == ["replay-x", "replay-hard"]


def test_a_harder_task_is_out_of_the_default_matrix_too():
    got = run.select_replay(list(SUITES["task"]), SUITES, _args())
    assert [t["name"] for t in got] == ["excision"]


def test_the_real_harder_tasks_are_in_their_own_suite():
    cfg = tomllib.loads((run.HERE / "tasks.toml").read_text())
    for task in cfg["task"]:
        if task.get("hidden_tests") or task.get("span_start"):
            assert replay.suite(task) == "hard", task["name"]
