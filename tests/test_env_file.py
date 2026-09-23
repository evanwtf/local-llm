"""A .env value that vanishes when sourced is the failure this guards.

Twice on 2026-09-22 a multi-word value was written to a serving recipe's .env
without quotes. Bash assigned the first word and tried to execute the rest, so
the setting silently never reached the engine -- discovered only after a
ten-minute model load. These pin the round-trip, because the whole point of
the module is that writing is not enough: the value has to survive bash.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import env_file


def test_the_exact_value_that_broke_survives(tmp_path: pathlib.Path) -> None:
    """A JSON argument carrying its own single quotes.

    Written bare this became `EXTRA_VLLM_ARGS=--default-chat-template-kwargs`
    with the JSON run as a command.
    """
    p = tmp_path / ".env"
    value = '--default-chat-template-kwargs \'{"reasoning_effort":"low"}\''
    env_file.set_keys(p, [f"EXTRA_VLLM_ARGS={value}"])
    assert env_file.read_via_bash(p, "EXTRA_VLLM_ARGS") == value


def test_the_second_value_that_broke_survives(tmp_path: pathlib.Path) -> None:
    """Multi-word docker args. Written bare, everything after `--cap-add` was lost."""
    p = tmp_path / ".env"
    value = "--cap-add IPC_LOCK -e VLLM_USE_FLASHINFER_MOE_FP4=0"
    env_file.set_keys(p, [f"EXTRA_DOCKER_ARGS={value}"])
    assert env_file.read_via_bash(p, "EXTRA_DOCKER_ARGS") == value


def test_an_unquoted_value_written_by_hand_is_caught(tmp_path: pathlib.Path) -> None:
    """The failure mode itself, and it is not what it looks like.

    `VAR=value command args` is a PREFIX ASSIGNMENT: bash sets VAR only for
    that one command's environment, so after sourcing the variable does not
    exist at all. It is not left holding `--cap-add`, which is the intuitive
    guess and was written into this module's docstring until this test
    disagreed with it.
    """
    p = tmp_path / ".env"
    p.write_text("EXTRA_DOCKER_ARGS=--cap-add IPC_LOCK -e FOO=1\n")
    assert env_file.read_via_bash(p, "EXTRA_DOCKER_ARGS") is None


def test_set_replaces_rather_than_appends(tmp_path: pathlib.Path) -> None:
    """A second `set` must not leave two assignments; bash would take the last,
    which is the opposite of what a reader of the file would expect."""
    p = tmp_path / ".env"
    env_file.set_keys(p, ["GPU_MEMORY_UTILIZATION=0.835"])
    env_file.set_keys(p, ["GPU_MEMORY_UTILIZATION=0.78"])
    assert p.read_text().count("GPU_MEMORY_UTILIZATION=") == 1
    assert env_file.read_via_bash(p, "GPU_MEMORY_UTILIZATION") == "0.78"


def test_unset_and_empty_are_distinguishable(tmp_path: pathlib.Path) -> None:
    """`check` reports them differently: unset means a typo in the key, empty
    means the quoting ate the value. Conflating them hides which happened."""
    p = tmp_path / ".env"
    p.write_text("PRESENT=x\nEMPTY=\n")
    assert env_file.read_via_bash(p, "MISSING") is None
    assert env_file.read_via_bash(p, "EMPTY") == ""
    assert env_file.read_via_bash(p, "PRESENT") == "x"


def test_a_value_with_a_dollar_sign_is_not_expanded(tmp_path: pathlib.Path) -> None:
    """Single quoting keeps `$HOME` literal. An argument holding a shell
    variable would otherwise expand at source time and reach the engine as
    whatever the launcher's environment happened to hold."""
    p = tmp_path / ".env"
    env_file.set_keys(p, ["ARG=--path $HOME/models"])
    assert env_file.read_via_bash(p, "ARG") == "--path $HOME/models"


def test_a_bad_key_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError):
        env_file.parse_assignment("not an assignment")
    with pytest.raises(ValueError):
        env_file.parse_assignment("has-a-dash=1")
