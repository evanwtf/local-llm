"""`scripts/local-agent.sh` must match what RECOMMENDATIONS.md tells people to run.

The script is a convenience wrapper, so its whole value is that a reader can
copy a line out of the recommendations and have it work. That breaks silently
the moment either side is edited alone, which is what these tests are for.

The execution tests use `--check`, which stops before any download, build or
server start. A test that started a 105 GB download would be worse than no
test.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "local-agent.sh"
DOC = ROOT / "RECOMMENDATIONS.md"

#: The stacks the script implements, one per row of the top-three table plus
#: the mainline fallback named in the slot-2 note.
STACKS = ("starter", "fast", "mainline", "lineage")
CLIENTS = ("opencode", "claude")

zsh = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, cwd=ROOT, timeout=120
    )


# --- static: the script and the doc must agree ------------------------------


def test_the_script_is_executable() -> None:
    assert SCRIPT.exists(), "scripts/local-agent.sh is missing"
    assert SCRIPT.stat().st_mode & 0o111, "scripts/local-agent.sh is not executable"


def test_every_stack_has_a_case_arm() -> None:
    body = SCRIPT.read_text()
    for stack in STACKS:
        assert re.search(rf"^\s*{stack}\)", body, re.M), f"no case arm for '{stack}'"


def test_the_doc_documents_every_stack_the_script_implements() -> None:
    """Drift guard. A stack the script supports and the doc never names is a
    stack nobody will run; the reverse is a copy-paste line that fails."""
    doc = DOC.read_text()
    for stack in STACKS:
        assert f"local-agent.sh {stack}" in doc, (
            f"RECOMMENDATIONS.md never shows `local-agent.sh {stack}`"
        )


def test_the_doc_names_no_stack_the_script_does_not_implement() -> None:
    doc = DOC.read_text()
    for found in set(re.findall(r"local-agent\.sh\s+([a-z]+)", doc)):
        assert found in STACKS, (
            f"the doc shows stack '{found}', which the script has no arm for"
        )


def test_both_clients_are_reachable_from_the_doc() -> None:
    doc = DOC.read_text()
    for client in CLIENTS:
        assert client in doc, f"RECOMMENDATIONS.md never mentions the '{client}' client"


def test_the_client_is_validated_before_any_side_effect() -> None:
    """A typo'd client must not cost a 105 GB download first.

    The validation has to sit above the weights block; this asserts on order,
    because that ordering is the whole point and it is easy to undo.
    """
    body = SCRIPT.read_text()
    guard = body.index("unknown client")
    weights = body.index("# --- 1. weights")
    assert guard < weights, "client validation moved below the download step"


def test_it_never_starts_a_second_engine_on_a_busy_port() -> None:
    body = SCRIPT.read_text()
    assert "already listening on :$ENGINE_PORT" in body, (
        "the reuse-don't-restart guard is gone; two resident models will not fit"
    )


def test_large_downloads_are_confirmed() -> None:
    body = SCRIPT.read_text()
    assert body.count("confirm ") >= 3, "downloads/builds are no longer confirmed"
    assert "LOCAL_AGENT_YES" in body, "the non-interactive override is gone"


# --- execution: --check only, no side effects -------------------------------


@zsh
@pytest.mark.parametrize("stack", STACKS)
def test_check_mode_succeeds_for_every_stack(stack: str) -> None:
    got = run(stack, "opencode", "--check")
    assert got.returncode == 0, got.stderr
    assert "stopping before any download" in got.stdout


@zsh
def test_an_unknown_stack_is_refused() -> None:
    got = run("nope")
    assert got.returncode != 0
    assert "unknown stack" in got.stderr


@zsh
def test_an_unknown_client_is_refused() -> None:
    got = run("fast", "claud")
    assert got.returncode != 0
    assert "unknown client" in got.stderr


@zsh
def test_no_arguments_prints_usage() -> None:
    got = run()
    assert got.returncode != 0
    assert "usage:" in got.stderr
