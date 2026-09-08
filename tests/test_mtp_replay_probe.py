"""The #151 replay probe: `scripts/mtp_replay_probe.py`.

Eight synthetic cells engaged MTP; fifteen real agent trials emitted not one
speculative cycle. The discriminator is something a hand-built request does
not have, so this script stops guessing axes and replays the real payload,
then removes one property at a time.

The HTTP needs a 79 GB model resident. What is tested is the ablation
algebra, which is where a wrong answer would be quiet: an ablation that
silently changed nothing, or two that masked each other, would produce a
table of plausible cells and no signal.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mtp_replay_probe as rp


def payload():
    return {
        "model": "m",
        "stream": True,
        "tool_choice": "auto",
        "messages": [
            {"role": "system", "content": "you are an agent"},
            {"role": "user", "content": "fix the parser"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "file contents"},
            {"role": "user", "content": "continue"},
        ],
        "tools": [{"function": {"name": "read"}}, {"function": {"name": "write"}}],
    }


def test_verbatim_is_the_payload_unchanged():
    assert rp.ablate(payload(), "verbatim") == payload()


def test_each_ablation_removes_exactly_one_property():
    """Always from the original, never cumulatively -- two ablations applied
    in sequence can mask each other, and the table would not say so."""
    base = payload()
    assert "tools" not in rp.ablate(base, "no-tools")
    assert len(rp.ablate(base, "one-tool")["tools"]) == 1
    assert not [
        m for m in rp.ablate(base, "no-system")["messages"] if m["role"] == "system"
    ]
    assert not [
        m for m in rp.ablate(base, "no-tool-results")["messages"] if m["role"] == "tool"
    ]
    # And none of them touched anything else.
    for name in ("no-tools", "one-tool", "no-system", "no-tool-results"):
        got = rp.ablate(base, name)
        assert got["model"] == "m", name
    assert base == payload(), "the source payload was mutated"


def test_no_tools_removes_tool_choice_with_it():
    """`tool_choice` without `tools` is a request no client sends, and the
    engine's handling of it would be a fourth thing being varied."""
    got = rp.ablate(payload(), "no-tools")
    assert "tool_choice" not in got


def test_last_message_only_keeps_the_last_user_turn():
    got = rp.ablate(payload(), "last-message-only")
    assert got["messages"] == [{"role": "user", "content": "continue"}]


def test_an_ablation_with_nothing_to_remove_is_none_not_a_duplicate_arm():
    """A no-op ablation produces a cell identical to verbatim, which reads as
    a result. Returning None makes the script report it as skipped."""
    bare = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    assert rp.ablate(bare, "no-tools") is None
    assert rp.ablate(bare, "one-tool") is None
    assert rp.ablate(bare, "no-system") is None
    assert rp.ablate(bare, "no-tool-results") is None
    assert rp.ablate(bare, "last-message-only") is None
    # verbatim always runs; it is the baseline.
    assert rp.ablate(bare, "verbatim") == bare


def test_one_tool_is_skipped_when_there_is_already_one():
    single = dict(payload(), tools=[{"function": {"name": "read"}}])
    assert rp.ablate(single, "one-tool") is None


def test_an_unknown_ablation_is_refused_rather_than_ignored():
    with pytest.raises(ValueError):
        rp.ablate(payload(), "no-temperature")


def test_verbatim_runs_first_so_the_baseline_exists_before_the_ablations():
    """If the verbatim payload engages MTP, the request is not the cause and
    every ablation is measuring noise. That has to be known first."""
    assert rp.ABLATIONS[0] == "verbatim"


def test_describe_names_what_a_reader_needs_to_compare_two_captures():
    got = rp.describe(payload())
    assert "5 message(s)" in got
    assert "2 tool(s)" in got
    assert "'tool': 1" in got
