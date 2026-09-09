"""The commit-time guard (#227, #237).

A commit during a pinned run moves HARNESS_HEAD and kills every remaining
sweep. This hook is the only place that can stop it before HEAD moves.

Every test that names a date is drawn from a failure that reached the machine.
The guard has now been wrong in both directions -- too narrow to catch eleven
drivers, then broad enough to block every commit for hours while nothing was
running -- and both times the cause was the same: it was looking for processes
by name instead of reading the lock that records them.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = ROOT / "scripts" / "refuse_commit_during_benchmark.py"
CONFIG = ROOT / ".pre-commit-config.yaml"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import refuse_commit_during_benchmark as refuse
from source_text import code_of

HOSTNAME = "test-machine.local"


def lock_file(tmp_path: pathlib.Path, payload: object) -> pathlib.Path:
    path = tmp_path / "run-lock.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def held_by(pid: int, host: str = HOSTNAME) -> dict:
    return {
        "cwd": "/Users/x/git/local-llm",
        "hostname": host,
        "pid": pid,
        "started": "2026-09-08T21:58:23-0400",
        "what": "greedy_mtp_ab.sh (#151/#39)",
    }


# --- the drift the two hardcoded paths invite --------------------------------


def test_the_two_lock_paths_agree() -> None:
    """The hook is standard-library only on purpose: `language: python` builds
    a hermetic env so `uv run pre-commit run` and a bare `git commit` invoke
    the same interpreter, and the guard cannot be present under one and absent
    under the other. That means it cannot import preflight, so LOCK_PATH is
    written down twice -- and two copies drift into a guard that reads a file
    nobody writes."""
    import preflight

    assert refuse.LOCK_PATH == preflight.LOCK_PATH


def test_the_hook_imports_nothing_outside_the_standard_library() -> None:
    """If it grows a third-party import, the hermetic environment stops
    building and the guard silently disappears from a bare `git commit`."""
    source = HOOK.read_text()
    for forbidden in ("import yaml", "import pytest", "import requests", "import uv"):
        assert forbidden not in source
    assert "import preflight" not in source, "see test_the_two_lock_paths_agree"


# --- what each lock state does -----------------------------------------------


def test_a_held_lock_refuses_the_commit(tmp_path) -> None:
    state, why = refuse.lock_state(
        refuse.read_lock(lock_file(tmp_path, held_by(os.getpid()))), HOSTNAME
    )
    assert state == refuse.HELD
    assert "greedy_mtp_ab.sh (#151/#39)" in why, "the reader needs to know WHICH run"
    assert "2026-09-08T21:58:23-0400" in why, "and since when"


def test_no_lock_allows_the_commit(tmp_path) -> None:
    assert (
        refuse.lock_state(refuse.read_lock(tmp_path / "absent.json"), HOSTNAME)[0]
        == refuse.FREE
    )


def test_a_dead_pid_is_stale_and_allows_the_commit(tmp_path) -> None:
    """A lock outliving its process must not block commits forever. This is
    the failure that had seven waiter shells blocking every commit for hours
    while the machine was idle -- in that shape, a guard that cannot be
    satisfied gets overridden, and then it is not a guard."""
    dead = _a_pid_that_is_gone()
    state, _ = refuse.lock_state(
        refuse.read_lock(lock_file(tmp_path, held_by(dead))), HOSTNAME
    )
    assert state == refuse.STALE


def test_another_machines_lock_allows_the_commit(tmp_path) -> None:
    """A lock is a claim on one machine. Its pid means nothing here, and
    refusing on it would block this machine on another's run."""
    payload = held_by(os.getpid(), host="some-other-host")
    state, _ = refuse.lock_state(
        refuse.read_lock(lock_file(tmp_path, payload)), HOSTNAME
    )
    assert state == refuse.FOREIGN


@pytest.mark.parametrize(
    "payload",
    ["{ not json", "[]", '"a string"', json.dumps({"hostname": HOSTNAME})],
    ids=["unparseable", "a list", "a string", "no pid"],
)
def test_an_unusable_lock_is_corrupt(tmp_path, payload) -> None:
    """An unparseable lock is not evidence that nobody is running -- it is
    evidence that something went wrong while claiming the machine, which is
    exactly when a commit must not land. `preflight.read_lock` says the same,
    and the two disagreeing would be worse than either."""
    lock = refuse.read_lock(lock_file(tmp_path, payload))
    assert refuse.lock_state(lock, HOSTNAME)[0] == refuse.CORRUPT


def _a_pid_that_is_gone() -> int:
    """A pid that has certainly exited: spawn `true` and reap it."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


# --- the exit codes pre-commit acts on ---------------------------------------


def test_main_refuses_during_a_live_run(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN", raising=False)
    monkeypatch.setattr(refuse, "LOCK_PATH", lock_file(tmp_path, held_by(os.getpid())))
    monkeypatch.setattr(refuse.platform, "node", lambda: HOSTNAME)
    assert refuse.main() == 1


def test_main_allows_a_commit_on_an_idle_machine(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN", raising=False)
    monkeypatch.setattr(refuse, "LOCK_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(refuse.platform, "node", lambda: HOSTNAME)
    assert refuse.main() == 0


def test_the_override_still_works_during_a_live_run(monkeypatch, tmp_path) -> None:
    """A deliberate commit during a run is sometimes right; it must be
    possible, and it must be explicit."""
    monkeypatch.setenv("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN", "1")
    monkeypatch.setattr(refuse, "LOCK_PATH", lock_file(tmp_path, held_by(os.getpid())))
    monkeypatch.setattr(refuse.platform, "node", lambda: HOSTNAME)
    assert refuse.main() == 0


def test_the_refusal_names_the_run_and_the_stake(monkeypatch, tmp_path, caplog) -> None:
    """The message must say WHICH run, or the reader cannot tell whether to
    wait five minutes or three hours -- a bare script name never did.

    caplog, not capsys: `logging.basicConfig` is a no-op once any other test
    has configured the root logger, so capsys sees an empty string and the
    test passes or fails for a reason unrelated to the guard. That the output
    reaches stdout in a real process is asserted by
    `test_the_hook_runs_end_to_end_as_pre_commit_invokes_it`, which runs the
    hook the way pre-commit does.
    """
    monkeypatch.delenv("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN", raising=False)
    monkeypatch.setattr(refuse, "LOCK_PATH", lock_file(tmp_path, held_by(os.getpid())))
    monkeypatch.setattr(refuse.platform, "node", lambda: HOSTNAME)
    with caplog.at_level("ERROR", logger=refuse.logger.name):
        refuse.main()
    out = caplog.text
    assert "greedy_mtp_ab.sh (#151/#39)" in out
    assert "HARNESS_HEAD" in out


# --- what the rewrite must have deleted --------------------------------------


def test_the_guard_no_longer_looks_for_processes_by_name() -> None:
    """2026-09-08, three failures from one predicate:

    - Only `stack_agent_ab.sh` was listed, so eleven of the twelve lock-holding
      drivers were uncovered and a logs-only commit killed 7 of 8 sweeps.
    - Widened, it matched seven orphaned `until ! pgrep -f '<driver>.sh'`
      waiter shells and refused every commit while the machine was idle.
    - `pgrep -a` is GNU-only and GNU `pgrep -l` truncates the process name to
      15 characters, so `stack_agent_ab.sh` arrived as `stack_agent_ab.` and
      CI went red.

    None of those is possible against a recorded pid.
    """
    assert "pgrep" not in code_of(HOOK)
    assert "pkill" not in code_of(HOOK)
    assert "_PATTERNS" not in code_of(HOOK)
    # The prose still discusses pgrep -- explaining why it is gone is the
    # point of the docstring. Asserting on the raw text would forbid the
    # explanation, which is the same word-for-a-metric confusion this
    # module's history is full of.
    assert "pgrep" in HOOK.read_text(), "the docstring should still explain why"


# --- the wiring, unchanged ---------------------------------------------------


def test_the_config_declares_a_language_python_hook_first() -> None:
    """`language: python` (not `system`) and first in the list are the point:
    a bare `git commit` must invoke the same hermetic env a `pre-commit run`
    does, and nothing reformats a file before this guard runs."""
    data = yaml.safe_load(CONFIG.read_text())
    hooks = data["repos"][0]["hooks"]
    assert hooks[0]["id"] == "refuse-commit-during-benchmark"
    assert hooks[0]["language"] == "python"
    assert hooks[0]["pass_filenames"] is False
    assert hooks[0]["always_run"] is True
    assert hooks[0]["stages"] == ["pre-commit"]
    assert hooks[0]["entry"].startswith("python ")
    assert hooks[0]["entry"].endswith("scripts/refuse_commit_during_benchmark.py")


def test_the_pre_commit_hook_is_installed_into_the_repo() -> None:
    """#227: a config file alone is the 'skipping test' the repo rejects -- the
    guard reads as covered while a clone that never ran `pre-commit install`
    commits straight past it."""
    git_hook = ROOT / ".git" / "hooks" / "pre-commit"
    assert git_hook.exists(), (
        "pre-commit stage hook missing; run `uv run pre-commit install`"
    )
    assert os.access(git_hook, os.X_OK), git_hook
    assert "pre-commit" in git_hook.read_text()


def test_the_hook_runs_end_to_end_as_pre_commit_invokes_it(tmp_path) -> None:
    """The entry point pre-commit actually calls, in a fresh process, with a
    lock it can see. Everything above monkeypatches; this does not."""
    lock = lock_file(tmp_path, held_by(os.getpid()))
    script = (
        "import sys, pathlib, platform;"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r});"
        "import refuse_commit_during_benchmark as r;"
        f"r.LOCK_PATH = pathlib.Path({str(lock)!r});"
        f"platform.node = lambda: {HOSTNAME!r};"
        "sys.exit(r.main())"
    )
    env = dict(os.environ)
    env.pop("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN", None)
    done = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert done.returncode == 1
    assert "greedy_mtp_ab.sh" in done.stdout
