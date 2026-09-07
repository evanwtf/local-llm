"""Which engine build served a row (#192).

A row must name the engine build that produced it, the way the decode A/Bs
already do. Two kinds of engine have to work: a git tree (ds4, llama.cpp),
whose version is a sha, and a brew binary (ollama, mtplx, mlx-serve), whose
version is `--version` output. `engine_dirty` and `engine_built` are not
padding -- a sha names a commit, not a binary, and a tree rebuilt from
uncommitted changes reports a clean sha while running different code.
"""

from __future__ import annotations

import pathlib
import subprocess

import engine_identity


def _git_tree(tmp_path, *, dirty: bool = False) -> pathlib.Path:
    """A throwaway git tree with one commit, optionally with uncommitted code."""
    tree = tmp_path / "tree"
    tree.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tree, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tree, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tree, check=True)
    (tree / "f").write_text("one\n")
    subprocess.run(["git", "add", "f"], cwd=tree, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=tree, check=True)
    if dirty:
        (tree / "f").write_text("two\n")
    return tree


def test_a_git_tree_reports_its_sha(tmp_path):
    tree = _git_tree(tmp_path)
    got = engine_identity.identity("ds4", tree=str(tree))
    assert got["engine_name"] == "ds4"
    assert got["engine_version"], "a git tree must report a sha"
    assert got["engine_tree"] == str(tree)
    assert "engine_dirty" not in got, "a clean tree is not dirty"


def test_a_dirty_git_tree_is_marked_dirty(tmp_path):
    tree = _git_tree(tmp_path, dirty=True)
    got = engine_identity.identity("ds4", tree=str(tree))
    assert got["engine_dirty"] is True, "uncommitted code must be recorded"


def test_an_unknown_engine_is_omitted_not_unrecorded():
    """`unrecorded` is a real answer for a server the harness did not start."""
    assert engine_identity.identity("no-such-engine") == {}


def test_a_brew_binary_reports_version_output(tmp_path, monkeypatch):
    """mlx-serve is a brew install; its version is --version, not a sha."""
    fake = tmp_path / "mlx-serve"
    fake.write_text("#!/bin/sh\necho 'mlx-serve 0.1.2'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(engine_identity.shutil, "which", lambda _: str(fake))
    got = engine_identity.identity("mlx-serve")
    assert got["engine_name"] == "mlx-serve"
    assert got["engine_version"] == "mlx-serve 0.1.2"
    assert "engine_dirty" not in got, "a brew binary has no tree to be dirty"
    assert got["engine_tree"] == str(fake.parent)


def test_engine_built_is_the_binary_mtime(tmp_path):
    """A rebuilt binary has a new mtime even when the sha is unchanged."""
    tree = _git_tree(tmp_path)
    binary = tree / "ds4-server"
    binary.write_text("#!/bin/sh\necho hi\n")
    binary.chmod(0o755)
    got = engine_identity.identity("ds4", tree=str(tree))
    assert got["engine_built"] == int(binary.stat().st_mtime)
