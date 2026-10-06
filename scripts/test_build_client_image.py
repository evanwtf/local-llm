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
        {"opencode": "1.18.30", "uv": mod.PINS["uv"], "python": mod.PINS["python"]},
        mod.PINS,
    )
    by_name = {name: ok for name, ok, _ in got}
    assert by_name["opencode"] is False
    assert by_name["uv"] is True and by_name["python"] is True


def test_a_missing_tool_is_a_failure_with_a_readable_detail():
    got = {name: (ok, detail) for name, ok, detail in mod.check_pins({}, mod.PINS)}
    assert got["uv"][0] is False
    assert "missing" in got["uv"][1] and f"pinned {mod.PINS['uv']}" in got["uv"][1]


def test_build_pulls_the_base_and_skips_the_cache(monkeypatch, tmp_path):
    """A rebuild on 2026-10-01 took uv and CPython current but left
    rust-coreutils at the base image's version: `docker build` reused a cached
    apt layer. Every rebuild now pulls the base and skips the cache."""
    seen = []

    def run(argv, **_):
        seen.append(argv)
        return mod.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(mod.subprocess, "run", run)
    assert mod.build("t:1", tmp_path) == 0
    assert seen[0][:2] == ["docker", "build"]
    assert "--pull" in seen[0]
    assert "--no-cache" in seen[0]


def test_dockerfile_upgrades_the_base_packages():
    """Install alone leaves the base image's own packages at its versions."""
    assert "apt-get upgrade -y" in DOCKERFILE.read_text()


def test_ripgrep_is_pinned_and_parsed():
    """#968: OpenCode's grep and glob need `rg` on PATH in the image."""
    assert "ripgrep" in mod.PINS
    got = mod.parse_versions({"ripgrep": "ripgrep 15.2.0 (rev abc123)"})
    assert got == {"ripgrep": "15.2.0"}
    missing = mod.parse_versions({"ripgrep": "ripgrep missing"})
    assert missing["ripgrep"] != mod.PINS["ripgrep"]


def test_the_image_puts_rg_on_path_and_records_its_pin():
    text = DOCKERFILE.read_text()
    assert "LOCAL_LLM_PINNED_RIPGREP=${RIPGREP_VERSION}" in text
    assert "install -m 0755" in text and "/usr/local/bin/rg" in text


def test_a_build_is_checked_under_a_candidate_tag():
    assert mod.candidate_tag("local-llm-client:1.18.34") == (
        "local-llm-client:1.18.34-candidate"
    )
    assert mod.candidate_tag("local-llm-client") == "local-llm-client:candidate"


def _fake_docker(monkeypatch, *, smoke_failures):
    calls = []

    def run(argv, **_):
        calls.append(argv)
        return mod.subprocess.CompletedProcess(argv, 0, "", "")

    def build(tag, context=None):
        calls.append(["build", tag])
        return 0

    def smoke(tag):
        calls.append(["smoke", tag])
        return smoke_failures

    seen = {
        "opencode": mod.PINS["opencode"],
        "uv": f"uv {mod.PINS['uv']} (x86_64-unknown-linux-gnu)",
        "python": f"Python {mod.PINS['python']}",
        "git": "git version 2.53.0",
        "machine": "x86_64",
        "ripgrep": f"ripgrep {mod.PINS['ripgrep']} (rev 1)",
    }
    monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mod, "build", build)
    monkeypatch.setattr(mod, "inspect", lambda tag: seen)
    monkeypatch.setattr(mod, "tool_smoke", smoke)
    monkeypatch.setattr(mod, "_run", run)
    return calls


def test_a_build_whose_tools_fail_is_never_tagged(monkeypatch):
    """#968: such a build must not become the image client_container.py runs."""
    calls = _fake_docker(monkeypatch, smoke_failures=["grep: error: tar"])
    assert mod.main(["--tag", "local-llm-client:9"]) == 1
    assert ["build", "local-llm-client:9-candidate"] in calls
    assert ["smoke", "local-llm-client:9-candidate"] in calls
    assert not any(c[:2] == ["docker", "tag"] for c in calls)
    assert ["docker", "rmi", "local-llm-client:9-candidate"] in calls


def test_a_build_that_passes_every_check_gets_its_tag(monkeypatch):
    calls = _fake_docker(monkeypatch, smoke_failures=[])
    assert mod.main(["--tag", "local-llm-client:9"]) == 0
    tag = ["docker", "tag", "local-llm-client:9-candidate", "local-llm-client:9"]
    assert tag in calls
    assert calls.index(["smoke", "local-llm-client:9-candidate"]) < calls.index(tag)


def test_skip_build_still_runs_the_tool_self_test(monkeypatch):
    calls = _fake_docker(monkeypatch, smoke_failures=["glob: error: tar"])
    assert mod.main(["--tag", "local-llm-client:9", "--skip-build"]) == 1
    assert ["smoke", "local-llm-client:9"] in calls
