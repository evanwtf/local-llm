"""A pinned client that drifted must refuse, not warn (#131)."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import client_versions

REPO = pathlib.Path(__file__).resolve().parents[1]


def write(tmp_path, body):
    p = tmp_path / "client-versions.toml"
    p.write_text(body)
    return p


def test_the_repo_ships_pins_for_the_clients_it_drives():
    pins = client_versions.load_recorded()
    assert "opencode" in pins, "OpenCode is the client #104 measured drifting"
    assert pins["opencode"]


def test_an_empty_string_is_not_a_pin(tmp_path):
    """ "" means present but deliberately unpinned; it must not compare."""
    pins = client_versions.load_recorded(
        write(tmp_path, '[reference]\naider = ""\nx = "1.0"\n')
    )
    assert pins == {"x": "1.0"}


def test_a_missing_file_pins_nothing(tmp_path):
    assert client_versions.load_recorded(tmp_path / "absent.toml") == {}


def test_malformed_toml_pins_nothing_rather_than_crashing(tmp_path):
    assert client_versions.load_recorded(write(tmp_path, "[reference\nbroken")) == {}


def test_a_matching_version_is_not_drift():
    assert (
        client_versions.moved_since({"opencode": "1.18.27"}, {"opencode": "1.18.27"})
        == []
    )


def test_tool_decoration_does_not_count_as_drift():
    """The tools disagree on format; the pin file should not encode each one."""
    assert (
        client_versions.moved_since(
            {"codex": "codex-cli 0.152.0"}, {"codex": "0.152.0"}
        )
        == []
    )
    assert (
        client_versions.moved_since(
            {"ollama": "ollama version is 0.33.3"}, {"ollama": "0.33.3"}
        )
        == []
    )


def test_a_different_version_is_drift():
    got = client_versions.moved_since({"opencode": "1.18.26"}, {"opencode": "1.18.27"})
    assert got == [("opencode", "1.18.27", "1.18.26")]


def test_a_pinned_client_that_is_absent_is_drift_not_a_skip():
    """The pin says this machine should have it; absence is drift too."""
    got = client_versions.moved_since({}, {"opencode": "1.18.27"})
    assert got == [("opencode", "1.18.27", "not found")]


def test_preflight_never_refuses_on_a_client_version():
    """The pin was removed on 2026-09-04: this laptop is a daily driver, and a
    guard that gets overridden every time teaches people to skip reading it.

    Asserted as an absence deliberately. Restoring the refusal is the obvious
    thing for a later reader to do, and the reason not to lives here."""
    text = (REPO / "benchmarks" / "agent" / "preflight.py").read_text()
    assert "refusing to run with a drifted client" not in text
    assert "check_client_versions(" in text


def test_the_record_file_explains_why_nothing_is_pinned():
    """The file somebody opens to "fix" this must carry the reason not to."""
    text = (REPO / "client-versions.toml").read_text()
    assert "daily driver" in text
    assert "client_version" in text, "the row is what makes the trade safe"


def test_the_env_var_disables_autoupdate(tmp_path):
    got, how = client_versions.opencode_autoupdate_disabled(
        env={"OPENCODE_DISABLE_AUTOUPDATE": "1"}, config=tmp_path / "absent.json"
    )
    assert got and "OPENCODE_DISABLE_AUTOUPDATE" in how


def test_the_config_key_disables_autoupdate(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text('{"autoupdate": false}')
    got, how = client_versions.opencode_autoupdate_disabled(env={}, config=cfg)
    assert got and "autoupdate" in how


def test_autoupdate_true_is_not_disabled(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text('{"autoupdate": true}')
    got, _ = client_versions.opencode_autoupdate_disabled(env={}, config=cfg)
    assert not got


def test_an_unreadable_config_is_not_read_as_disabled(tmp_path):
    """Absence of evidence is not the switch being off."""
    got, how = client_versions.opencode_autoupdate_disabled(
        env={}, config=tmp_path / "nope.json"
    )
    assert not got and "unreadable" in how


def test_preflight_reports_autoupdate_but_does_not_change_the_config():
    """A harness silently editing the tool under test is the class of thing
    #131 exists to prevent."""
    text = (REPO / "benchmarks" / "agent" / "preflight.py").read_text()
    assert "autoupdate_status" in text
    body = text.split("def check_client_versions")[1].split("\ndef ")[0]
    for forbidden in ("opencode.json", "write_text", "opencode upgrade"):
        assert forbidden not in body


def test_no_clients_installed_is_not_a_refusal(monkeypatch):
    """The case CI caught and the local suite could not: a machine that drives
    none of the pinned clients -- the Linux runner -- must still run preflight.

    An absent client cannot take a row, so it cannot corrupt a comparison.
    Refusing on absence made preflight refuse everywhere but this laptop.
    """
    import preflight

    monkeypatch.setattr(preflight.staleness, "installed_versions", dict)
    assert preflight.check_client_versions(offline=True) is False


def test_an_installed_but_different_version_is_recorded_not_refused(monkeypatch):
    """It logs a series boundary and returns False. Nothing blocks."""
    import preflight

    recorded = client_versions.load_recorded()
    wrong = {name: "0.0.1" for name in recorded}
    monkeypatch.setattr(preflight.staleness, "installed_versions", lambda: wrong)
    assert preflight.check_client_versions(offline=True) is False


def test_the_decision_not_to_pin_is_recorded_where_it_would_be_undone():
    """Restoring the pin is the obvious move for a later reader who sees an
    unpinned client move a number. The reason not to must sit in the file
    they would open to do it (#131)."""
    # The phrases wrap across comment lines, so compare on normalised text
    # rather than the literal -- the first version of this assertion matched
    # the string as written in the commit message, not as written in the file.
    text = " ".join(
        line.lstrip("# ").strip()
        for line in (REPO / "client-versions.toml").read_text().splitlines()
    )
    assert "Do not restore it without the operator asking" in text
    assert "daily driver" in text


# ---------------------------------------------------------------------------
# With nothing pinned, "run the current version" is the rule, so preflight has
# to ask the opposite question: is anything BEHIND?


def test_a_client_behind_its_release_is_reported():
    got = client_versions.behind_latest(
        {"opencode": "1.18.27"}, {"opencode": "v1.18.28"}
    )
    assert got == [("opencode", "1.18.27", "v1.18.28")]


def test_a_current_client_is_not_reported():
    assert (
        client_versions.behind_latest(
            {"codex": "codex-cli 0.152.0"}, {"codex": "0.152.0"}
        )
        == []
    )


def test_a_client_ahead_of_the_release_is_not_reported():
    """A prerelease or a local build is not "behind"."""
    assert (
        client_versions.behind_latest({"claude": "2.1.262"}, {"claude": "2.1.261"})
        == []
    )


def test_an_unreadable_latest_is_skipped_not_called_current():
    """Not knowing is not the same as being up to date."""
    assert (
        client_versions.behind_latest({"opencode": "1.18.27"}, {"opencode": None}) == []
    )


def test_every_client_has_an_upgrade_command_to_print():
    """A warning that does not say what to type is a warning people ignore."""
    for name in ("opencode", "claude", "codex"):
        assert client_versions.UPGRADE_COMMAND[name]


def test_preflight_reports_a_behind_client_but_never_upgrades_it():
    """A harness that updates the tool under test moves the version mid-batch,
    which is the exact failure #131 is about."""
    text = (REPO / "benchmarks" / "agent" / "preflight.py").read_text()
    body = text.split("def _log_clients_behind")[1].split("\ndef ")[0]
    assert "behind_latest" in body
    for forbidden in ("subprocess", "opencode upgrade", "claude update"):
        assert forbidden not in body


def test_autoupdate_status_covers_every_client_we_drive():
    got = client_versions.autoupdate_status()
    assert set(got) == {"opencode", "claude", "codex"}
    for how in got.values():
        assert how.startswith(("on (", "off ("))
