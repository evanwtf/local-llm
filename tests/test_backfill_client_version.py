"""Backfill only what is derivable; never invent a measurement (#131)."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import backfill_client_version as bf


def test_derives_from_env_by_client_name():
    row = {"client": "opencode", "env": {"opencode": "1.18.27", "codex": "0.152.0"}}
    assert bf.derive(row) == "1.18.27"


def test_a_client_absent_from_env_is_unknowable():
    """Not a guess, not a nearest neighbour, not an inference from time."""
    assert bf.derive({"client": "aider", "env": {"opencode": "1.18.27"}}) is None


def test_a_row_with_no_env_is_unknowable():
    assert bf.derive({"client": "opencode"}) is None


def test_a_blank_version_is_unknowable():
    assert bf.derive({"client": "opencode", "env": {"opencode": "  "}}) is None


def test_an_existing_value_is_never_overwritten():
    lines = [
        json.dumps(
            {
                "client": "opencode",
                "client_version": "1.0.0",
                "env": {"opencode": "9.9.9"},
            }
        )
    ]
    out, counts = bf.plan(lines)
    assert counts == {"filled": 0, "already": 1, "unknowable": 0, "unparsed": 0}
    assert json.loads(out[0])["client_version"] == "1.0.0"


def test_unparseable_lines_pass_through_untouched():
    """A results file is append-only evidence. A backfill must not be able to
    drop a row it failed to parse."""
    lines = ["not json at all", json.dumps({"client": "x", "env": {"x": "1"}})]
    out, counts = bf.plan(lines)
    assert out[0] == "not json at all"
    assert counts["unparsed"] == 1
    assert counts["filled"] == 1


def test_blank_lines_survive():
    out, _ = bf.plan(["", json.dumps({"client": "x", "env": {"x": "1"}}), ""])
    assert out[0] == "" and out[-1] == ""


def test_nothing_but_client_version_is_added():
    """The one field. Any other change would rewrite evidence."""
    original = {
        "client": "opencode",
        "passed": True,
        "trial": 2,
        "env": {"opencode": "1.18.27"},
    }
    out, _ = bf.plan([json.dumps(original)])
    got = json.loads(out[0])
    assert got.pop("client_version") == "1.18.27"
    assert got == original
