"""The re-grade must only ever grade a row with that row's own patch.

Patch file names repeat across batches, so a later batch overwrites an
earlier one's file. Grading a row with another row's patch would attach a
verdict to work the row never did; the sha256 check is the whole defense.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import rescore_hidden as rh


def row(patch: pathlib.Path | None, sha: str | None, **extra: object) -> dict:
    return {
        "task": "replay-web-auth-hidden",
        "backend": "b",
        "trial": 1,
        "started": "2026-09-27T00:10:40-04:00",
        "hidden": {"pytest": "1 error"},
        "solution_patch": str(patch) if patch else None,
        "solution_sha256": sha,
    } | extra


def test_the_rows_own_patch_matches(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "x.patch"
    p.write_text("diff\n")
    assert rh.match(row(p, hashlib.sha256(b"diff\n").hexdigest())) == p


def test_an_overwritten_patch_does_not_match(tmp_path: pathlib.Path) -> None:
    """A later batch wrote a different diff under the same name."""
    p = tmp_path / "x.patch"
    p.write_text("a later batch's diff\n")
    assert rh.match(row(p, hashlib.sha256(b"diff\n").hexdigest())) is None


def test_a_missing_patch_or_hash_does_not_match(tmp_path: pathlib.Path) -> None:
    assert rh.match(row(tmp_path / "gone.patch", "0" * 64)) is None
    assert rh.match(row(None, "0" * 64)) is None
    p = tmp_path / "x.patch"
    p.write_text("diff\n")
    assert rh.match(row(p, None)) is None


def test_rows_for_keeps_only_the_task_with_a_hidden_verdict(
    tmp_path: pathlib.Path,
) -> None:
    ledger = tmp_path / "results.jsonl"
    rows = [
        row(None, None),
        row(None, None, task="other"),
        row(None, None, hidden=None),
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    assert len(rh.rows_for(ledger, "replay-web-auth-hidden")) == 1


def test_the_key_names_the_row_and_its_patch_hash() -> None:
    got = rh.key(row(None, "abc"))
    assert got == {
        "task": "replay-web-auth-hidden",
        "backend": "b",
        "trial": 1,
        "started": "2026-09-27T00:10:40-04:00",
        "solution_sha256": "abc",
    }


def test_patch_dir_finds_a_container_path_by_name(tmp_path: pathlib.Path) -> None:
    """The cluster records /root/bench-solutions/..., which no host has.

    The recorded path is built under tmp_path, not taken from /root: on a
    Linux host /root exists and is unreadable, and Python before 3.14 raises
    PermissionError from is_file() there. That made this test depend on the
    host's disk; the unreadable case has its own test below.
    """
    (tmp_path / "x.patch").write_text("diff\n")
    r = row(
        tmp_path / "container-root" / "bench-solutions" / "x.patch",
        hashlib.sha256(b"diff\n").hexdigest(),
    )
    assert rh.match(r) is None
    assert rh.match(r, tmp_path) == tmp_path / "x.patch"


def test_patch_dir_still_refuses_another_clients_patch(tmp_path: pathlib.Path) -> None:
    """Two clients wrote patches with the same name; only the hash tells them apart."""
    (tmp_path / "x.patch").write_text("the other client's diff\n")
    r = row(
        pathlib.Path("/root/bench-solutions/x.patch"),
        hashlib.sha256(b"diff\n").hexdigest(),
    )
    assert rh.match(r, tmp_path) is None


def test_an_unreadable_recorded_path_is_unrecoverable_not_a_crash(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cluster row records /root/bench-solutions/...; on a Linux host /root
    is there but unreadable, and is_file() raises PermissionError on Python
    3.11-3.13. One such row must not stop the re-grade of every other row."""

    def denied(self: pathlib.Path) -> bool:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(pathlib.Path, "is_file", denied)
    r = row(pathlib.Path("/root/bench-solutions/x.patch"), "0" * 64)
    assert rh.match(r) is None


REPLAY = {
    "commit": "52eaf52a28d9b2234f69161a895180d18d0b7295",
    "reverted": [
        {"path": "src/a.py", "action": "restored"},
        {"path": "src/b.py", "action": "deleted"},
    ],
}
HIDDEN_TESTS = ["tests/test_a.py::test_one"]


def rebuilt(**over: object) -> dict:
    return {
        "commit": REPLAY["commit"],
        "reverted": REPLAY["reverted"],
        "span_start": None,
        "hidden_tests": HIDDEN_TESTS,
    } | over


def recorded(**over: object) -> dict:
    return (
        row(None, "0" * 64, replay=dict(REPLAY), hidden={"tests": HIDDEN_TESTS}) | over
    )


def test_a_tree_rebuilt_as_the_trial_had_it_is_accepted() -> None:
    assert rh.provenance_mismatch(recorded(), rebuilt()) == []


@pytest.mark.parametrize(
    ("change", "word"),
    [
        ({"commit": "1" * 40}, "commit"),
        ({"reverted": REPLAY["reverted"][:1]}, "reverted"),
        (
            {
                "reverted": [{"path": "src/a.py", "action": "deleted"}]
                + [REPLAY["reverted"][1]]
            },
            "reverted",
        ),
        ({"span_start": "2" * 40}, "span_start"),
        ({"hidden_tests": [*HIDDEN_TESTS, "tests/test_a.py::test_two"]}, "hidden"),
    ],
)
def test_a_task_changed_since_the_trial_is_refused(change: dict, word: str) -> None:
    """A matching patch hash proves the patch, not the tree it was applied to.
    The task's base commit, revert set, span or hidden tests changing after
    the trial means the re-grade would grade different source (#801 review)."""
    got = rh.provenance_mismatch(recorded(), rebuilt(**change))
    assert got and word in got[0]


def test_a_row_without_replay_provenance_is_refused() -> None:
    bare = {k: v for k, v in recorded().items() if k != "replay"}
    assert rh.provenance_mismatch(bare, rebuilt())


def test_regrade_refuses_a_changed_task_before_applying_the_patch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rh.harness, "build_checkout", lambda *a: None)
    monkeypatch.setattr(rh.replay, "revert", lambda *a: REPLAY["reverted"])
    monkeypatch.setattr(rh.replay, "hide", lambda *a: None)
    monkeypatch.setattr(rh.replay, "resolve", lambda source, ref: "1" * 40)
    monkeypatch.setattr(
        rh.subprocess,
        "run",
        lambda *a, **k: pytest.fail("the patch must not be applied"),
    )
    task = {
        "name": "replay-web-auth-hidden",
        "base_commit": "1111111",
        "revert": ["src/a.py", "src/b.py"],
        "hidden_tests": HIDDEN_TESTS,
    }
    cfg = {"repo": "~/git/x", "base_commit": "abc"}
    got = rh.regrade(
        cfg, task, tmp_path, tmp_path / "p.patch", tmp_path / "w", recorded()
    )
    assert "commit" in got["error"]


def _main_fixture(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, outcome: dict
) -> list[str]:
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(json.dumps(recorded()) + "\n")
    monkeypatch.setattr(rh, "match", lambda r, d=None: tmp_path / "x.patch")
    monkeypatch.setattr(rh.verifier, "source_for", lambda *a: tmp_path)
    monkeypatch.setattr(rh, "regrade", lambda *a: dict(outcome))
    return ["--task", "replay-web-auth-hidden", "--ledger", str(ledger),
            "--out", str(tmp_path / "out.jsonl")]  # fmt: skip


def test_a_failed_regrade_is_not_appended_and_fails_the_run(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error record in the verdict file broke report.py (KeyError on
    hidden_counts) and, being newest, superseded the row's valid re-grade."""
    argv = _main_fixture(tmp_path, monkeypatch, {"error": "git apply failed: x"})
    assert rh.main(argv) == 1
    assert not (tmp_path / "out.jsonl").exists()


def test_a_successful_regrade_is_appended(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok = {
        "hidden_ref": "r",
        "drop_imports": [],
        "hidden_counts": {"passed": 1, "failed": 0, "skipped": 0, "errors": 0},
        "hidden_passed": True,
    }
    argv = _main_fixture(tmp_path, monkeypatch, ok)
    assert rh.main(argv) == 0
    got = json.loads((tmp_path / "out.jsonl").read_text())
    assert got["hidden_passed"] is True
