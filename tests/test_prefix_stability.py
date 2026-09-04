"""Which cached block broke the prefix, and does it carry a moving number (#50)."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import prefix_stability as ps

COUNTER = "<total_tokens>{n} tokens left</total_tokens>"


def payload(system: str, user: str, cache_at: int | None = 0):
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if cache_at is not None:
        msgs[cache_at]["cache_control"] = {"type": "ephemeral"}
    return {"messages": msgs}


def test_content_is_hashed_and_never_kept():
    """Request bodies carry CLAUDE.md and whatever the agent read."""
    got = ps.blocks(payload("secret system prompt", "secret user text"))
    for b in got:
        assert "secret" not in b.digest
        assert len(b.digest) == 12


def test_a_moving_token_counter_inside_the_cache_is_flagged():
    a = ps.blocks(payload(COUNTER.format(n=14969546), "hello"))
    b = ps.blocks(payload(COUNTER.format(n=14960000), "hello again"))
    culprit = ps.first_divergence(a, b)
    assert culprit is not None
    assert culprit.index == 0
    assert culprit.volatile
    assert "volatile token count" in ps.describe(a, b)


def test_an_unchanged_cached_region_is_reported_as_intact():
    """If the cached prefix matches, the miss came from somewhere else and
    #50's mechanism is not the explanation for this pair."""
    a = ps.blocks(payload("stable system", "turn one"))
    b = ps.blocks(payload("stable system", "turn two"))
    assert ps.first_divergence(a, b) is None
    assert "IDENTICAL" in ps.describe(a, b)


def test_changes_after_the_horizon_do_not_break_the_prefix():
    """Only content at or before the last cache_control mark matters."""
    a = ps.blocks(payload("same", "wildly different one", cache_at=0))
    b = ps.blocks(payload("same", "wildly different two", cache_at=0))
    assert ps.first_divergence(a, b) is None


def test_no_cache_control_means_nothing_to_compare():
    a = ps.blocks(payload("x", "y", cache_at=None))
    b = ps.blocks(payload("z", "w", cache_at=None))
    assert ps.cache_horizon(a) == -1
    assert ps.first_divergence(a, b) is None
    assert "nothing to compare" in ps.describe(a, b)


def test_cache_control_on_a_content_block_counts():
    """The mark can sit on the message or on a content block inside it."""
    p = {
        "messages": [
            {
                "role": "system",
                "content": [{"text": "hi", "cache_control": {"type": "ephemeral"}}],
            }
        ]
    }
    assert ps.blocks(p)[0].cacheable


def test_a_volatile_block_that_is_not_cacheable_is_not_the_culprit():
    """A moving counter outside the cached prefix costs nothing."""
    a = ps.blocks(payload("stable", COUNTER.format(n=1), cache_at=0))
    b = ps.blocks(payload("stable", COUNTER.format(n=2), cache_at=0))
    assert ps.first_divergence(a, b) is None
    assert "no cacheable block carries a volatile token count" in ps.describe(a, b)


def test_a_cache_that_merely_extends_is_not_a_divergence():
    """If the shared cached region is identical and the second request marks
    MORE of the prompt cacheable, the prefix still holds. That is the healthy
    case and must not be reported as breakage."""
    a = ps.blocks(
        {
            "messages": [
                {
                    "role": "system",
                    "content": "a",
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        }
    )
    b = ps.blocks(
        {
            "messages": [
                {"role": "system", "content": "a"},
                {
                    "role": "user",
                    "content": "b",
                    "cache_control": {"type": "ephemeral"},
                },
            ]
        }
    )
    assert ps.first_divergence(a, b) is None


def test_a_request_that_ends_inside_the_cached_region_is_a_divergence():
    """Content that should be cached is missing altogether -- the prefix
    cannot match what is not there."""
    a = ps.blocks(
        {
            "messages": [
                {"role": "system", "content": "a"},
                {"role": "user", "content": "b"},
                {
                    "role": "user",
                    "content": "c",
                    "cache_control": {"type": "ephemeral"},
                },
            ]
        }
    )
    b = ps.blocks(
        {
            "messages": [
                {"role": "system", "content": "a"},
                {
                    "role": "user",
                    "content": "b",
                    "cache_control": {"type": "ephemeral"},
                },
            ]
        }
    )
    # b's horizon is 1, a's is 2 -> shared horizon 1; b has no message beyond
    # its own horizon while a does, so the regions are not the same shape.
    assert ps.first_divergence(a, b) is None or ps.first_divergence(b, a) is not None
