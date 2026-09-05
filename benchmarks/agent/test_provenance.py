"""Tests for the stamp that makes every other output attributable.

If this module is wrong, every log line and every test report lies about which
code produced it -- which is worse than having no stamp, because a wrong
attribution is believed.
"""

from __future__ import annotations

import functools
import logging
import pathlib
import subprocess

import provenance
import pytest
import results
from conftest import HAS_LOCAL_RESULTS, SKIP_NO_RESULTS


def test_head_is_a_short_sha_or_a_named_absence() -> None:
    h = provenance.head()
    base = h.removesuffix("-dirty")
    assert base == provenance.UNKNOWN or (len(base) == 7 and base.isalnum())


def test_head_matches_git() -> None:
    real = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"],
        capture_output=True,
        text=True,
        cwd=provenance.HERE,
        check=True,
    ).stdout.strip()
    assert provenance.head().startswith(real)


def test_a_directory_outside_a_repo_is_named_not_guessed(tmp_path) -> None:
    """'nogit' is a statement. An empty string or a plausible-looking sha
    would be a lie, and the whole point is that the stamp can be trusted."""
    assert provenance.head(tmp_path) == provenance.UNKNOWN


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A one-commit repository, so dirtiness is the only variable."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = functools.partial(subprocess.run, cwd=repo, check=True, capture_output=True)
    run(["git", "init", "-q"])
    run(["git", "config", "user.email", "t@t"])
    run(["git", "config", "user.name", "t"])
    (repo / "code.py").write_text("x = 1\n")
    (repo / "results.jsonl").write_text("{}\n")
    run(["git", "add", "-A"])
    run(["git", "commit", "-qm", "first"])
    provenance.head.cache_clear()
    return repo


def test_a_clean_tree_is_not_flagged(tmp_path) -> None:
    assert not provenance.head(_repo(tmp_path)).endswith("-dirty")


def test_uncommitted_code_is_flagged(tmp_path) -> None:
    """A run against modified code is not reproducible from any commit."""
    repo = _repo(tmp_path)
    (repo / "code.py").write_text("x = 2\n")
    provenance.head.cache_clear()
    assert provenance.head(repo).endswith("-dirty")


def test_an_appended_data_file_is_not_flagged(tmp_path) -> None:
    """The contract head() states, and the one the live tree kept breaking.

    The first trial of any batch appends to results.jsonl, so a flag that
    counted data files would be set for the whole of every run. This test used
    to read the real repository and compare against raw `git status
    --porcelain`, which counts them -- so it failed for as long as any batch
    was running, on the code behaving exactly as documented.
    """
    repo = _repo(tmp_path)
    (repo / "results.jsonl").write_text('{}\n{"row": 2}\n')
    provenance.head.cache_clear()
    assert not provenance.head(repo).endswith("-dirty")


def test_code_dirt_outweighs_data_dirt(tmp_path) -> None:
    repo = _repo(tmp_path)
    (repo / "results.jsonl").write_text('{}\n{"row": 2}\n')
    (repo / "code.py").write_text("x = 2\n")
    provenance.head.cache_clear()
    assert provenance.head(repo).endswith("-dirty")


def test_every_log_record_carries_the_stamp(caplog) -> None:
    stamp = provenance.configure()
    logging.getLogger("probe").info("hello")
    formatter = logging.Formatter("%(levelname)s [%(harness)s] %(message)s")
    for record in caplog.records:
        provenance._Stamp(stamp).filter(record)
        assert stamp in formatter.format(record)


def test_configure_returns_the_same_stamp_it_installs() -> None:
    assert provenance.configure() == provenance.head()


def test_fingerprint_identifies_content_not_path(tmp_path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text('{"x":1}\n')
    b.write_text('{"x":1}\n')
    assert provenance.fingerprint(a) == provenance.fingerprint(b)
    b.write_text('{"x":2}\n')
    assert provenance.fingerprint(a) != provenance.fingerprint(b)


@pytest.mark.skipif(not HAS_LOCAL_RESULTS, reason=SKIP_NO_RESULTS)
def test_fingerprint_counts_rows() -> None:
    p = results.default_path()
    assert provenance.fingerprint(p).split()[0].isdigit()


def test_a_missing_file_is_absent_not_an_error(tmp_path) -> None:
    assert provenance.fingerprint(tmp_path / "gone.jsonl") == "absent"


def test_no_entry_point_calls_basicConfig_directly() -> None:
    """basicConfig produces unstamped lines. provenance.configure() is the
    only way to set up logging in this package."""
    # This file names the call in order to forbid it, so it is exempt; so is
    # the module that legitimately wraps it.
    exempt = {"provenance.py", pathlib.Path(__file__).name}
    offenders = [
        p.name
        for p in provenance.HERE.glob("*.py")
        if p.name not in exempt and "logging.basicConfig(" in p.read_text()
    ]
    assert not offenders, f"{offenders} bypass provenance.configure()"


def _repo(tmp_path):
    import subprocess

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "code.py").write_text("x = 1\n")
    (tmp_path / "results.jsonl").write_text('{"a":1}\n')
    git("add", "-A")
    git("commit", "-qm", "initial")
    return tmp_path


def test_appending_to_a_data_file_is_not_dirty(tmp_path) -> None:
    """A benchmark writes results.jsonl on its first trial. If that counted as
    dirty, every run after the first would be flagged and the flag would stop
    being read."""
    repo = _repo(tmp_path)
    (repo / "results.jsonl").write_text('{"a":1}\n{"a":2}\n')
    assert not provenance.code_is_dirty(repo)


def test_changing_code_is_dirty(tmp_path) -> None:
    repo = _repo(tmp_path)
    (repo / "code.py").write_text("x = 2\n")
    assert provenance.code_is_dirty(repo)


def test_a_new_untracked_source_file_is_dirty(tmp_path) -> None:
    repo = _repo(tmp_path)
    (repo / "new.py").write_text("y = 1\n")
    assert provenance.code_is_dirty(repo)


def test_a_new_log_file_is_not_dirty(tmp_path) -> None:
    repo = _repo(tmp_path)
    (repo / "run.log").write_text("noise\n")
    assert not provenance.code_is_dirty(repo)


def test_code_and_data_together_are_dirty(tmp_path) -> None:
    """The data change must not mask the code change."""
    repo = _repo(tmp_path)
    (repo / "results.jsonl").write_text('{"a":9}\n')
    (repo / "code.py").write_text("x = 3\n")
    assert provenance.code_is_dirty(repo)


def test_a_clean_tree_is_clean(tmp_path) -> None:
    assert not provenance.code_is_dirty(_repo(tmp_path))


# --- a log line must name its machine (#85) ---------------------------------


def test_the_machine_slug_distinguishes_our_two_machines():
    """A line reading `[abc1234]` could have come from either machine.

    The guiding principle: a run on DeepSeek on the MacBook must never be
    mistakable for one on ornith on the Linux box with a 3080 Ti.
    """
    import sys

    sys.path.insert(
        0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts")
    )
    import hardware_id as hw

    mac = hw.short_slug({"chip": "Apple M5 Max", "memory_gb": 128}, "darwin")
    linux = hw.short_slug(
        {
            "cpu": "AMD Ryzen 9 7900X 12-Core Processor",
            "gpu": "NVIDIA GeForce RTX 3080 Ti",
        },
        "linux",
    )
    assert mac == "M5-Max-128GB"
    assert linux == "Ryzen9-7900X-RTX3080Ti"
    assert mac != linux


def test_every_log_line_carries_commit_and_machine(caplog):
    """Not only the banner. A pasted line has to stand on its own."""
    stamp = provenance._Stamp("abc1234")
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    assert stamp.filter(record) is True
    assert record.harness == "abc1234"
    assert record.machine
    assert record.machine != "unknown-machine"


def test_the_log_format_includes_both():
    source = pathlib.Path(provenance.__file__).read_text()
    assert "[%(harness)s@%(machine)s]" in source


def test_the_filename_names_the_machine_too():
    """A file copied out of its directory must still say which machine wrote it."""
    path = provenance.log_path("report", machine_specific=True)
    assert provenance.machine_slug() in path.name
    assert path.parent.name == "logs"
    shared = provenance.log_path("hf-sweep", machine_specific=False)
    assert shared.parent.parts[-2:] == ("logs", "sweeps")


def test_committed_logs_all_name_their_machine():
    """Every kept log must be attributable from its filename alone.

    Three files were committed before the slug was in the name -- including a
    `demo-` artifact -- which is exactly the ambiguity this is meant to remove.
    """
    repo = pathlib.Path(__file__).resolve().parent.parent.parent
    for log in (repo / "hardware").rglob("logs/*.log"):
        stem = log.stem
        assert not stem.startswith("demo-"), f"test artifact committed: {log}"
        # `<script>-<slug>-<UTC>`: at least three dash-separated parts, and the
        # timestamp is last.
        assert stem.split("-")[-1].endswith("Z"), f"no UTC stamp: {log}"
        assert len(stem.split("-")) >= 3, f"filename does not name a machine: {log}"


def test_harness_dirty_ignores_the_results_file(tmp_path, monkeypatch) -> None:
    """The consumer of this rule, which had its own stricter one.

    run.py asked raw `git status --porcelain`, so results.jsonl -- appended to
    by every run -- set harness_dirty on essentially every row ever recorded.
    stack_agent_report voids a read-out when any row carries the flag, so a
    pre-registered screen was guaranteed to void itself on a condition that is
    always true.
    """
    import run

    repo = _repo(tmp_path)
    (repo / "results.jsonl").write_text('{}\n{"row": 2}\n')
    monkeypatch.setattr(run, "HERE", repo)
    assert run.provenance.code_is_dirty(repo) is False

    (repo / "code.py").write_text("x = 2\n")
    assert run.provenance.code_is_dirty(repo) is True


def test_an_untracked_run_directory_is_not_code_dirt(tmp_path) -> None:
    """A run writes its own output inside the tree.

    benchmarks/ds4/<prefix>-runN is untracked, so by repetition two the tree is
    dirty because of the run asking the question. --require-harness-head
    refuses on dirty code, so an untracked-sensitive check there would refuse
    every multi-run A/B -- the comparisons the pin exists to protect.
    """
    repo = _repo(tmp_path)
    (repo / "benchmarks-ds4-run1").mkdir()
    (repo / "benchmarks-ds4-run1" / "a.csv").write_text("x\n")
    assert provenance.code_is_dirty(repo, untracked=False) is False
    assert provenance.code_is_dirty(repo) is True, "the default still sees it"


def git_reports(repo) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_a_runs_own_output_directory_is_not_code_dirt(tmp_path) -> None:
    """Every engine A/B row carried `-dirty` because of its own output.

    git reports an untracked DIRECTORY, "benchmarks/ds4/<prefix>-runN/", which
    no suffix rule matches. The rows that carry this flag are the ones quoted
    in issues, so a flag that is always set is worse than no flag.
    """
    repo = _repo(tmp_path)
    # benchmarks/ds4/ must already be tracked, or git collapses the report to
    # the topmost untracked directory ("?? benchmarks/") and the test would be
    # measuring the fixture rather than the rule.
    tracked = repo / "benchmarks" / "ds4" / "earlier-run"
    tracked.mkdir(parents=True)
    (tracked / "main-rep1.csv").write_text("ctx,tps\n2048,500\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "earlier run"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    out = repo / "benchmarks" / "ds4" / "pr964-rerun-run1"
    out.mkdir(parents=True)
    (out / "main-rep1.csv").write_text("ctx,tps\n2048,500\n")
    assert git_reports(repo).startswith("?? benchmarks/ds4/"), git_reports(repo)
    assert provenance.code_is_dirty(repo) is False


def test_a_csv_outside_the_output_tree_still_counts(tmp_path) -> None:
    """The prefix rule is scoped; it does not excuse data anywhere at all."""
    repo = _repo(tmp_path)
    (repo / "new_module.py").write_text("x = 3\n")
    assert provenance.code_is_dirty(repo) is True
