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


def test_the_env_var_disables_autoupdate(tmp_path):
    got, how = client_pins.opencode_autoupdate_disabled(
        env={"OPENCODE_DISABLE_AUTOUPDATE": "1"}, config=tmp_path / "absent.json"
    )
    assert got and "OPENCODE_DISABLE_AUTOUPDATE" in how


def test_the_config_key_disables_autoupdate(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text('{"autoupdate": false}')
    got, how = client_pins.opencode_autoupdate_disabled(env={}, config=cfg)
    assert got and "autoupdate" in how


def test_autoupdate_true_is_not_disabled(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text('{"autoupdate": true}')
    got, _ = client_pins.opencode_autoupdate_disabled(env={}, config=cfg)
    assert not got


def test_an_unreadable_config_is_not_read_as_disabled(tmp_path):
    """Absence of evidence is not the switch being off."""
    got, how = client_pins.opencode_autoupdate_disabled(
        env={}, config=tmp_path / "nope.json"
    )
    assert not got and "unreadable" in how


def test_preflight_reports_autoupdate_but_does_not_change_the_config():
    """A harness silently editing the tool under test is the class of thing
    #131 exists to prevent."""
    text = (REPO / "benchmarks" / "agent" / "preflight.py").read_text()
    assert "opencode_autoupdate_disabled" in text
    for forbidden in ("opencode.json", "write_text", "opencode upgrade"):
        assert forbidden not in text.split("def check_client_pins")[1].split("def ")[0]


def test_no_clients_installed_is_not_a_refusal(monkeypatch):
    """The case CI caught and the local suite could not: a machine that drives
    none of the pinned clients -- the Linux runner -- must still run preflight.

    An absent client cannot take a row, so it cannot corrupt a comparison.
    Refusing on absence made preflight refuse everywhere but this laptop.
    """
    import preflight

    monkeypatch.setattr(preflight.staleness, "installed_versions", lambda: {})
    assert preflight.check_client_pins() is False


def test_an_installed_but_wrong_version_is_still_a_refusal(monkeypatch):
    import preflight

    pins = client_pins.load_pins()
    wrong = {name: "0.0.1" for name in pins}
    monkeypatch.setattr(preflight.staleness, "installed_versions", lambda: wrong)
    assert preflight.check_client_pins() is True
