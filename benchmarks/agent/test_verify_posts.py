"""Parsing for X post verification. No network."""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import verify_posts as vp


def test_ids_are_scraped_and_deduped():
    text = (
        "https://x.com/googlegemma/status/2094817003806806172?s=46 and "
        "2094817003806806172 and 12345"
    )
    assert vp.ids_from(text) == ["2094817003806806172"]
    assert vp.ids_from("") == []


def test_fxtwitter_keeps_the_quoted_post():
    """The substance often lives in the quote.

    The Gemma 4 "2x faster on a Mac" claim reached us as a quoted post, not as
    the post we were handed. Dropping quotes would lose the finding.
    """
    got = vp._from_fxtwitter(
        {
            "tweet": {
                "author": {"screen_name": "kydo"},
                "created_at": "Tue Sep 01 17:49:41 +0000 2026",
                "text": "look at this",
                "quote": {
                    "author": {"screen_name": "googlegemma"},
                    "text": "Gemma 4 26B A4B on a Mac just got faster",
                },
            }
        }
    )
    assert got["author"] == "kydo"
    assert got["quoted_author"] == "googlegemma"
    assert "Gemma 4" in got["quoted_text"]


def test_a_reply_is_labelled_a_reply():
    """grok has reported a reply as a post; the label is part of the check."""
    got = vp._from_fxtwitter(
        {"tweet": {"author": {"screen_name": "a"}, "replying_to": "b", "text": "x"}}
    )
    assert got["is_reply"] is True


def test_syndication_shape_is_understood():
    got = vp._from_syndication(
        {
            "__typename": "Tweet",
            "user": {"screen_name": "a"},
            "created_at": "2026-09-01T15:57:48.000Z",
            "text": "hello",
        }
    )
    assert got["author"] == "a"
    assert got["is_reply"] is False


def test_a_non_tweet_payload_is_rejected():
    """A mirror can answer 200 with something that is not a post."""
    assert vp._from_syndication({"__typename": "TweetTombstone"}) is None
    assert vp._from_fxtwitter({"code": 404}) is None
