"""#680 findings 5, 6 and 9: a two-node batch claims both Sparks and needs earlyoom.

Every test uses a fake ssh. No test reaches a real node.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from collections.abc import Sequence

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import node_claims

TARGETS = [("head", "head.example"), ("worker", "worker.example")]
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


class FakeSsh:
    """Records each call; `fail` maps (host, action) to (status, output)."""

    def __init__(self, fail: dict[tuple[str, str], tuple[int, str]] | None = None):
        self.fail = fail or {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> tuple[int, str]:
        host, command = argv[-2], argv[-1]
        action = next(
            (a for a in ("acquire", "release", "earlyoom", "run-lock") if a in command),
            "?",
        )
        self.calls.append((host, action))
        if (host, action) in self.fail:
            return self.fail[(host, action)]
        return 0, "active" if action == "earlyoom" else "ok"


@pytest.fixture(autouse=True)
def identified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_AGENT", "claude")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "opus")
    monkeypatch.setenv("LOCAL_LLM_EFFORT", "medium")


def test_only_facts_with_two_nodes_are_two_node() -> None:
    assert node_claims.is_two_node({"env": {"cluster": {"nodes": 2, "peer": {}}}})
    assert not node_claims.is_two_node({})
    assert not node_claims.is_two_node({"env": {}})
    assert not node_claims.is_two_node({"env": {"cluster": {"nodes": 1}}})


def test_node_names_come_from_config(tmp_path: pathlib.Path) -> None:
    env = {"LOCAL_LLM_DGX_HEAD": "h.example", "LOCAL_LLM_DGX_WORKER": "w.example"}
    got = node_claims.nodes(env=env, path=tmp_path / "absent.toml")
    assert got == [("head", "h.example"), ("worker", "w.example")]


def test_an_unconfigured_node_is_a_claim_error(tmp_path: pathlib.Path) -> None:
    env = {"LOCAL_LLM_DGX_HEAD": "h.example"}
    with pytest.raises(node_claims.ClaimError, match="LOCAL_LLM_DGX_WORKER"):
        node_claims.nodes(env=env, path=tmp_path / "absent.toml")


def test_the_acquire_command_carries_the_identity_and_the_intent() -> None:
    seen: list[list[str]] = []

    def run(argv: Sequence[str], timeout: float) -> tuple[int, str]:
        seen.append(list(argv))
        return 0, ""

    with node_claims.held(TARGETS[:1], "two-node batch b (#680)", run):
        pass
    assert seen[0][:-1] == [*SSH, "head.example"]
    token = re.search(r"\[batch ([0-9a-f]{12})\]", seen[0][-1])
    assert token is not None
    # The whole remote command, uv fallback included: a non-interactive ssh
    # shell on a Spark has no ~/.local/bin on PATH (2026-11-07).
    assert seen[0][-1] == (
        'UV="$(command -v uv || echo "$HOME/.local/bin/uv")"; '
        '[ -x "$UV" ] || { echo "uv not found: not on PATH and not at '
        '$HOME/.local/bin/uv" >&2; exit 127; }; '
        "cd ~/git/local-llm && LOCAL_LLM_AGENT=claude LOCAL_LLM_MODEL=opus "
        'LOCAL_LLM_EFFORT=medium "$UV" run python scripts/machine_claim.py '
        f"acquire --what 'two-node batch b (#680) [batch {token.group(1)}]'"
    )
    assert seen[1][-1].endswith("scripts/machine_claim.py release")


def test_both_claims_are_taken_before_the_body_and_released_after() -> None:
    ssh = FakeSsh()
    inside: list[list[tuple[str, str]]] = []
    with node_claims.held(TARGETS, "batch", ssh):
        inside.append(list(ssh.calls))
    assert inside == [[("head.example", "acquire"), ("worker.example", "acquire")]]
    assert ssh.calls[2:] == [
        ("worker.example", "release"),
        ("head.example", "release"),
    ]


def test_a_failed_worker_claim_refuses_and_releases_the_head() -> None:
    ssh = FakeSsh({("worker.example", "acquire"): (1, "machine busy")})
    ran = []
    with (
        pytest.raises(node_claims.ClaimError, match="worker.*machine busy"),
        node_claims.held(TARGETS, "batch", ssh),
    ):
        ran.append(True)
    assert ran == []
    assert ssh.calls == [
        ("head.example", "acquire"),
        ("worker.example", "acquire"),
        ("worker.example", "run-lock"),
        ("head.example", "release"),
    ]


def test_a_failed_head_claim_touches_nothing_else() -> None:
    ssh = FakeSsh({("head.example", "acquire"): (255, "ssh: timed out")})
    with (
        pytest.raises(node_claims.ClaimError, match="head"),
        node_claims.held(TARGETS, "batch", ssh),
    ):
        pass
    assert ssh.calls == [("head.example", "acquire"), ("head.example", "run-lock")]


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt()])
def test_both_claims_are_released_when_the_batch_dies(exc: BaseException) -> None:
    ssh = FakeSsh()
    with pytest.raises(type(exc)), node_claims.held(TARGETS, "batch", ssh):
        raise exc
    assert ssh.calls[-2:] == [
        ("worker.example", "release"),
        ("head.example", "release"),
    ]


def test_a_failed_release_is_logged_and_the_other_still_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ssh = FakeSsh({("worker.example", "release"): (1, "not ours")})
    with node_claims.held(TARGETS, "batch", ssh):
        pass
    assert ("head.example", "release") in ssh.calls
    assert "could NOT release the claim on the worker" in caplog.text


def test_an_unidentified_client_claims_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCAL_LLM_AGENT")
    ssh = FakeSsh()
    with (
        pytest.raises(node_claims.ClaimError, match="LOCAL_LLM_AGENT"),
        node_claims.held(TARGETS, "batch", ssh),
    ):
        pass
    assert ssh.calls == []


def test_earlyoom_active_on_both_nodes_passes() -> None:
    ssh = FakeSsh()
    assert node_claims.check_earlyoom(TARGETS, ssh) is None
    assert ssh.calls == [("head.example", "earlyoom"), ("worker.example", "earlyoom")]


def test_earlyoom_down_on_the_worker_is_refused() -> None:
    ssh = FakeSsh({("worker.example", "earlyoom"): (3, "inactive")})
    why = node_claims.check_earlyoom(TARGETS, ssh)
    assert why is not None
    assert "worker (inactive)" in why
    assert "head" not in why.split("on the ")[1].split(".")[0]
    assert "setup_earlyoom.py --apply" in why


def test_an_unreachable_node_fails_the_earlyoom_check() -> None:
    ssh = FakeSsh({("head.example", "earlyoom"): (255, "")})
    why = node_claims.check_earlyoom(TARGETS, ssh)
    assert why is not None and "head (ssh exit 255)" in why


# Codex review: an ssh that ends without an answer may still have written the
# claim, and a session claim never expires. The batch's own token decides.
def _lock_echo(what_of: dict[str, tuple[int, str]]):
    """A fake ssh whose `cat` of the lock returns the claim it was given."""
    calls: list[tuple[str, str]] = []
    whats: list[str] = []

    def run(argv: Sequence[str], timeout: float) -> tuple[int, str]:
        host, command = argv[-2], argv[-1]
        if "acquire" in command:
            calls.append((host, "acquire"))
            whats.append(command.split("--what ", 1)[1])
            return what_of.get(host, (0, "ok"))
        if "run-lock" in command:
            calls.append((host, "read"))
            mine = whats[-1].strip("'")
            return 0, json.dumps({"what": mine if host == "worker.example" else "x"})
        calls.append((host, "release"))
        return 0, "ok"

    return run, calls


def test_a_timed_out_acquire_that_wrote_the_claim_is_released() -> None:
    run, calls = _lock_echo({"worker.example": (255, "timed out")})
    with (
        pytest.raises(node_claims.ClaimError),
        node_claims.held(TARGETS, "batch", run),
    ):
        pass
    assert calls == [
        ("head.example", "acquire"),
        ("worker.example", "acquire"),
        ("worker.example", "read"),
        ("worker.example", "release"),
        ("head.example", "release"),
    ]


def test_a_refused_acquire_never_releases_the_holder() -> None:
    """The head holds somebody else's claim: read it, leave it alone."""
    run, calls = _lock_echo({"head.example": (1, "machine busy")})
    with (
        pytest.raises(node_claims.ClaimError),
        node_claims.held(TARGETS, "batch", run),
    ):
        pass
    assert calls == [("head.example", "acquire"), ("head.example", "read")]


def test_a_signal_during_the_acquire_still_reconciles() -> None:
    calls: list[tuple[str, str]] = []
    whats: list[str] = []

    def run(argv: Sequence[str], timeout: float) -> tuple[int, str]:
        host, command = argv[-2], argv[-1]
        if "acquire" in command:
            calls.append((host, "acquire"))
            whats.append(command.split("--what ", 1)[1].strip("'"))
            raise SystemExit(143)
        if "run-lock" in command:
            calls.append((host, "read"))
            return 0, json.dumps({"what": whats[-1]})
        calls.append((host, "release"))
        return 0, "ok"

    with pytest.raises(SystemExit), node_claims.held(TARGETS, "batch", run):
        pass
    assert calls == [
        ("head.example", "acquire"),
        ("head.example", "read"),
        ("head.example", "release"),
    ]


def test_a_node_without_uv_is_named_in_the_refusal() -> None:
    ssh = FakeSsh(
        {
            ("worker.example", "acquire"): (
                127,
                "uv not found: not on PATH and not at /home/x/.local/bin/uv",
            )
        }
    )
    with (
        pytest.raises(node_claims.ClaimError, match=r"worker \(exit 127\).*uv not"),
        node_claims.held(TARGETS, "batch", ssh),
    ):
        pass
