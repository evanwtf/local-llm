"""Tests for the harness helpers that record provenance.

`probe_server` runs before every batch and touches the network. It records
what the server is really serving -- including the sampling parameters, whose
absence let #26 blame a KV cache for ordinary sampling spread. It must record
what it can and return {} for everything else: losing provenance is bad, and
losing a half-hour trial to a provenance bug is worse.
"""

from __future__ import annotations

import io
import json
import pathlib
import tomllib
import types
import urllib.error

import pytest
import results
import run

HERE = pathlib.Path(__file__).resolve().parent

PROPS = {
    "model_path": "/models/GLM-5.3-Flash-GGUF/UD-Q2_K_XL/GLM-00001-of-00004.gguf",
    "model_alias": "glm-5.3-flash-q2",
    "build_info": "b10677-8a8d0bcc4",
    "total_slots": 1,
    "chat_template": "[gMASK]<sop>" + "x" * 5000,
    "default_generation_settings": {
        "n_ctx": 65536,
        "params": {
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 40,
            "min_p": 0.05,
            "seed": 4294967295,
            "samplers": ["top_k", "temperature"],
            "n_predict": -1,
        },
    },
}


class _Serves:
    """A stand-in for one /props endpoint: set `payload`, read back `url`."""

    def __init__(self):
        self.payload = None
        self.url = None

    def open(self, url, timeout=None):
        self.url = url
        if isinstance(self.payload, Exception):
            raise self.payload
        body = (
            self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        )
        return _closing(io.StringIO(body))


class _closing:
    def __init__(self, fh):
        self.fh = fh

    def __enter__(self):
        return self.fh

    def __exit__(self, *a):
        return False


@pytest.fixture
def serves(monkeypatch):
    stub = _Serves()
    monkeypatch.setattr(run.urllib.request, "urlopen", stub.open)
    return stub


def test_records_the_sampling_parameters(serves):
    serves.payload = PROPS
    got = run.probe_server({"base_url": "http://127.0.0.1:8030"})
    assert got["sampling"]["temperature"] == 1.0
    assert got["sampling"]["seed"] == 4294967295


def test_records_the_exact_model_file_being_served(serves):
    """The point of the probe: not every GGUF in the directory, the one loaded."""
    serves.payload = PROPS
    got = run.probe_server({"base_url": "http://127.0.0.1:8030"})
    assert got["model_path"].endswith("GLM-00001-of-00004.gguf")
    assert got["build_info"] == "b10677-8a8d0bcc4"


def test_does_not_haul_the_chat_template_into_every_row(serves):
    """/props carries a multi-kilobyte template. It must not land in the row."""
    serves.payload = PROPS
    got = run.probe_server({"base_url": "http://127.0.0.1:8030"})
    assert "chat_template" not in got
    assert len(json.dumps(got)) < 1000


def test_props_url_overrides_base_url_for_a_shimmed_backend(serves):
    serves.payload = PROPS
    run.probe_server(
        {"base_url": "http://127.0.0.1:11501", "props_url": "http://127.0.0.1:8030"}
    )
    assert serves.url == "http://127.0.0.1:8030/props"


def test_a_trailing_slash_does_not_produce_a_double_slash(serves):
    serves.payload = PROPS
    run.probe_server({"base_url": "http://127.0.0.1:8030/"})
    assert serves.url == "http://127.0.0.1:8030/props"


def test_a_hosted_backend_has_no_server_to_probe():
    assert run.probe_server({}) == {}


def test_a_refused_connection_is_not_an_error(serves):
    serves.payload = urllib.error.URLError("Connection refused")
    assert run.probe_server({"base_url": "http://127.0.0.1:9999"}) == {}


def test_a_timeout_is_not_an_error(serves):
    serves.payload = TimeoutError("timed out")
    assert run.probe_server({"base_url": "http://127.0.0.1:8030"}) == {}


def test_a_server_that_answers_with_junk_is_not_an_error(serves):
    """Ollama answers /props with a 404 page, not JSON."""
    serves.payload = "<html>404</html>"
    assert run.probe_server({"base_url": "http://127.0.0.1:11434"}) == {}


def test_a_server_with_no_sampling_block_still_records_what_it_has(serves):
    serves.payload = {"model_path": "/x.gguf", "build_info": "b1"}
    got = run.probe_server({"base_url": "http://127.0.0.1:8000"})
    assert got == {"model_path": "/x.gguf", "build_info": "b1"}


# --- direction 1: more than one symbol per task ---------------------------


def test_a_single_symbol_task_still_describes_itself_the_old_way():
    """398 rows name tasks defined with file/symbol. Do not break them."""
    task = {"name": "t", "file": "src/a.py", "symbol": "f", "tests": ["tests/"]}
    assert run.targets(task) == [{"file": "src/a.py", "symbol": "f"}]


def test_a_task_may_hollow_out_several_symbols():
    task = {
        "name": "t",
        "tests": ["tests/"],
        "targets": [
            {"file": "src/a.py", "symbol": "f"},
            {"file": "src/b.py", "symbol": "g"},
        ],
    }
    assert len(run.targets(task)) == 2
    assert {t["file"] for t in run.targets(task)} == {"src/a.py", "src/b.py"}


def test_a_task_with_neither_form_is_refused_loudly():
    """A typo in tasks.toml must not silently produce a task that removes nothing.

    That would excise nothing, pass the control check trivially... no: it would
    make the control check FAIL to fail, and the trial would be recorded with
    `control_fails_as_expected` false. Better to refuse at load.
    """
    with pytest.raises(KeyError):
        run.targets({"name": "t", "tests": ["tests/"]})


def test_targets_are_returned_in_a_stable_order():
    """Excision order decides which file the stub lands in first; keep it fixed."""
    task = {
        "name": "t",
        "tests": [],
        "targets": [
            {"file": "src/z.py", "symbol": "z"},
            {"file": "src/a.py", "symbol": "a"},
        ],
    }
    assert [t["symbol"] for t in run.targets(task)] == ["z", "a"]


# --- the environment handed to the client --------------------------------


def test_codex_gets_its_api_key_from_the_backend():
    """Codex profiles declare `env_key = "CODEX_API_KEY"` and the harness must
    supply it.

    It did not, for the life of the project. Every Codex row until 2026-08-28
    was produced with the variable exported in the operator's interactive
    shell, so the harness silently depended on ambient state it neither set nor
    recorded. Unattended, Codex exits in 0.7 s with "Missing environment
    variable: CODEX_API_KEY" and the row lands as a model failure, which is
    what it looks like and is not what it is.
    """
    env = run.agent_env(
        {
            "base_url": "http://127.0.0.1:8000",
            "model": "m",
            "auth_token": "tok",
            "context_tokens": 1,
        }
    )
    assert env["CODEX_API_KEY"] == "tok"


def test_the_hosted_reference_keeps_its_ambient_auth():
    """No base_url means the operator's real login. Do not inject a token."""
    env = run.agent_env({"model": "claude-opus-5"})
    assert "CODEX_API_KEY" not in env or env.get("CODEX_API_KEY") != "tok"
    assert env["ANTHROPIC_MODEL"] == "claude-opus-5"


def test_a_local_backend_never_leaks_a_real_anthropic_key():
    env = run.agent_env(
        {
            "base_url": "http://127.0.0.1:8000",
            "model": "m",
            "auth_token": "tok",
            "context_tokens": 1,
        }
    )
    assert "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_AUTH_TOKEN"] == "tok"


# --- recording the sampler on every backend (#36) -------------------------


def test_ollama_modelfile_parameters_are_recorded():
    """A modelfile that sets the sampler is the one case Ollama tells us about."""
    show = {"modelfile": "FROM x\nPARAMETER temperature 0.6\nPARAMETER top_p 0.95\n"}
    got = run.parse_ollama_show(show)
    assert got["sampling"] == {"temperature": "0.6", "top_p": "0.95"}
    assert got["sampling_source"] == "modelfile"


def test_an_empty_modelfile_records_that_defaults_apply_rather_than_nothing():
    """The case that caused #28 and #36.

    `ornith-1.5:35b` sets no PARAMETER lines, so Ollama's built-in defaults
    applied -- top_p 0.9, repeat_penalty 1.1 -- while llamacpp-up used 0.95 and
    never set repeat_penalty. Recording silence as silence is what made the
    confound invisible. An explicit "engine defaults, unrecorded" is a warning;
    a missing key is not.
    """
    got = run.parse_ollama_show({"modelfile": "FROM x\nTEMPLATE y\n"})
    assert got["sampling"] == {}
    # The string now also names WHICH engine rule applied: ollama#16471 changed
    # sampler precedence in 0.33.3, so a bare "engine defaults (unrecorded)"
    # described two different samplers either side of that release (#84).
    # It must still read as unrecorded -- naming the regime is not reading the
    # resolved values. test_ollama_sampler_regime.py pins the boundary.
    assert "unrecorded" in got["sampling_source"]
    assert got["sampling_source"].startswith("engine defaults")


def test_a_malformed_parameter_line_is_skipped_not_guessed():
    got = run.parse_ollama_show({"modelfile": "PARAMETER\nPARAMETER top_k 40\n"})
    assert got["sampling"] == {"top_k": "40"}


def test_a_show_response_without_a_modelfile_yields_nothing():
    assert run.parse_ollama_show({}) == {}
    assert run.parse_ollama_show({"modelfile": ""}) == {}


def test_ds4_records_that_its_sampler_is_unreported_not_that_it_is_absent():
    """ds4 exposes which parameters it accepts, never the values in force.

    `/v1/models` lists `supported_parameters`; there is no endpoint for the
    effective sampler, and the source carries two conflicting sets -- ds4.h has
    TOP_P 1.0 / MIN_P 0.05, ds4_cli.c overrides to 0.95 / 0.0 for CLI and agent
    paths. Which applies to the server cannot be settled by reading it.

    So the row records the ambiguity rather than a guess. #28 and #36 both came
    from a sampler nobody wrote down; a confident wrong value would be worse
    than an explicit unknown.
    """
    models = {
        "data": [
            {
                "id": "deepseek-v4-flash",
                "context_length": 100000,
                "supported_parameters": [
                    "temperature",
                    "top_p",
                    "top_k",
                    "min_p",
                    "seed",
                ],
            }
        ]
    }
    got = run.parse_ds4_models(models)
    assert got["sampling"] == {}
    assert got["sampling_source"] == "engine defaults (not reported by ds4)"
    assert set(got["accepts_sampling"]) == {
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "seed",
    }
    assert got["context_length"] == 100000


def test_a_models_response_from_something_else_is_ignored():
    """Ambiguity is ignored; a lone entry is not.

    This used to assert that a single `gpt-4` entry yielded {}, because the
    parser only accepted ids containing "ds4" or "deepseek". That rule is what
    #78 fixed: it also rejected `glm-5.3-flash` served by ds4, and every LM
    Studio model, so those rows carried an empty `servers` entry in silence.

    We choose the base_url, so a server answering with exactly one model is
    telling us what it serves and recording it is right. What must still be
    ignored is a response we cannot resolve to one model.
    """
    assert run.parse_ds4_models({"data": [{"id": "a"}, {"id": "b"}]}) == {}
    assert run.parse_ds4_models({}) == {}
    assert run.parse_ds4_models({"data": [{"id": "gpt-4"}]})["served_model_id"] == (
        "gpt-4"
    )


# --- a second target repository, in another language (#42) ----------------


def test_a_task_inherits_the_global_repo_when_it_names_none():
    """558 recorded rows name tasks defined against the global repo. Do not
    change what they mean."""
    cfg = {
        "repo": "~/git/gmail-archive",
        "base_commit": "56e55cc",
        "test_command": "uv run pytest -q",
    }
    got = run.task_target(cfg, {"name": "t"})
    assert got["repo"] == "~/git/gmail-archive"
    assert got["base_commit"] == "56e55cc"
    assert got["test_command"] == "uv run pytest -q"


def test_a_task_may_name_its_own_repo_and_test_command():
    cfg = {
        "repo": "~/git/gmail-archive",
        "base_commit": "56e55cc",
        "test_command": "uv run pytest -q",
    }
    task = {
        "name": "t",
        "repo": "~/git/monitor",
        "base_commit": "cbb85ca",
        "test_command": "swift test",
    }
    got = run.task_target(cfg, task)
    assert got["repo"] == "~/git/monitor"
    assert got["base_commit"] == "cbb85ca"
    assert got["test_command"] == "swift test"


def test_a_task_may_override_only_some_fields():
    cfg = {"repo": "~/git/a", "base_commit": "aaa", "test_command": "x"}
    got = run.task_target(cfg, {"name": "t", "base_commit": "bbb"})
    assert (got["repo"], got["base_commit"], got["test_command"]) == (
        "~/git/a",
        "bbb",
        "x",
    )


def test_excision_dispatches_on_file_extension():
    """Python goes through `ast`; Swift through the brace scanner. Choosing the
    wrong one does not crash, it finds nothing or cuts the wrong span."""
    assert run.exciser_for("src/gmail_archive/mbox.py").__name__ == "excise"
    assert run.exciser_for("Sources/MonitorCore/Downsample.swift").__name__ == "excise"
    # different modules, same function name -- check the module, not the name
    assert run.exciser_for("a.py").__module__ == "excise"
    assert run.exciser_for("a.swift").__module__ == "swift_excise"


def test_an_unsupported_language_is_refused_loudly():
    """Silently picking the Python parser for a .rs file would excise nothing
    and the control check would then pass, recording a broken task as valid."""
    with pytest.raises(ValueError, match="no excision support"):
        run.exciser_for("src/main.rs")


def test_the_summary_falls_back_to_stderr_when_stdout_is_empty():
    """Swift compile errors go to stderr and leave stdout empty.

    The first Swift failure recorded `pytest: "no output"` -- true, useless, and
    indistinguishable from a harness fault. The agent had written code that did
    not compile, which is a real and interesting failure, and the diagnosis was
    thrown away.
    """
    got = run.summarise_run("", "error: cannot find 'Buckets' in scope\n")
    assert "cannot find 'Buckets'" in got


def test_stdout_still_wins_when_present():
    got = run.summarise_run("17 passed in 0.07s\n", "some warning\n")
    assert got == "17 passed in 0.07s"


def test_both_empty_is_still_reported_rather_than_crashing():
    assert run.summarise_run("", "") == "no output"


def test_serving_ds4_root_is_a_git_tree_or_none() -> None:
    """Provenance must come from the running server, not from a default path.

    On 2026-08-31 the engine under test was a worktree (`~/git/ds4-main` at
    upstream/main) while the default pointed at the fork, so rows would have
    recorded an engine that was not running. This never raises: bad provenance
    is worth a warning, never a dead batch.
    """
    root = run.serving_ds4_root()
    assert root is None or (root / ".git").exists()


def test_serving_gguf_reports_a_real_file_or_none() -> None:
    """A row must be able to name the weights that produced it.

    `model` is a server-side alias: ds4 serves whatever GGUF it was started
    with regardless of the name requested, and on 2026-08-31 it advertised
    `glm-5.2*` while holding a GLM-5.3 file. Best-effort and never fatal.
    """
    info = run.serving_gguf()
    if info is None:
        return
    assert pathlib.Path(info["gguf_path"]).exists()
    assert info["gguf_bytes"] > 0
    assert info["server_argv"]


def test_metal_ceiling_is_an_int_or_none() -> None:
    """The ceiling decides whether a ~90 GiB model loads, and resets on reboot."""
    ceiling = run.metal_ceiling_mb()
    assert ceiling is None or isinstance(ceiling, int)


def test_prompts_doc_is_current() -> None:
    """PROMPTS.md must match what the harness actually sends.

    A published prompt that has drifted from the code is worse than none: it
    reports a benchmark nobody ran. Regenerate with
    `uv run python gen_prompts.py > PROMPTS.md`.
    """
    import subprocess

    here = pathlib.Path(__file__).parent
    doc = here / "PROMPTS.md"
    if not doc.exists():
        return
    fresh = subprocess.run(
        ["uv", "run", "python", "gen_prompts.py"],
        cwd=here,
        capture_output=True,
        text=True,
        check=False,
    )
    if fresh.returncode != 0:
        return
    assert fresh.stdout == doc.read_text(), (
        "PROMPTS.md is stale -- regenerate: uv run python gen_prompts.py > PROMPTS.md"
    )


# --- #67: the --dir contract -------------------------------------------------
#
# `opencode run` attaches to a persistent server, and that server holds its own
# working directory. Setting cwd= on the child process does nothing. There is no
# error when --dir is missing: the client runs, solves the task, and writes the
# answer into the server's directory, so the oracle reports a model failure.
# 64 published trials were wrong for this reason. These tests are the guard.


def _opencode_backend():
    return {"model": "qwen38fnq3", "opencode_model": "local/qwen"}


def test_opencode_is_told_where_to_work(tmp_path) -> None:
    argv = run.opencode_argv({"prompt": "go"}, _opencode_backend(), tmp_path)
    assert "--dir" in argv
    assert argv[argv.index("--dir") + 1] == str(tmp_path)


def test_opencode_refuses_to_run_without_a_worktree() -> None:
    """A missing --dir must fail loudly, not fall back to the server's cwd.

    The optional third parameter exists because the four argv builders share a
    signature. For OpenCode specifically, defaulting is the bug.
    """
    with pytest.raises(SystemExit, match="--dir"):
        run.opencode_argv({"prompt": "go"}, _opencode_backend(), None)


def test_the_directory_precedes_the_prompt() -> None:
    """--dir is a flag on `run`, so it cannot follow the positional prompt."""
    argv = run.opencode_argv({"prompt": "go"}, _opencode_backend(), "/w")
    assert argv.index("--dir") < argv.index("go")
    assert argv[-1] == "go"


def test_every_client_is_offered_the_worktree(tmp_path) -> None:
    """The builders share one signature, so a new client cannot silently
    lose the argument the way OpenCode silently ignored the cwd."""
    import inspect

    for name, (build, _parse) in run.CLIENTS.items():
        params = list(inspect.signature(build).parameters)
        assert params[2:3] == ["worktree"], f"{name} does not take a worktree"


# --- #74: Claude Code splits input across three fields --------------------
#
# `input_tokens` counts only the UNCACHED remainder. Against ds4 it read 0 for
# a prompt of 53,130 tokens, because everything was cache-created or cache-read.
# Cache reads are cheaper than fresh prefill but not free, and they are the
# whole subject of #64.


def test_prompt_tokens_sums_all_three_input_fields() -> None:
    usage = {
        "input_tokens": 0,
        "cache_creation_input_tokens": 26494,
        "cache_read_input_tokens": 26636,
    }
    assert run.claude_prompt_tokens(usage) == 53130


def test_a_plain_uncached_prompt_is_unchanged() -> None:
    assert run.claude_prompt_tokens({"input_tokens": 4200}) == 4200


def test_absent_usage_is_none_not_zero() -> None:
    """#29: absent must never be recorded as zero. A literal 0 is
    indistinguishable from a real measurement and gets averaged in."""
    assert run.claude_prompt_tokens({}) is None
    assert run.claude_prompt_tokens({"output_tokens": 100}) is None


def test_a_real_zero_is_still_zero() -> None:
    """Distinct from absent: the field was reported and said none."""
    assert run.claude_prompt_tokens({"input_tokens": 0}) == 0


def test_non_integer_values_are_ignored_not_summed() -> None:
    assert (
        run.claude_prompt_tokens({"input_tokens": None, "cache_read_input_tokens": 7})
        == 7
    )


def test_the_parser_keeps_the_split_for_later_analysis() -> None:
    """The total is what a row reports, but cache_read vs fresh prefill is the
    distinction #64 needs, so both are retained."""
    out = run.claude_parse(
        json.dumps(
            {
                "usage": {
                    "input_tokens": 0,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 90,
                    "output_tokens": 5,
                }
            }
        )
    )
    assert out["input_tokens"] == 100
    assert out["uncached_input_tokens"] == 0
    assert out["cache_read_input_tokens"] == 90


def test_opencode_is_the_default_client() -> None:
    """2026-09-01: OpenCode is the default and usually the only client.

    Sweeping every client multiplies machine time across an axis that is
    already measured -- 11.1s Aider, 39.5s OpenCode, 189.6s Claude Code on one
    server for the same task -- and whose winner is fixed by the project's
    premise. The interesting axes are models and engines.
    """
    src = (pathlib.Path(__file__).parent / "run.py").read_text()
    assert 'clients = args.client or ["opencode"]' in src


# --- /v1/models selection (#78) -------------------------------------------
#
# The old parser matched the entry whose id contained "ds4" or "deepseek".
# That silently returned {} for GLM-5.3 (served by ds4 as `glm-5.3-flash`) and
# for LM Studio, so those rows carried an empty `servers` entry and nobody
# noticed. These pin the selection rule instead of the substring.


def test_openai_models_selects_the_id_the_backend_declares():
    models = {
        "data": [
            {"id": "deepseek-v4-flash", "context_length": 100000},
            {"id": "glm-5.3-flash", "context_length": 131072},
        ]
    }
    got = run.parse_openai_models(models, {"model": "glm-5.3-flash"})
    assert got["served_model_id"] == "glm-5.3-flash"
    assert got["context_length"] == 131072


def test_openai_models_reads_glm_which_the_substring_match_dropped():
    """The exact regression: ds4 serving GLM used to yield {}."""
    models = {"data": [{"id": "glm-5.3-flash", "context_length": 131072}]}
    assert run.parse_openai_models(models, {"model": "glm-5.3-flash"})


def test_openai_models_falls_back_to_a_lone_entry():
    """A server with one model needs no declaration to be identifiable."""
    models = {"data": [{"id": "qwen3.8-flash-next-ud", "quantization": "Q3_K_XL"}]}
    got = run.parse_openai_models(models, {"model": "something-else"})
    assert got["served_model_id"] == "qwen3.8-flash-next-ud"
    assert got["quantization"] == "Q3_K_XL"


def test_openai_models_refuses_to_guess_between_several():
    """Ambiguity must return nothing, not the first row.

    Attributing one model's context length to another is worse than an empty
    record: an empty record is visibly absent, a wrong one is not.
    """
    models = {"data": [{"id": "a", "context_length": 1}, {"id": "b"}]}
    assert run.parse_openai_models(models, {"model": "c"}) == {}


def test_openai_models_keeps_lmstudio_build_fields():
    models = {
        "data": [
            {
                "id": "qwen3.8-flash-next-ud",
                "quantization": "Q3_K_XL",
                "arch": "qwen4exp",
                "publisher": "unsloth",
                "max_context_length": 131072,
            }
        ]
    }
    got = run.parse_openai_models(models, {"model": "qwen3.8-flash-next-ud"})
    assert got["arch"] == "qwen4exp"
    assert got["publisher"] == "unsloth"
    assert got["max_context_length"] == 131072


def test_openai_models_handles_nothing():
    assert run.parse_openai_models({}, {"model": "x"}) == {}
    assert run.parse_openai_models(None, {"model": "x"}) == {}
    assert run.parse_openai_models({"data": []}, {"model": "x"}) == {}


# --- retired backends -----------------------------------------------------


def test_retired_backends_are_documented_not_deleted():
    """LM Studio is retired but its config must survive.

    27 rows in results.jsonl name these backends. Deleting the blocks would
    leave those rows unexplainable -- which sampler, which context length,
    which deviations from LM Studio's defaults. The config is the record.
    """
    cfg = tomllib.loads((HERE / "tasks.toml").read_text())
    retired = {k: v for k, v in cfg["backend"].items() if v.get("retired")}
    assert "qwen38fnq3lms" in retired
    for name, backend in retired.items():
        assert backend.get("description"), name
        assert isinstance(backend["retired"], str) and backend["retired"], name


# --- one file, one machine (#20) ------------------------------------------


def test_foreign_hardware_is_detected():
    """The near-miss: a Linux run appended 13 rows to the Mac's results file."""
    rows = [
        {"env": {"arch": "arm64", "cpu": "Apple M5 Max"}},
        {"env": {"arch": "x86_64", "cpu": "AMD Ryzen 9 7900X 12-Core Processor"}},
    ]
    facts = {"arch": "arm64", "cpu": "Apple M5 Max"}
    assert results.foreign_hardware(rows, facts) == {
        ("x86_64", "AMD Ryzen 9 7900X 12-Core Processor")
    }


def test_same_hardware_is_not_foreign():
    rows = [{"env": {"arch": "arm64", "cpu": "Apple M5 Max"}}] * 3
    facts = {"arch": "arm64", "cpu": "Apple M5 Max"}
    assert results.foreign_hardware(rows, facts) == set()


def test_rows_that_do_not_say_are_not_treated_as_foreign():
    """979 existing rows predate machine_facts; they must not trip the guard.

    An unstamped row is unknown, not different. Refusing to run against the
    project's entire history would make the guard the first thing anyone
    disabled.
    """
    rows = [{"env": {"claude": "2.1.252"}}, {"env": {}}, {}]
    facts = {"arch": "arm64", "cpu": "Apple M5 Max"}
    assert results.foreign_hardware(rows, facts) == set()


def test_the_guard_is_inert_when_this_machine_is_unknown():
    """If we cannot identify ourselves we cannot accuse anyone else."""
    rows = [{"env": {"arch": "x86_64", "cpu": "Ryzen"}}]
    assert results.foreign_hardware(rows, {}) == set()


def test_dry_run_reports_a_script_task_without_crashing(tmp_path, monkeypatch):
    """--dry-run raised UnboundLocalError on every script task.

    `summary` is bound only inside the excision branch, and the dry-run log
    line named it unconditionally. Script tasks have no excision, so the one
    path that exists to verify a task before spending an agent on it crashed
    for the whole class -- since the class was added, because nobody ran a dry
    run on one.
    """
    source = (HERE / "run.py").read_text()
    assert 'summary if not is_script else "script task; no control to check"' in source


def test_tiered_backends_are_out_of_the_default_matrix():
    """Another machine's backends must not run here by default.

    Their config belongs in the repo -- it is the record of how the desktop's
    rows were made, and it lived in an untracked /tmp file before that. But a
    tier names hardware this machine is not, so it cannot be in the default
    matrix. `--backend` still selects one, on the machine that can serve it.
    """
    cfg = tomllib.loads((HERE / "tasks.toml").read_text())
    tiered = {k: v for k, v in cfg["backend"].items() if v.get("tier")}
    assert tiered, "expected the desktop tier to be configured"
    for name, backend in tiered.items():
        assert backend["tier"] == "desktop-3080ti", name
        assert backend.get("opencode_model"), name


def test_every_ollama_backend_declares_an_opencode_model():
    """A backend with no opencode_model cannot be run by our only client.

    #69: an undeclared model exits in 0.6s and the row records a model failure.
    The same shape as a missing backend block -- installed is not testable.
    """
    cfg = tomllib.loads((HERE / "tasks.toml").read_text())
    missing = sorted(
        name
        for name, b in cfg["backend"].items()
        if (b.get("base_url") or "").endswith(":11434")
        and not b.get("retired")
        and not b.get("opencode_model")
    )
    assert not missing, f"Ollama backends with no opencode_model: {missing}"


# --- the oracle's own bounds (#82) ----------------------------------------


def test_the_oracle_has_its_own_deadline():
    """It used to inherit the agent's --timeout of 1800s.

    A passing excision oracle finishes in about 0.1s. Handing it the agent's
    budget let a runaway test run hold 49 GB for half an hour before anything
    would have stopped it. Script tasks already used GATE_TIMEOUT; only the
    excision path did not.
    """
    assert run.ORACLE_TIMEOUT <= 300
    source = (HERE / "run.py").read_text()
    assert 'task["tests"], ORACLE_TIMEOUT, target["test_command"]' in source


def test_peak_rss_is_recorded_and_plausible():
    """A pathological implementation is invisible to a binary oracle.

    `gemma426` wrote a `scan` that made the oracle allocate 49 GB. Nothing in
    the row said so. Peak RSS is one call and turns that into a column.
    """
    got = run.peak_child_rss_gib()
    assert isinstance(got, float)
    assert 0.0 <= got < 1024.0


def test_peak_rss_uses_the_right_unit_per_platform(monkeypatch):
    """ru_maxrss is bytes on macOS and kilobytes on Linux.

    Getting this backwards would report 49 GB as 0.05, or 0.05 GB as 48,000 --
    either way the column would be useless in exactly the case it exists for.
    """

    class Fake:
        ru_maxrss = 2 * 1024**3  # 2 GiB expressed in bytes

    monkeypatch.setattr(run.resource, "getrusage", lambda who: Fake())
    monkeypatch.setattr(run.sys, "platform", "darwin")
    assert run.peak_child_rss_gib() == 2.0

    class FakeLinux:
        ru_maxrss = 2 * 1024**2  # 2 GiB expressed in kB

    monkeypatch.setattr(run.resource, "getrusage", lambda who: FakeLinux())
    monkeypatch.setattr(run.sys, "platform", "linux")
    assert run.peak_child_rss_gib() == 2.0


def test_the_results_path_is_an_option():
    """The hardware guard's error message told people to use --results.

    It did not exist. An error that gives impossible advice is worse than a
    bare refusal: it sends the reader looking for a flag, and the real fix --
    a per-machine results file (#85) -- stays invisible.
    """
    source = (HERE / "run.py").read_text()
    assert '"--results"' in source
    assert "results.write_row(r, args.results)" in source
    assert "results.trials(args.results)" in source
    # The guard must test the file being written, not a hardcoded default.
    assert "results.trials(RESULTS)" not in source


def test_a_missing_results_file_is_an_empty_history(tmp_path):
    """The first run on a new machine writes to a file that does not exist.

    load() raised FileNotFoundError, so the desktop's first real run crashed
    after its smoke gate had already passed -- the most expensive place to
    discover it.
    """
    assert results.load(tmp_path / "nope.jsonl") == []
    assert results.trials(tmp_path / "nope.jsonl") == []


def test_write_row_creates_the_machine_directory(tmp_path):
    """A per-machine run must need no setup step someone can forget."""
    target = tmp_path / "hardware" / "Some-Machine" / "results.jsonl"
    row = {
        "schema_version": 2,
        "task": "t",
        "backend": "b",
        "client": "opencode",
        "trial": 1,
    }
    results.write_row(dict(row), target)
    assert target.exists()
    assert len(results.load(target)) == 1


def test_odd_trials_run_the_backends_in_order():
    """#130: position bias is real, so the order must not be constant."""
    backends = {"a": {}, "b": {}, "c": {}}
    assert [n for n, _ in run.trial_order(backends, 1)] == ["a", "b", "c"]
    assert [n for n, _ in run.trial_order(backends, 3)] == ["a", "b", "c"]


def test_even_trials_reverse_the_backends():
    backends = {"a": {}, "b": {}, "c": {}}
    assert [n for n, _ in run.trial_order(backends, 2)] == ["c", "b", "a"]


def test_no_backend_holds_the_last_position_in_every_trial():
    """The bias lands on whichever arm always runs last. None may."""
    backends = {"a": {}, "b": {}}
    last = {run.trial_order(backends, t)[-1][0] for t in (1, 2, 3, 4)}
    assert last == {"a", "b"}


def test_ordering_does_not_drop_or_duplicate_a_backend():
    backends = {"a": {}, "b": {}, "c": {}, "d": {}}
    for trial in range(1, 6):
        names = [n for n, _ in run.trial_order(backends, trial)]
        assert sorted(names) == ["a", "b", "c", "d"]


def test_a_single_backend_is_unaffected_by_alternation():
    backends = {"only": {}}
    for trial in (1, 2, 3):
        assert [n for n, _ in run.trial_order(backends, trial)] == ["only"]


def test_the_stack_capture_records_which_ollama_was_installed(monkeypatch):
    """#84: 0.33.3 changed sampler precedence, so the build has to be on the
    row. The regime string names the rule; this names what applied it."""
    monkeypatch.setattr(
        run,
        "run",
        lambda *a, **k: types.SimpleNamespace(
            stdout="0.33.3\n", stderr="", returncode=0
        ),
    )
    env = run.capture_versions({"base_commit": "abc"}, {})
    assert env["ollama"] == "0.33.3"


def test_the_suite_runner_takes_the_machine_lock():
    """#133: a batch runs for hours and spends part of it with no server up,
    where a process scan truthfully reports "all clear"."""
    source = pathlib.Path(run.__file__).read_text()
    assert "acquire_lock" in source
    assert "atexit.register" in source
    assert "--no-lock" in source
