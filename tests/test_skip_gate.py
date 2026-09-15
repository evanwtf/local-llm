"""#223: CI fails on a skip whose reason is not on the committed allowlist.

A skipping test is not a passing test. Three ledger guards skipped on every
machine for weeks because a path constant went stale, and CI printed only a
count. The gate names each unexpected skip and fails the session, but only
when LOCAL_LLM_STRICT_SKIPS=1, so a developer machine with fewer checkouts
still runs the suite normally.
"""

from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_root_conftest():
    """Load the ROOT conftest by path; see test_machine_busy_guard.py for why."""
    spec = importlib.util.spec_from_file_location(
        "local_llm_root_conftest_skips", ROOT / "conftest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_root_conftest()

#: Every reason the Linux CI runner printed with -rs on 2026-09-15 (#417).
CI_REASONS_2026_09_15 = [
    (
        "no measured trials for this machine at /home/github-runner/actions-runner/"
        "_work/local-llm/local-llm/hardware/Ryzen7-PRO-8845HS-32GB-Phoenix3/"
        "results.jsonl; these tests read measured data and there is none here "
        "(a dry-run-only ledger does not count)"
    ),
    "one client version measured everything; nothing to caveat",
    "results.jsonl not present",
    "/home/github-runner/git/gmail-archive not checked out",
    "/home/github-runner/git/monitor not checked out",
    "script task: nothing is excised from a repo",
    "script task: the prompt names no repository file",
    "IOKit thermal sensors are macOS-only; there is no Linux equivalent to read",
    "gmail-archive at the pinned commit, and uv",
    "no ledger for this machine",
    "no ds4 checkout at ~/git/ds4-main",
    "no ds4 tree here",
    "no ds4 tree checked out",
    "this machine (Ryzen7-PRO-8845HS-32GB-Phoenix3) is not one we manage",
]


def test_every_skip_ci_prints_today_is_expected():
    assert guard.unexpected_skips(CI_REASONS_2026_09_15) == []


def test_a_new_skip_reason_is_unexpected():
    assert guard.unexpected_skips(["results path moved; nothing to read"]) == [
        "results path moved; nothing to read"
    ]


def test_the_match_is_on_the_whole_reason_not_a_fragment():
    """A longer reason that merely contains an allowed one is still new."""
    reason = "no ds4 tree here, and the ledger guard is now inert"
    assert guard.unexpected_skips([reason]) == [reason]


def test_the_pytest_skipped_prefix_is_ignored():
    assert guard.unexpected_skips(["Skipped: no ds4 tree here"]) == []


def test_the_gate_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv(guard.STRICT_SKIPS_ENV, raising=False)
    assert not guard.strict_skips_enabled()
    monkeypatch.setenv(guard.STRICT_SKIPS_ENV, "1")
    assert guard.strict_skips_enabled()
