"""The client image's pins are only worth having if a drift is a test failure.

#611: the image exists so a row's client identity is a CPU and a memory size,
not also a distro and a Python. These tests check the two things that would
silently break that -- a pin drifting between the Dockerfile and the verifier,
and a version string parsed differently on one architecture than the other.
"""

from __future__ import annotations

import pathlib
import re

import build_client_image as mod

DOCKERFILE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "docker"
    / "opencode-client"
    / "Dockerfile"
)


def test_every_pin_matches_the_dockerfile():
    """The script's PINS and the Dockerfile's ARGs are one statement in two
    places, which is exactly how they drift."""
    text = DOCKERFILE.read_text()
    args = dict(re.findall(r"^ARG (\w+)_VERSION=(\S+)$", text, re.MULTILINE))
    assert args, "no ARG <name>_VERSION lines found in the Dockerfile"
    for name, want in mod.PINS.items():
        assert args.get(name.upper()) == want, (
            f"{name}: script {want}, Dockerfile {args.get(name.upper())}"
        )


def test_both_machines_this_project_runs_on_are_supported():
    assert mod.platform_for("x86_64") == "linux/amd64"
    assert mod.platform_for("aarch64") == "linux/arm64"


def test_an_unknown_architecture_is_refused_rather_than_guessed():
    assert mod.platform_for("riscv64") is None


def test_uv_version_parses_the_same_on_both_arches():
    """`uv --version` carries the build triple, so a naive compare passes on
    one machine and fails on the other."""
    amd = mod.parse_versions({"uv": "uv 0.12.13 (x86_64-unknown-linux-gnu)"})
    arm = mod.parse_versions({"uv": "uv 0.12.13 (aarch64-unknown-linux-gnu)"})
    assert amd["uv"] == arm["uv"] == "0.12.13"


def test_python_and_opencode_versions_parse():
    got = mod.parse_versions({"python": "Python 3.14.4", "opencode": "1.18.31"})
    assert got == {"python": "3.14.4", "opencode": "1.18.31"}


def test_a_pin_mismatch_is_reported_not_swallowed():
    got = mod.check_pins(
        {"opencode": "1.18.30", "uv": "0.12.13", "python": "3.14.4"}, mod.PINS
    )
    by_name = {name: ok for name, ok, _ in got}
    assert by_name["opencode"] is False
    assert by_name["uv"] is True and by_name["python"] is True


def test_a_missing_tool_is_a_failure_with_a_readable_detail():
    got = {name: (ok, detail) for name, ok, detail in mod.check_pins({}, mod.PINS)}
    assert got["uv"][0] is False
    assert "missing" in got["uv"][1] and "pinned 0.12.13" in got["uv"][1]
