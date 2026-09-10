"""The `local-agent` launcher, ported from scripts/local-agent.sh (#235).

A launcher has no benchmark number to protect, but it has the equivalent: which
stack, which model file, which port, which context the user actually gets. A
transcription slip in the stack table is a silent "it started the wrong thing",
so the table is pinned value-by-value against the shell source. The rest is the
argument contract and the two refusals the shell was careful about: validate the
client before any download, and never start a second engine on a busy port.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import local_agent as la

# --- the stack table: pinned against scripts/local-agent.sh ------------------


def test_all_four_stacks_present() -> None:
    assert set(la.STACKS) == {"starter", "fast", "mainline", "lineage"}


def test_stack_table_values_match_the_shell() -> None:
    """Every value a user depends on, transcribed from local-agent.sh 64-112."""
    starter = la.STACKS["starter"]
    assert starter.engine == "ollama"
    assert starter.engine_port == 11434
    assert starter.ctx == 262144
    assert starter.ollama_tag == "qwen3.6:27b-coding-mxfp8"
    assert starter.opencode_model == "ollama/qwen3.6:27b-coding-mxfp8"
    assert starter.claude_port == 11500
    assert starter.claude_upstream == "http://127.0.0.1:11434"

    fast = la.STACKS["fast"]
    assert fast.engine == "ds4"
    assert fast.engine_port == 8000
    assert fast.shim_port == 8101
    assert fast.ctx == 100000
    assert fast.engine_tree.endswith("/git/ds4-ivan-qwen38fn")
    assert fast.engine_branch == "qwen3.8-flash-next"
    assert fast.model_file.endswith(
        "/qwen3.8-flash-next-ds4-q4k-imatrix/"
        "Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-"
        "Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
    )
    assert fast.ple_file.endswith("/Qwen3.8-Flash-Next-PLE-Q4_1.gguf")
    assert fast.hf_repo == "ivanfioravanti/Qwen3.8-Flash-Next-DS4-Q4"
    assert fast.opencode_baseurl == "http://127.0.0.1:8101/v1"
    assert fast.claude_port == 8101
    assert fast.claude_token == "dsv4-local"
    # The fast stack is served bare over the shim, so it needs no wire shim.
    assert fast.claude_upstream is None

    mainline = la.STACKS["mainline"]
    assert mainline.engine == "llamacpp"
    assert mainline.engine_port == 8020
    assert mainline.ctx == 131072
    assert mainline.hf_include == "UD-Q3_K_XL/*"
    assert mainline.claude_upstream == "http://127.0.0.1:8020"
    assert mainline.shim_port is None

    lineage = la.STACKS["lineage"]
    assert lineage.engine == "ds4"
    assert lineage.engine_port == 8000
    assert lineage.ctx == 100000
    assert lineage.model_file.endswith("-chat-v2-imatrix-fixed-0731.gguf")
    assert lineage.ple_file is None
    assert lineage.claude_upstream is None


def test_only_fast_carries_a_tool_shim() -> None:
    """The Qwen tool shim (#112) is the fast stack's, and only its."""
    shimmed = [name for name, s in la.STACKS.items() if s.shim_port is not None]
    assert shimmed == ["fast"]


# --- the argument contract ---------------------------------------------------


def test_stack_is_required() -> None:
    with pytest.raises(la.LaunchError):
        la.parse_invocation([])


def test_client_defaults_to_opencode() -> None:
    assert la.parse_invocation(["fast"]).client == "opencode"


def test_client_is_validated_before_the_stack() -> None:
    """A bad client with a bad stack reports the client -- the shell validates
    it first (lines 56-59) so a typo never costs a 105 GB download."""
    with pytest.raises(la.LaunchError, match="unknown client"):
        la.parse_invocation(["badstack", "badclient"])


def test_bad_stack_is_rejected() -> None:
    with pytest.raises(la.LaunchError, match="unknown stack"):
        la.parse_invocation(["badstack", "opencode"])


def test_a_bare_agent_arg_is_read_as_the_client() -> None:
    """local-agent.sh fast "task" reads "task" as the client and refuses it.
    Preserve the quirk: pass a client explicitly, or use it after two
    positionals."""
    with pytest.raises(la.LaunchError, match="unknown client"):
        la.parse_invocation(["fast", "task"])


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["fast"], []),
        (["fast", "claude"], []),
        (["fast", "claude", "a", "b"], ["a", "b"]),
        (["fast", "claude", "--", "a"], ["a"]),
        (["fast", "claude", "--check", "a"], ["a"]),
        (["starter", "opencode", "one", "--", "two"], ["one", "two"]),
    ],
)
def test_agent_args_passthrough(argv: list[str], expected: list[str]) -> None:
    assert la.parse_invocation(argv).agent_args == expected


def test_check_flag_is_detected_anywhere() -> None:
    assert la.parse_invocation(["fast", "claude", "--check"]).check_only is True
    assert la.parse_invocation(["fast", "claude"]).check_only is False


# --- confirm() ---------------------------------------------------------------


def test_confirm_auto_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_AGENT_YES", "1")
    assert la.confirm("download 105 GB?") is True


def test_confirm_reads_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_AGENT_YES", raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert la.confirm("go?") is True
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert la.confirm("go?") is False


# --- the two refusals --------------------------------------------------------


def test_check_only_fetches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """--check must return before ensure_weights, ensure_engine, start_server."""
    called: list[str] = []
    for name in ("ensure_weights", "ensure_engine", "start_server", "start_shims"):
        monkeypatch.setattr(la, name, lambda *a, _n=name, **k: called.append(_n))
    monkeypatch.setattr(la, "listening", lambda _port: False)
    assert la.main(["fast", "opencode", "--check"]) == 0
    assert called == []


def test_bad_client_returns_before_any_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(la, "ensure_weights", lambda *a, **k: called.append("fetch"))
    assert la.main(["fast", "badclient"]) == 2
    assert called == []


def test_server_not_started_twice_when_port_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listening port is reused, never overwritten with a second engine."""
    monkeypatch.setattr(la, "listening", lambda _port: True)

    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("unitctl.start must not run when the port is busy")

    monkeypatch.setattr(la.unitctl, "start", refuse)
    la.start_server(la.STACKS["fast"])  # must not raise


# --- the exact server / shim command lines (would this change the command?) --


def test_ds4_server_command_matches_the_shell() -> None:
    """local-agent.sh 196-198: ./ds4-server --metal -m M --ple P --ctx C
    --warm-weights --host 127.0.0.1 --port N."""
    fast = la.STACKS["fast"]
    cmd = la.server_command(fast)
    assert cmd[:4] == ["./ds4-server", "--metal", "-m", fast.model_file]
    assert cmd[4:6] == ["--ple", fast.ple_file]
    assert cmd[6:] == [
        "--ctx",
        "100000",
        "--warm-weights",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]


def test_ds4_server_command_omits_ple_when_absent() -> None:
    """The lineage stack has no PLE sidecar; --ple must not appear."""
    assert "--ple" not in la.server_command(la.STACKS["lineage"])


def test_llamacpp_server_command_matches_the_shell() -> None:
    """local-agent.sh 201-204: the sampler flags are part of the command."""
    cmd = la.server_command(la.STACKS["mainline"])
    assert cmd[0].endswith("/build/bin/llama-server")
    assert cmd[1:5] == [
        "-m",
        la.STACKS["mainline"].model_file,
        "-a",
        "qwen3.8-flash-next-q3",
    ]
    for flag in ("--temp", "1.0", "--top-p", "0.95", "--top-k", "20", "--min-p", "0.0"):
        assert flag in cmd
    assert cmd[cmd.index("-c") + 1] == "131072"


def test_ollama_server_command() -> None:
    assert la.server_command(la.STACKS["starter"]) == ["ollama", "serve"]


def test_shim_commands_match_the_shell() -> None:
    fast = la.STACKS["fast"]
    assert la.qwen_shim_command(fast) == [
        "uv",
        "run",
        "python",
        "ds4_qwen_tool_shim.py",
        "--upstream",
        "http://127.0.0.1:8000",
        "--port",
        "8101",
    ]
    starter = la.STACKS["starter"]
    assert la.claude_shim_command(starter) == [
        "uv",
        "run",
        "python",
        "ollama_claude_shim.py",
        "--port",
        "11500",
        "--upstream",
        "http://127.0.0.1:11434",
    ]


# --- the OpenCode provider declaration (#69) ---------------------------------


def test_declare_opencode_provider_creates_and_is_idempotent(
    tmp_path: pathlib.Path,
) -> None:
    cfg = tmp_path / "opencode.json"
    la.declare_opencode_provider(
        cfg, "ds4qwenshim/qwen3.8-flash-next-q4", "http://127.0.0.1:8101/v1"
    )
    data = json.loads(cfg.read_text())
    prov = data["provider"]["ds4qwenshim"]
    assert prov["npm"] == "@ai-sdk/openai-compatible"
    assert prov["options"]["baseURL"] == "http://127.0.0.1:8101/v1"
    assert "qwen3.8-flash-next-q4" in prov["models"]

    # A second call must not clobber an unrelated provider already present.
    data["provider"]["other"] = {"npm": "x", "models": {"m": {}}}
    cfg.write_text(json.dumps(data) + "\n")
    la.declare_opencode_provider(
        cfg, "ds4qwenshim/qwen3.8-flash-next-q4", "http://127.0.0.1:8101/v1"
    )
    data2 = json.loads(cfg.read_text())
    assert data2["provider"]["other"] == {"npm": "x", "models": {"m": {}}}
