"""Tests for the stamp that makes every other output attributable.

If this module is wrong, every log line and every test report lies about which
code produced it -- which is worse than having no stamp, because a wrong
attribution is believed.
"""

from __future__ import annotations

import logging
import pathlib
import subprocess

import provenance


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


def test_the_dirty_flag_tracks_uncommitted_changes() -> None:
    """A run against a modified tree is not reproducible from any commit, and
    the line has to say so."""
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=provenance.HERE,
            check=True,
        ).stdout.strip()
    )
    assert provenance.head().endswith("-dirty") == dirty


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


def test_fingerprint_counts_rows() -> None:
    p = pathlib.Path(__file__).resolve().parent / "results.jsonl"
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
    assert not provenance._code_is_dirty(repo)


def test_changing_code_is_dirty(tmp_path) -> None:
    repo = _repo(tmp_path)
    (repo / "code.py").write_text("x = 2\n")
    assert provenance._code_is_dirty(repo)


def test_a_new_untracked_source_file_is_dirty(tmp_path) -> None:
    repo = _repo(tmp_path)
    (repo / "new.py").write_text("y = 1\n")
    assert provenance._code_is_dirty(repo)


def test_a_new_log_file_is_not_dirty(tmp_path) -> None:
    repo = _repo(tmp_path)
    (repo / "run.log").write_text("noise\n")
    assert not provenance._code_is_dirty(repo)


def test_code_and_data_together_are_dirty(tmp_path) -> None:
    """The data change must not mask the code change."""
    repo = _repo(tmp_path)
    (repo / "results.jsonl").write_text('{"a":9}\n')
    (repo / "code.py").write_text("x = 3\n")
    assert provenance._code_is_dirty(repo)


def test_a_clean_tree_is_clean(tmp_path) -> None:
    assert not provenance._code_is_dirty(_repo(tmp_path))
