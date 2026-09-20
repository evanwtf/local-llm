"""The container invocation is where a silent confound would hide. #611

Three of these guard mistakes that would not look like mistakes: reusing the
host's virtualenv, dropping a mount the harness needs, and quietly shipping a
weaker privilege posture than the one bwrap was measured to require.
"""

from __future__ import annotations

import pathlib

import client_container as mod

HOME = pathlib.Path("/home/someone")
FACTS = pathlib.Path("/home/someone/facts.json")


def _argv(**kw):
    base = {
        "image": "img",
        "home": HOME,
        "server": "srv",
        "facts": FACTS,
        "command": ["--backend", "b", "--trials", "3"],
    }
    return mod.docker_argv(**{**base, **kw})


def test_the_project_environment_is_outside_the_mounted_repo():
    """Otherwise `uv run` uses the HOST's .venv and the image's Python is
    never exercised -- the run would measure nothing new."""
    argv = _argv()
    env = argv[argv.index("-e", argv.index("--network")) :]
    assert f"UV_PROJECT_ENVIRONMENT={mod.PROJECT_ENV}" in env
    assert not mod.PROJECT_ENV.startswith(f"{mod.CONTAINER_HOME}/git/local-llm")


def test_home_is_the_containers_own_so_tilde_paths_resolve():
    argv = _argv()
    assert f"HOME={mod.CONTAINER_HOME}" in argv
    assert "-w" in argv
    assert f"{mod.CONTAINER_HOME}/git/local-llm" in argv


def test_every_mount_lands_at_the_same_relative_path():
    got = mod.mount_args(HOME)
    pairs = [got[i + 1] for i in range(0, len(got), 2)]
    for rel in mod.MOUNTS:
        assert f"{HOME / rel}:{mod.CONTAINER_HOME}/{rel}" in pairs


def test_the_measured_privilege_posture_is_what_ships():
    """bwrap needs all three; each weaker combination failed at a different
    stage when measured on 2026-09-20."""
    argv = _argv()
    assert "SYS_ADMIN" in argv
    assert "seccomp=unconfined" in argv
    assert "apparmor=unconfined" in argv


def test_host_networking_so_the_lan_is_not_behind_a_bridge():
    argv = _argv()
    assert argv[argv.index("--network") + 1] == "host"


def test_the_facts_file_is_read_only_and_named_inside():
    argv = _argv()
    assert f"{FACTS}:{mod.CONTAINER_HOME}/facts.json:ro" in argv
    assert f"LOCAL_LLM_SERVER_FACTS={mod.CONTAINER_HOME}/facts.json" in argv


def test_the_memory_cap_is_only_set_when_asked_for():
    assert not any("CLIENT_MEM_CAP" in a for a in _argv())
    assert "LOCAL_LLM_CLIENT_MEM_CAP_GIB=11" in _argv(mem_cap_gib=11)


def test_a_missing_mount_is_named_before_the_run_starts(tmp_path):
    (tmp_path / "bench-logs").mkdir()
    gone = mod.missing_mounts(tmp_path)
    assert "bench-logs" not in gone
    assert "git/local-llm" in gone


def test_the_harness_is_what_the_container_runs():
    argv = _argv()
    assert argv[-5:] == ["benchmarks/agent/run.py", "--backend", "b", "--trials", "3"]


def test_git_is_told_the_mounted_repo_is_safe():
    """The repo is owned by the host user and the container runs as root, so
    git refuses to clone the sandbox target with "detected dubious ownership".
    Without this no trial can start."""
    argv = _argv()
    assert "GIT_CONFIG_COUNT=1" in argv
    assert "GIT_CONFIG_KEY_0=safe.directory" in argv
    assert "GIT_CONFIG_VALUE_0=*" in argv
