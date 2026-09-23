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
    for rel, mode in mod.MOUNTS:
        suffix = ":ro" if mode == "ro" else ""
        assert f"{HOME / rel}:{mod.CONTAINER_HOME}/{rel}{suffix}" in pairs


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


def test_the_hosts_opencode_state_is_not_mounted():
    """Under bwrap inside the container the process is mapped into a user
    namespace where root's override does not apply, so the host's state dir
    (owned by the host user) makes OpenCode die opening its own log --
    every trial fails in under a second, never reaching the server."""
    mounted = {rel for rel, _ in mod.MOUNTS}
    assert ".local/share/opencode" not in mounted
    assert ".local/share/opencode" in mod.UNMOUNTED


def test_the_hosts_opencode_config_is_read_only():
    argv = _argv()
    assert (
        f"{HOME / '.config/opencode'}:{mod.CONTAINER_HOME}/.config/opencode:ro" in argv
    )


def test_host_paths_in_the_harness_arguments_are_translated():
    """--client-log $HOME/bench-logs/<run> pointed at a path that does not
    exist in the container, so every transcript was written to container-local
    storage and discarded on exit. The rows survived -- the ledger is inside
    the mounted repo -- so the run looked complete while the per-trial
    evidence was gone (#611)."""
    got = mod.translate_paths(
        ["--client-log", f"{HOME}/bench-logs/611c", "--trials", "3"], HOME
    )
    assert got == [
        "--client-log",
        f"{mod.CONTAINER_HOME}/bench-logs/611c",
        "--trials",
        "3",
    ]


def test_arguments_that_are_not_host_paths_are_left_alone():
    got = mod.translate_paths(
        ["--backend", "b", "--metrics-url", "http://srv:8888"], HOME
    )
    assert got == ["--backend", "b", "--metrics-url", "http://srv:8888"]


def test_the_container_gets_a_hard_memory_limit_by_default():
    """#680: without --memory the harness cap had nothing under it, and the
    default 24 GiB cap sat above the 15 GiB client entirely."""
    argv = _argv()
    i = argv.index("--memory")
    assert argv[i + 1] == "12288m"
    j = argv.index("--memory-swap")
    assert argv[j + 1] == argv[i + 1], "swap must be capped too, or it pages instead"


def test_the_memory_limit_can_be_changed_or_disabled():
    argv = _argv(mem_limit_gib=10)
    assert argv[argv.index("--memory") + 1] == "10240m"
    assert "--memory" not in _argv(mem_limit_gib=0)
    assert "--memory" not in _argv(mem_limit_gib=None)


def test_the_limit_is_a_docker_flag_not_harness_argv():
    argv = _argv()
    assert argv.index("--memory") < argv.index("img")
