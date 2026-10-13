"""The container invocation is where a silent confound would hide. #611

Three of these guard mistakes that would not look like mistakes: reusing the
host's virtualenv, dropping a mount the harness needs, and quietly shipping a
weaker privilege posture than the one bwrap was measured to require.
"""

from __future__ import annotations

import datetime as dt
import json
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
    monkeypatch.setattr(mod, "check_provider_for", lambda command, home, **kw: None)
    smoked, launched = [], []

    def smoke(image, **kw):
        smoked.append(image)
        return smoke_failures

    def launch(argv, **kw):
        launched.append(argv)
        return 0

    monkeypatch.setattr(client_tool_smoke, "run_in_image", smoke)
    monkeypatch.setattr(mod.child, "run", launch)
    monkeypatch.setattr(mod, "audit_batch", lambda *a, **kw: 0)
    argv = ["--server", "s", "--facts", str(facts), "--home", str(tmp_path)]
    argv += ["--image", "img", "--log", str(tmp_path / "client.log")]
    return argv, smoked, launched


def _audited(monkeypatch, verdict):
    calls = []

    def audit(backend, since, home, log):
        calls.append((backend, since, home, log))
        return verdict

    monkeypatch.setattr(mod, "audit_batch", audit)
    return calls


def test_every_batch_ends_with_the_transcript_audit(tmp_path, monkeypatch):
    """#969: the audit ran only when someone remembered it. On 2026-10-10 it
    ran on #1018's 126 trials only because a label sweep named the issue."""
    argv, _, launched = _batch(tmp_path, monkeypatch, [])
    calls = _audited(monkeypatch, 0)
    assert mod.main([*argv, "--", "--backend", "b"]) == 0
    assert len(launched) == 1
    [(backend, since, home, log)] = calls
    assert backend == "b"
    assert home == tmp_path
    assert log == tmp_path / "client.log"
    # The window opens before the launch, with an offset, so it cannot miss
    # the first trial or be read in the wrong zone.
    assert dt.datetime.fromisoformat(since).tzinfo is not None


def test_a_harness_fault_fails_the_batch(tmp_path, monkeypatch):
    argv, _, _ = _batch(tmp_path, monkeypatch, [])
    _audited(monkeypatch, 1)
    assert mod.main([*argv, "--", "--backend", "b"]) == 1


def _faulted(tmp_path, ledger_rows):
    """A client home with one ledger, and a facts file that names it."""
    d = tmp_path / "git" / "local-llm" / "hardware" / "X2"
    d.mkdir(parents=True)
    ledger = d / "results.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in ledger_rows))
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"directory": "X2"}))
    audit = tmp_path / "b1-audit.json"
    audit.write_text(json.dumps({"backends": []}))
    return ledger, facts, audit


_ROW_IN = {"backend": "b", "started": "2026-10-13T09:10:00-0400", "task": "t1"}
_ROW_BEFORE = {"backend": "b", "started": "2026-10-13T08:00:00-0400", "task": "t0"}
_ROW_OTHER = {"backend": "c", "started": "2026-10-13T09:10:00-0400", "task": "t1"}


def test_a_faulted_batch_has_its_rows_excluded(tmp_path):
    """#969 item 2: rows from a batch with a broken tool must not land quietly."""
    ledger, facts, audit = _faulted(tmp_path, [_ROW_BEFORE, _ROW_IN, _ROW_OTHER])
    n = mod.quarantine_batch(
        tmp_path,
        facts,
        "b",
        "2026-10-13T09:00:00-04:00",
        "2026-10-13T10:00:00-04:00",
        audit,
        None,
    )
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert n == 1
    assert [r.get("excluded", False) for r in rows] == [False, True, False]
    assert "#969" in rows[1]["exclusion_reason"]
    assert str(audit) in rows[1]["exclusion_reason"]
    # Annotated, never rewritten: every measured field is unchanged.
    assert {k: rows[1][k] for k in _ROW_IN} == _ROW_IN


def test_an_override_keeps_the_rows_and_records_the_reason(tmp_path):
    ledger, facts, audit = _faulted(tmp_path, [_ROW_IN])
    before = ledger.read_text()
    n = mod.quarantine_batch(
        tmp_path,
        facts,
        "b",
        "2026-10-13T09:00:00-04:00",
        "2026-10-13T10:00:00-04:00",
        audit,
        "glob fails only on a path the task never uses",
    )
    assert n == 0
    assert ledger.read_text() == before
    assert json.loads(audit.read_text())["override_fault"] == (
        "glob fails only on a path the task never uses"
    )


def test_a_facts_file_without_a_directory_excludes_nothing_but_says_so(
    tmp_path, caplog
):
    facts = tmp_path / "facts.json"
    facts.write_text("{}")
    n = mod.quarantine_batch(
        tmp_path,
        facts,
        "b",
        "2026-10-13T09:00:00-04:00",
        "2026-10-13T10:00:00-04:00",
        tmp_path / "a.json",
        None,
    )
    assert n == 0
    assert "NOT excluded" in caplog.text


def test_a_harness_fault_quarantines_the_batch(tmp_path, monkeypatch):
    argv, _, _ = _batch(tmp_path, monkeypatch, [])
    _audited(monkeypatch, 1)
    seen = []

    def quarantine(*a):
        seen.append(a)
        return 0

    monkeypatch.setattr(mod, "quarantine_batch", quarantine)
    assert mod.main([*argv, "--override-fault", "why", "--", "--backend", "b"]) == 1
    [args] = seen
    assert args[2] == "b" and args[-1] == "why"


def test_an_unreadable_audit_does_not_fail_a_good_batch(tmp_path, monkeypatch):
    """No transcripts (a refused run, an early stop at zero) is not a fault."""
    argv, _, _ = _batch(tmp_path, monkeypatch, [])
    _audited(monkeypatch, 2)
    assert mod.main([*argv, "--", "--backend", "b"]) == 0


def test_no_audit_when_the_batch_never_ran(tmp_path, monkeypatch):
    argv, _, launched = _batch(tmp_path, monkeypatch, ["grep: error: tar"])
    calls = _audited(monkeypatch, 0)
    assert mod.main([*argv, "--", "--backend", "b"]) == 1
    assert launched == [] and calls == []
    argv, _, _ = _batch(tmp_path, monkeypatch, [])
    calls = _audited(monkeypatch, 0)
    assert mod.main([*argv, "--print", "--", "--backend", "b"]) == 0
    assert calls == []


def test_the_audit_summary_lands_beside_the_batch_log(tmp_path, monkeypatch):
    seen: list[list[str]] = []
    import audit_transcripts

    def fake_main(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr(audit_transcripts, "main", fake_main)
    log = tmp_path / "1018-r1-client.log"
    assert mod.audit_batch("b", "2026-10-10T09:40:00-04:00", tmp_path, log) == 0
    [argv] = seen
    assert argv[argv.index("--json") + 1] == str(tmp_path / "1018-r1-client-audit.json")
    assert argv[argv.index("--logs") + 1] == str(tmp_path / "bench-logs")
    assert argv[argv.index("--backend") + 1] == "b"


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


# #956: on 2026-10-05 a batch started with the backend's OpenCode provider
# (`tfmia8888`) missing from the client's opencode.json. Every trial failed in
# ~2 s with "UnknownError: Unexpected server error", and the rows read as model
# failures. The preflight now refuses that before the first trial.
_BACKEND = {
    "base_url": "http://127.0.0.1:8888",
    "opencode_model": "tfmia8888/GLM-5.3-Flash-EXL3",
}
_CONFIG = """{
  "provider": {
    "tfmia8888": {
      "options": {"baseURL": "http://head.example:8888/v1"},
      "models": {"GLM-5.3-Flash-EXL3": {}}
    }
  }
}"""
_PATH = pathlib.Path("/home/someone/.config/opencode/opencode.json")


def test_provider_check_passes_when_provider_model_and_port_match():
    assert mod.check_opencode_provider("b", _BACKEND, _CONFIG, _PATH) is None


def test_provider_check_refuses_a_missing_provider_and_names_it():
    backend = {**_BACKEND, "opencode_model": "vllm8001/some-model"}
    why = mod.check_opencode_provider("b", backend, _CONFIG, _PATH)
    assert why is not None
    assert "vllm8001" in why and str(_PATH) in why


def test_provider_check_refuses_a_model_the_provider_does_not_declare():
    backend = {**_BACKEND, "opencode_model": "tfmia8888/Qwen3.8-Flash-Next"}
    why = mod.check_opencode_provider("b", backend, _CONFIG, _PATH)
    assert why is not None and "Qwen3.8-Flash-Next" in why


def test_provider_check_refuses_a_port_that_differs_from_the_backend():
    backend = {**_BACKEND, "base_url": "http://127.0.0.1:8030"}
    why = mod.check_opencode_provider("b", backend, _CONFIG, _PATH)
    assert why is not None and "8030" in why and "8888" in why


def test_provider_check_refuses_a_host_that_differs_from_the_server():
    """#680 finding 14: the port matched but the host was never compared, so a
    provider aimed at another machine on the same port passed."""
    why = mod.check_opencode_provider(
        "b", _BACKEND, _CONFIG, _PATH, server="other.example"
    )
    assert why is not None and "head.example" in why and "other.example" in why
    ok = mod.check_opencode_provider(
        "b", _BACKEND, _CONFIG, _PATH, server="HEAD.example"
    )
    assert ok is None


def test_provider_check_refuses_loopback_in_the_client_container():
    """Inside the client container 127.0.0.1 is the client, not the server."""
    config = _CONFIG.replace("head.example", "127.0.0.1")
    why = mod.check_opencode_provider(
        "b", _BACKEND, config, _PATH, server="head.example"
    )
    assert why is not None and "127.0.0.1" in why


def test_provider_check_refuses_an_unreadable_config():
    why = mod.check_opencode_provider("b", _BACKEND, None, _PATH)
    assert why is not None and str(_PATH) in why
    why = mod.check_opencode_provider("b", _BACKEND, "{not json", _PATH)
    assert why is not None and str(_PATH) in why


def test_provider_check_refuses_an_unknown_backend():
    why = mod.check_opencode_provider("nope", None, _CONFIG, _PATH)
    assert why is not None and "nope" in why


def test_provider_check_skips_a_backend_with_no_opencode_model():
    backend = {"base_url": "http://127.0.0.1:8888"}
    assert mod.check_opencode_provider("b", backend, _CONFIG, _PATH) is None


def test_provider_check_reads_the_files_the_container_mounts(tmp_path):
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    (tmp_path / ".config" / "opencode" / "opencode.json").write_text(_CONFIG)
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(
        '[backend.good]\nbase_url = "http://127.0.0.1:8888"\n'
        'opencode_model = "tfmia8888/GLM-5.3-Flash-EXL3"\n'
        '[backend.bad]\nbase_url = "http://127.0.0.1:8888"\n'
        'opencode_model = "absent/x"\n'
    )
    assert mod.check_provider_for(["--backend", "good"], tmp_path, tasks) is None
    assert "absent" in (
        mod.check_provider_for(["--backend", "bad"], tmp_path, tasks) or ""
    )
    # No --backend: nothing to check here; run.py validates its own arguments.
    assert mod.check_provider_for(["--trials", "3"], tmp_path, tasks) is None


def test_a_missing_provider_refuses_the_batch_before_any_trial(tmp_path, monkeypatch):
    """#956: the refusal comes before the self-test and the launch."""
    argv, smoked, launched = _batch(tmp_path, monkeypatch, [])
    monkeypatch.setattr(
        mod, "check_provider_for", lambda command, home, **kw: "preflight: no provider"
    )
    assert mod.main([*argv, "--", "--backend", "b"]) == 1
    assert smoked == []
    assert launched == []


# #959 item 3: the client container mounts no credentials and passes none, and
# a change that adds one is refused before the batch, not found 16 days later.


def test_the_shipped_container_carries_no_credentials():
    assert mod.credential_leaks(_argv(), HOME) == []


def test_a_mounted_ssh_dir_is_a_leak():
    argv = [*_argv()[:3], "-v", f"{HOME}/.ssh:/root/.ssh:ro", *_argv()[3:]]
    assert mod.credential_leaks(argv, HOME) == [f"mount {HOME}/.ssh"]


def test_mounting_the_whole_home_is_a_leak():
    argv = ["docker", "run", "-v", f"{HOME}:/root", "img"]
    assert mod.credential_leaks(argv, HOME) == [f"mount {HOME}"]


def test_a_credential_variable_is_a_leak():
    argv = ["docker", "run", "-e", "SSH_AUTH_SOCK=/tmp/agent", "-e", "GH_TOKEN", "img"]
    assert mod.credential_leaks(argv, HOME) == ["env SSH_AUTH_SOCK", "env GH_TOKEN"]


def test_the_credential_list_matches_the_trial_sandboxes():
    import run

    assert mod.CREDENTIAL_PATHS == run.CREDENTIAL_PATHS


def test_a_leak_refuses_the_batch(tmp_path, monkeypatch):
    argv, _, launched = _batch(tmp_path, monkeypatch, [])
    monkeypatch.setattr(mod, "credential_leaks", lambda full, home: ["env GH_TOKEN"])
    assert mod.main([*argv, "--", "--backend", "b"]) == 1
    assert launched == []
