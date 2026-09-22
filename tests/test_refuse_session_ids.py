"""Session URLs and IDs never reach a commit or PR (AGENTS.md)."""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import refuse_session_ids as rs

# Built from parts so this file does not itself carry a literal session ID.
FAKE_ID = "session_01" + "Abc123XyZ" * 3
URL = "https://claude.ai/code/" + FAKE_ID


@pytest.mark.parametrize(
    "text",
    [
        f"fix: thing\n\n{URL}\n",
        f"fix: thing\n\nClaude-Session: {URL}\n",
        "fix: thing\n\nClaude-Session: whatever\n",
        "fix: thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
        f"see {FAKE_ID} for context",
    ],
)
def test_forbidden_forms_are_refused(text) -> None:
    assert rs.hits(text)


@pytest.mark.parametrize(
    "text",
    [
        "docs: never publish a `Claude-Session:` line or a claude.ai/code/session_… URL",
        "Sign comments with --opus.\n",
        "session_01 is how the IDs start",
    ],
)
def test_describing_the_rule_is_allowed(text) -> None:
    assert rs.hits(text) == []


def test_commit_msg_mode_exit_codes(tmp_path) -> None:
    bad, good = tmp_path / "bad", tmp_path / "good"
    bad.write_text(f"subject\n\nClaude-Session: {URL}\n")
    good.write_text("subject\n\nbody\n")
    assert rs.main([str(bad)]) == 1
    assert rs.main([str(good)]) == 0


def test_text_mode(capsys) -> None:
    assert rs.main(["--text", f"PR body\n\n{URL}"]) == 1
    assert "refused" in capsys.readouterr().err
    assert rs.main(["--text", "PR body"]) == 0


def test_git_range_mode_names_the_commit(tmp_path, monkeypatch, capsys) -> None:
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("commit", "-q", "--allow-empty", "-m", "base")
    git("commit", "-q", "--allow-empty", "-m", "clean")
    git("commit", "-q", "--allow-empty", "-m", f"dirty\n\nClaude-Session: {URL}")
    monkeypatch.chdir(tmp_path)
    assert rs.main(["--git-range", "HEAD~2..HEAD"]) == 1
    assert "commit " in capsys.readouterr().err
    assert rs.main(["--git-range", "HEAD~2..HEAD~1"]) == 0


def test_the_hook_is_standard_library_only() -> None:
    src = (ROOT / "scripts" / "refuse_session_ids.py").read_text()
    imports = {
        line.split()[1].split(".")[0]
        for line in src.splitlines()
        if line.startswith(("import ", "from "))
        and not line.startswith("from __future__")
    }
    assert imports <= {"argparse", "re", "subprocess", "sys"}


def test_the_config_runs_it_at_commit_msg() -> None:
    cfg = (ROOT / ".pre-commit-config.yaml").read_text()
    assert "scripts/refuse_session_ids.py" in cfg
    assert "stages: [commit-msg]" in cfg
    assert "default_install_hook_types: [pre-commit, commit-msg, pre-push]" in cfg, (
        "pre-push joined the installed types when `pytest -m fast` became the "
        "push gate; the commit-msg guard this test protects is unaffected"
    )


def test_ci_checks_pr_text_and_commits() -> None:
    wf = (ROOT / ".github" / "workflows" / "no-session-ids.yml").read_text()
    assert "refuse_session_ids.py --text" in wf
    assert "refuse_session_ids.py --git-range" in wf
    assert "edited" in wf


def test_the_commit_msg_hook_is_installed_into_the_repo() -> None:
    """A config entry alone reads as covered while a clone that never re-ran
    `pre-commit install` commits straight past it. Existing clones need
    `uv run pre-commit install` once to pick up the commit-msg hook."""
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    hook = (ROOT / common).resolve() / "hooks" / "commit-msg"
    assert hook.exists(), "commit-msg hook missing; run `uv run pre-commit install`"
    assert "pre-commit" in hook.read_text()
