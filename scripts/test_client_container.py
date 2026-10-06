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


def test_default_image_follows_the_build_pin():
    # The default was a literal "local-llm-client:1.18.33" that no bump touched,
    # so after the 1.18.34 bump every launch was refused as BEHIND (2026-09-30).
    import build_client_image

    assert (
        mod.DEFAULT_IMAGE == f"local-llm-client:{build_client_image.PINS['opencode']}"
    )
    src = pathlib.Path(mod.__file__).read_text()
    assert 'default="local-llm-client:' not in src


def test_an_equals_form_host_path_is_translated_too():
    """`--results=PATH` stayed a host path, so the harness wrote every row to a
    container-local file that `--rm` then deleted. The space form was the only
    one translated; argparse accepts both, and so must the wrapper."""
    got = mod.translate_paths(
        [
            f"--results={HOME}/git/local-llm/hardware/X/results.jsonl",
            f"--client-log={HOME}/bench-logs/run1",
            "--trials=3",
        ],
        HOME,
    )
    assert got == [
        f"--results={mod.CONTAINER_HOME}/git/local-llm/hardware/X/results.jsonl",
        f"--client-log={mod.CONTAINER_HOME}/bench-logs/run1",
        "--trials=3",
    ]


def test_an_output_path_the_container_cannot_keep_is_named():
    """An output outside every writable mount is deleted with the container.
    The rows or transcripts vanish while the run looks complete, so the
    wrapper names such a path before it starts the run."""
    got = mod.lost_outputs(
        [
            "--results",
            "/tmp/rows.jsonl",
            f"--client-log={HOME}/elsewhere/run1",
            "--solutions",
            f"{HOME}/bench-solutions/run1",
            "--results=hardware/X/results.jsonl",
            "--backend",
            "/not/an/output",
        ],
        HOME,
    )
    assert got == ["--results /tmp/rows.jsonl", f"--client-log {HOME}/elsewhere/run1"]


def test_the_documented_invocation_loses_nothing():
    """hardware/agent-opener-prompt.md, "One arm, start to finish"."""
    command = [
        "--backend",
        "b",
        "--client",
        "opencode",
        "--trials",
        "3",
        "--targets",
        "sandbox",
        "--replay",
    ]
    assert mod.lost_outputs(command, HOME) == []
    assert mod.translate_paths(command, HOME) == command


def _batch(tmp_path, monkeypatch, smoke_failures):
    """main() up to the docker launch, with every gate but the smoke faked."""
    import client_tool_smoke

    for rel, _ in mod.MOUNTS:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    facts = tmp_path / "facts.json"
    facts.write_text("{}")
    monkeypatch.setattr(mod, "check_image_current", lambda image: None)
    monkeypatch.setattr(mod, "check_facts_current", lambda facts, command: None)
    smoked, launched = [], []

    def smoke(image, **kw):
        smoked.append(image)
        return smoke_failures

    def launch(argv, **kw):
        launched.append(argv)
        return 0

    monkeypatch.setattr(client_tool_smoke, "run_in_image", smoke)
    monkeypatch.setattr(mod.child, "run", launch)
    argv = ["--server", "s", "--facts", str(facts), "--home", str(tmp_path)]
    argv += ["--image", "img", "--log", str(tmp_path / "client.log")]
    return argv, smoked, launched


def test_every_batch_runs_the_tool_self_test_first(tmp_path, monkeypatch):
    """#968: grep and glob failed on every call for two weeks of batches."""
    argv, smoked, launched = _batch(tmp_path, monkeypatch, [])
    assert mod.main([*argv, "--", "--backend", "b"]) == 0
    assert smoked == ["img"]
    assert len(launched) == 1


def test_a_failed_tool_self_test_refuses_the_batch(tmp_path, monkeypatch):
    argv, smoked, launched = _batch(tmp_path, monkeypatch, ["grep: error: tar"])
    assert mod.main([*argv, "--", "--backend", "b"]) == 1
    assert smoked == ["img"]
    assert launched == []


def test_the_self_test_is_skipped_only_when_asked(tmp_path, monkeypatch):
    argv, smoked, launched = _batch(tmp_path, monkeypatch, ["grep: error: tar"])
    assert mod.main([*argv, "--skip-tool-smoke", "--", "--backend", "b"]) == 0
    assert smoked == []
    assert len(launched) == 1


def test_an_image_must_declare_its_ripgrep():
    """#968: the pin that tells a fixed image from a broken one."""
    assert mod.PIN_ENV["LOCAL_LLM_PINNED_RIPGREP"] == "ripgrep"


def test_the_harness_and_the_self_test_share_one_container_posture():
    argv = _argv()
    base = mod.container_argv(image="img", home=HOME, command=["true"])
    assert argv[: argv.index("-v")] == base[: base.index("-v")]
    for entry in mod.BASE_ENV:
        assert entry in argv and entry in base
