"""The prune script deletes weights, so its guards are the part worth testing.

Everything here is about refusing: refusing a path outside the model roots,
refusing to delete a KEEP entry, refusing to touch REVIEW without being told,
and never removing an Ollama tag with a filesystem delete.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import prune_models as pm


def test_every_entry_has_a_tier_we_understand() -> None:
    for entry in pm.PLAN:
        assert entry.tier in (pm.KEEP, pm.DELETE, pm.REVIEW), entry.name


def test_entry_names_are_unique() -> None:
    """--also takes a name, so a duplicate would silently select the wrong one."""
    names = [e.name for e in pm.PLAN]
    assert len(names) == len(set(names))


def test_an_entry_is_either_a_path_or_an_ollama_tag_never_both() -> None:
    for entry in pm.PLAN:
        assert bool(entry.path) != bool(entry.ollama_tag), entry.name


def test_everything_deletable_says_how_to_get_it_back() -> None:
    """Re-downloadability is the entire justification for deleting it."""
    for entry in pm.PLAN:
        if entry.tier in (pm.DELETE, pm.REVIEW):
            assert entry.redownload.strip(), entry.name


def test_every_entry_gives_a_reason() -> None:
    for entry in pm.PLAN:
        assert len(entry.reason) > 40, entry.name


def test_the_eight_keepers_are_actually_kept() -> None:
    """The models carrying our published results must never be selected."""
    kept = {e.name for e in pm.PLAN if e.tier == pm.KEEP}
    for name in (
        "ds4-primary",
        "qwen38fnds4-pack",
        "qwen38fnq3",
        "qwen38fnq3reap",
        "glm53-antirez",
        "ornith15",
        "qwen36coding",
        "gemma426",
    ):
        assert name in kept, name


def test_the_open_base_weights_are_not_in_the_delete_tier() -> None:
    """CONVENTIONS.md: not fair game is anything hard to reacquire. Ask first."""
    base = next(e for e in pm.PLAN if e.name == "deepseek-hf-base")
    assert base.tier == pm.REVIEW


def test_models_blocked_on_open_issues_are_review_not_delete() -> None:
    """#51 and #80 are open, and their weights are the experiment."""
    for name in ("ds4-aproj-q4", "ds4-aproj-q8", "qwen38flashnext-mlx", "qwen36a3b"):
        entry = next(e for e in pm.PLAN if e.name == name)
        assert entry.tier == pm.REVIEW, name


# --- the path guard ------------------------------------------------------


def test_a_path_outside_the_model_roots_is_refused() -> None:
    assert pm.is_allowed(pm.HOME / "git/local-llm/README.md") is False
    assert pm.is_allowed(pathlib.Path("/etc/passwd")) is False
    assert pm.is_allowed(pm.HOME) is False


def test_the_roots_themselves_are_not_deletable() -> None:
    """A bug that resolved an entry to the root would wipe every model at once."""
    for root in pm.ALLOWED_ROOTS:
        assert pm.is_allowed(root) is False


def test_a_path_inside_a_model_root_is_allowed() -> None:
    assert pm.is_allowed(pm.HOME / "models/SomeModel") is True
    assert pm.is_allowed(pm.HOME / "git/ds4/gguf/x.gguf") is True


def test_traversal_out_of_a_root_is_refused() -> None:
    assert pm.is_allowed(pm.HOME / "models/../git/local-llm") is False


def test_remove_refuses_a_path_outside_the_roots(tmp_path, caplog) -> None:
    """The last line of defence, tested rather than assumed."""
    victim = tmp_path / "important.txt"
    victim.write_text("do not delete me")
    entry = pm.Entry(
        name="bad",
        path=str(victim),  # absolute, so HOME / path resolves to victim itself
        ollama_tag=None,
        tier=pm.DELETE,
        reason="a table bug that points somewhere it should not, for the test",
        redownload="n/a",
    )
    assert pm.remove(entry, dry_run=False) is False
    assert victim.exists(), "the guard let a file outside the roots be deleted"


def test_an_ollama_entry_never_touches_the_filesystem(monkeypatch) -> None:
    """Ollama blobs are shared between tags; rm -rf corrupts unrelated models."""
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(pm.subprocess, "run", fake_run)

    def explode(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("rmtree called for an Ollama model")

    monkeypatch.setattr(pm.shutil, "rmtree", explode)

    entry = next(e for e in pm.PLAN if e.name == "ornith-35b-retired")
    assert pm.remove(entry, dry_run=False) is True
    assert calls == [["ollama", "rm", "ornith:35b"]]


def test_a_failed_ollama_rm_is_reported_not_swallowed(monkeypatch) -> None:
    class Result:
        returncode = 1
        stdout = ""
        stderr = "model not found"

    monkeypatch.setattr(pm.subprocess, "run", lambda *a, **k: Result())
    entry = next(e for e in pm.PLAN if e.name == "ornith-35b-retired")
    assert pm.remove(entry, dry_run=False) is False


def test_dry_run_deletes_nothing(tmp_path, monkeypatch) -> None:
    target = tmp_path / "models" / "Doomed"
    target.mkdir(parents=True)
    (target / "weights.gguf").write_text("x" * 100)
    monkeypatch.setattr(pm, "HOME", tmp_path)
    monkeypatch.setattr(pm, "ALLOWED_ROOTS", (tmp_path / "models",))
    entry = pm.Entry(
        name="doomed",
        path="models/Doomed",
        ollama_tag=None,
        tier=pm.DELETE,
        reason="a fixture standing in for a superseded model, long enough to pass",
        redownload="hf download something",
    )
    assert pm.remove(entry, dry_run=True) is True
    assert target.exists(), "dry run must not delete"


def test_a_real_delete_removes_the_directory(tmp_path, monkeypatch) -> None:
    target = tmp_path / "models" / "Doomed"
    target.mkdir(parents=True)
    (target / "weights.gguf").write_text("x" * 100)
    monkeypatch.setattr(pm, "HOME", tmp_path)
    monkeypatch.setattr(pm, "ALLOWED_ROOTS", (tmp_path / "models",))
    entry = pm.Entry(
        name="doomed",
        path="models/Doomed",
        ollama_tag=None,
        tier=pm.DELETE,
        reason="a fixture standing in for a superseded model, long enough to pass",
        redownload="hf download something",
    )
    assert pm.remove(entry, dry_run=False) is True
    assert not target.exists()


def test_a_missing_target_is_not_an_error(tmp_path, monkeypatch) -> None:
    """Re-running after a successful prune must be a no-op, not a crash."""
    monkeypatch.setattr(pm, "HOME", tmp_path)
    monkeypatch.setattr(pm, "ALLOWED_ROOTS", (tmp_path / "models",))
    entry = pm.Entry(
        name="gone",
        path="models/AlreadyGone",
        ollama_tag=None,
        tier=pm.DELETE,
        reason="a fixture for the already-deleted case, long enough to pass the check",
        redownload="hf download something",
    )
    assert pm.remove(entry, dry_run=False) is False


@pytest.mark.parametrize("tier", [pm.KEEP])
def test_no_keep_entry_carries_a_redownload_command(tier: str) -> None:
    """A KEEP line offering re-download reads as an invitation to delete it."""
    for entry in pm.PLAN:
        if entry.tier == tier:
            assert entry.redownload == "", entry.name
