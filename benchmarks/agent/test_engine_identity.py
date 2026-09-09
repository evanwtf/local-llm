"""Which engine build served a row (#192).

A row must name the engine build that produced it, the way the decode A/Bs
already do. Two kinds of engine have to work: a git tree (ds4, llama.cpp),
whose version is a sha, and a brew binary (ollama, mtplx, mlx-serve), whose
version is `--version` output. `engine_dirty` and `engine_built` are not
padding -- a sha names a commit, not a binary, and a tree rebuilt from
uncommitted changes reports a clean sha while running different code.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import time

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


def test_a_tree_based_mlx_serve_records_the_sha_not_the_brew_version(
    tmp_path, monkeypatch
):
    """#225 arm B is a git checkout; its rows must name the sha, not the brew
    --version. Passing the tree makes identity() take the git branch."""
    tree = _git_tree(tmp_path)
    fake = tmp_path / "brew-mlx-serve"
    fake.write_text("#!/bin/sh\necho 'mlx-serve 26.9.1'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(engine_identity.shutil, "which", lambda _: str(fake))
    got = engine_identity.identity("mlx-serve", tree=str(tree))
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert got["engine_version"] == sha
    assert got["engine_version"] != "mlx-serve 26.9.1"
    assert got["engine_tree"] == str(tree)


def test_a_tree_based_mlx_serve_records_the_tree_binarys_mtime(tmp_path, monkeypatch):
    """engine_built must be the tree's own zig build, not the brew binary's.

    The mtime is the one fact that survives a rebuild from uncommitted code; a
    tree-based arm that records the brew binary's mtime would hide a rebuild
    exactly when the sha is unchanged. `binary_rel` is what makes _binary_path
    resolve to the tree's build rather than falling through to PATH.
    """
    tree = _git_tree(tmp_path)
    built = tree / "zig-out" / "bin" / "mlx-serve"
    built.parent.mkdir(parents=True)
    built.write_text("#!/bin/sh\necho hi\n")
    built.chmod(0o755)
    fake = tmp_path / "brew-mlx-serve"
    fake.write_text("#!/bin/sh\necho 'mlx-serve 26.9.1'\n")
    fake.chmod(0o755)
    # A distinct mtime, or the test is vacuous: two binaries created within the
    # same second have equal truncated-to-seconds mtimes, and the brew binary
    # would win without being noticed.
    os.utime(fake, (0, 0))
    monkeypatch.setattr(engine_identity.shutil, "which", lambda _: str(fake))
    got = engine_identity.identity("mlx-serve", tree=str(tree))
    m = int(built.stat().st_mtime)
    expected = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(m))
    assert got["engine_built"] == expected


def test_two_mlx_serve_builds_produce_different_engine_versions(tmp_path, monkeypatch):
    """#225: the two arms must be distinguishable after the fact.

    A brew arm (no tree) reports the --version string; a git arm (tree) reports
    the sha. If both reported the brew version the run would be worthless -- the
    field is the only thing separating the arms in the ledger.
    """
    tree = _git_tree(tmp_path)
    fake = tmp_path / "brew-mlx-serve"
    fake.write_text("#!/bin/sh\necho 'mlx-serve 26.9.1'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(engine_identity.shutil, "which", lambda _: str(fake))
    brew = engine_identity.identity("mlx-serve")
    git = engine_identity.identity("mlx-serve", tree=str(tree))
    assert brew["engine_version"] == "mlx-serve 26.9.1"
    assert git["engine_version"] != brew["engine_version"]


def test_the_two_mlx_serve_backends_are_distinguishable() -> None:
    """#225: arm A (brew) and arm B (git) must not pool in the ledger.

    Same URL and pack, different names; the git arm declares its tree so
    engine_identity stamps the sha, the brew arm does not.
    """
    import tomllib

    with (pathlib.Path(__file__).parent / "tasks.toml").open("rb") as fh:
        backends = tomllib.load(fh)["backend"]
    brew = backends["qwen38fnmlxserve"]
    git = backends["qwen38fnmlxserve-git"]
    assert brew["engine"] == git["engine"] == "mlx-serve"
    assert brew["base_url"] == git["base_url"], "same URL"
    assert brew["model"] == git["model"], "same pack"
    assert "engine_tree" not in brew, "the brew arm has no tree"
    assert git["engine_tree"] == "~/git/mlx-serve"


def test_engine_built_is_the_binary_mtime_in_iso(tmp_path):
    """A rebuilt binary has a new mtime even when the sha is unchanged.

    The mtime is written in ISO 8601 with an explicit offset, the same shape
    the harness uses for every timestamp, so a row is comparable across runs.
    """
    tree = _git_tree(tmp_path)
    binary = tree / "ds4-server"
    binary.write_text("#!/bin/sh\necho hi\n")
    binary.chmod(0o755)
    got = engine_identity.identity("ds4", tree=str(tree))
    m = int(binary.stat().st_mtime)
    expected = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(m))
    assert got["engine_built"] == expected


def test_a_failed_git_status_is_unknown_not_clean(tmp_path, monkeypatch):
    """A git that cannot answer must not read as a clean tree.

    `_git` returns None on failure and "" when clean; both are falsy, so a
    naive `if status:` would omit `engine_dirty` either way and a failed
    status would read as verified-clean. The tri-state keeps them apart.
    """
    tree = _git_tree(tmp_path)
    real = engine_identity._git

    def flaky(*args, **kw):
        if args and args[0] == "status":
            return None
        return real(*args, **kw)

    monkeypatch.setattr(engine_identity, "_git", flaky)
    got = engine_identity.identity("ds4", tree=str(tree))
    assert got["engine_dirty"] == "unknown"


def test_a_hosted_backend_has_no_engine() -> None:
    """opus5 is hosted Claude; stamping a local ds4 sha on it would be
    invented provenance on the baseline every local number is read against."""
    import tomllib

    with (pathlib.Path(__file__).parent / "tasks.toml").open("rb") as fh:
        backends = tomllib.load(fh)["backend"]
    assert "engine" not in backends["opus5"]


def _ds4_tree_from_description(desc: str) -> str | None:
    """The tree a ds4 description names, or None if it names none."""
    if "ds4-metal" in desc:
        return "~/git/ds4-metal"
    if "ivanfioravanti/ds4" in desc:
        return "~/git/ds4-ivan-qwen38fn"
    if "upstream/main" in desc:
        return "~/git/ds4-main"
    return None


def test_every_ds4_backend_whose_description_names_a_tree_declares_it() -> None:
    """A description that names a tree must declare it, or the row would
    resolve to nothing -- or, before #195, to the wrong fork. ds4 has four
    trees, so a wrong default is worse than none."""
    import tomllib

    with (pathlib.Path(__file__).parent / "tasks.toml").open("rb") as fh:
        backends = tomllib.load(fh)["backend"]
    for name, cfg in backends.items():
        if cfg.get("engine") != "ds4":
            continue
        expected = _ds4_tree_from_description(cfg.get("description", ""))
        if expected is None:
            continue
        assert cfg.get("engine_tree") == expected, (
            f"{name} names {expected!r} in its description but declares "
            f"{cfg.get('engine_tree')!r}"
        )


def test_pld_reads_the_running_argv_not_a_belief(monkeypatch):
    """--no-pld in the served process's argv is the only thing that means off."""
    monkeypatch.setattr(
        engine_identity, "_argv_of", lambda _b: ["mlx-serve", "--serve", "--no-pld"]
    )
    assert engine_identity.pld_state() == "off"
    monkeypatch.setattr(
        engine_identity, "_argv_of", lambda _b: ["mlx-serve", "--serve", "--ctx-size"]
    )
    assert engine_identity.pld_state() == "on"


def test_no_server_is_not_the_same_as_pld_off(monkeypatch):
    """The distinction this field exists for.

    "n/a" means nothing was running to ask. Collapsing that into "off" would
    label every ds4-only run as one that disabled PLD, which is the exact
    class of confusion #191 paid for -- the same discipline `engine_dirty`
    uses when it refuses to write false for an unknown.
    """
    monkeypatch.setattr(engine_identity, "_argv_of", lambda _b: None)
    assert engine_identity.pld_state() == "n/a"
    assert engine_identity.pld_state() != "off"


def test_only_mlx_serve_carries_a_pld_key(monkeypatch, tmp_path):
    """An absent key must not read as a negative answer.

    ds4 has no PLD concept; writing "off" onto its rows would assert something
    about a draft path this field does not measure. ds4's own MTP state is #39.
    """
    monkeypatch.setattr(engine_identity, "_argv_of", lambda _b: ["mlx-serve"])
    assert "pld" in engine_identity.identity("mlx-serve")
    tree = tmp_path / "ds4"
    (tree / ".git").mkdir(parents=True)
    assert "pld" not in engine_identity.identity("ds4", tree=str(tree))


def test_a_brew_binary_reports_its_version_not_homebrews_git_sha(monkeypatch):
    """The bug this ordering exists to prevent, found by reading a real log.

    /opt/homebrew is itself a git checkout, so `git rev-parse` inside the
    Cellar answers with HOMEBREW's HEAD. Probing git before the Cellar labelled
    the 26.9.1 arm `mlx-serve/08e85c4e42` -- a real sha, of the wrong repo --
    while the source arm reported its own. Two arms both labelled with a sha
    and no way to tell them apart is worse than no label, and #225 compares
    exactly those two builds.
    """
    monkeypatch.setattr(
        engine_identity,
        "_argv_of",
        lambda n: (
            ["/opt/homebrew/Cellar/mlx-serve/26.9.1/bin/mlx-serve", "--serve"]
            if n == "mlx-serve"
            else None
        ),
    )
    monkeypatch.setattr(engine_identity, "_sha_of_tree", lambda _p: "08e85c4e42")
    assert engine_identity.running_engine() == "mlx-serve/26.9.1"


def test_a_source_build_reports_its_sha(monkeypatch):
    monkeypatch.setattr(
        engine_identity,
        "_argv_of",
        lambda n: (
            ["/Users/x/git/mlx-serve/zig-out/bin/mlx-serve"]
            if n == "mlx-serve"
            else None
        ),
    )
    monkeypatch.setattr(engine_identity, "_sha_of_tree", lambda _p: "25de4d5")
    assert engine_identity.running_engine() == "mlx-serve/25de4d5"


def test_nothing_serving_says_so(monkeypatch):
    """ "none" is a fact. A blank would read as a missing field."""
    monkeypatch.setattr(engine_identity, "_argv_of", lambda _n: None)
    assert engine_identity.running_engine() == "none"
