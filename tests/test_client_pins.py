"""A pinned client that drifted must refuse, not warn (#131)."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import client_pins

REPO = pathlib.Path(__file__).resolve().parents[1]


def write(tmp_path, body):
    p = tmp_path / "client-pins.toml"
    p.write_text(body)
    return p


def test_the_repo_ships_pins_for_the_clients_it_drives():
    pins = client_pins.load_pins()
    assert "opencode" in pins, "OpenCode is the client #104 measured drifting"
    assert pins["opencode"]


def test_an_empty_string_is_not_a_pin(tmp_path):
    """ "" means present but deliberately unpinned; it must not compare."""
    pins = client_pins.load_pins(write(tmp_path, '[clients]\naider = ""\nx = "1.0"\n'))
    assert pins == {"x": "1.0"}


def test_a_missing_file_pins_nothing(tmp_path):
    assert client_pins.load_pins(tmp_path / "absent.toml") == {}


def test_malformed_toml_pins_nothing_rather_than_crashing(tmp_path):
    assert client_pins.load_pins(write(tmp_path, "[clients\nbroken")) == {}


def test_a_matching_version_is_not_drift():
    assert client_pins.drift({"opencode": "1.18.27"}, {"opencode": "1.18.27"}) == []


def test_tool_decoration_does_not_count_as_drift():
    """The tools disagree on format; the pin file should not encode each one."""
    assert client_pins.drift({"codex": "codex-cli 0.152.0"}, {"codex": "0.152.0"}) == []
    assert (
        client_pins.drift({"ollama": "ollama version is 0.33.3"}, {"ollama": "0.33.3"})
        == []
    )


def test_a_different_version_is_drift():
    got = client_pins.drift({"opencode": "1.18.26"}, {"opencode": "1.18.27"})
    assert got == [("opencode", "1.18.27", "1.18.26")]


def test_a_pinned_client_that_is_absent_is_drift_not_a_skip():
    """The pin says this machine should have it; absence is drift too."""
    got = client_pins.drift({}, {"opencode": "1.18.27"})
    assert got == [("opencode", "1.18.27", "not found")]


def test_preflight_refuses_on_drift_and_offers_one_override():
    """Warning is what every other version check does. This one must not."""
    text = (REPO / "benchmarks" / "agent" / "preflight.py").read_text()
    assert "refusing to run with a drifted client" in text
    assert "--allow-client-drift" in text
    assert "check_client_pins()" in text


def test_the_pin_file_says_not_to_pool_across_a_move():
    """A moved pin starts a new series; the file must say so where it is edited."""
    text = (REPO / "client-pins.toml").read_text()
    assert "separate series" in text
    assert "Do not pool" in text
