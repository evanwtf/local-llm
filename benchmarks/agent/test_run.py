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
import os
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


def test_the_resolved_sampler_is_read_from_model_info(monkeypatch):
    """#84: /api/show carries the GGUF's own KVs, so the numbers actually in
    force from ollama 0.33.3 on are readable without a GGUF path."""
    show = {
        "modelfile": "FROM /blobs/sha256-abc\n",
        "model_info": {
            "general.architecture": "llama",
            "general.sampling.temp": 1,
            "general.sampling.top_k": 20,
            "general.sampling.top_p": 0.95,
        },
    }
    got = run.parse_ollama_show(show, ollama_version="0.33.3")
    assert got["sampling"] == {"temp": 1, "top_k": 20, "top_p": 0.95}
    assert "model-authored" in got["sampling_source"]
    assert "unrecorded" not in got["sampling_source"]


def test_before_0_33_3_the_declared_sampler_is_named_as_overridden():
    """Pre-0.33.3 the built-ins win, so recording the declared numbers as if
    they ran would be a lie about that row (#84)."""
    show = {
        "modelfile": "FROM /blobs/sha256-abc\n",
        "model_info": {"general.sampling.top_p": 0.95},
    }
    got = run.parse_ollama_show(show, ollama_version="0.33.2")
    assert got["sampling"] == {}
    assert "unrecorded" in got["sampling_source"]
    assert "overridden" in got["sampling_source"]
    assert "0.95" in got["sampling_source"]


def test_a_modelfile_parameter_still_outranks_the_gguf():
    """Precedence 2 beats precedence 3 either side of the boundary."""
    show = {
        "modelfile": "FROM /x\nPARAMETER top_p 0.9\n",
        "model_info": {"general.sampling.top_p": 0.95},
    }
    got = run.parse_ollama_show(show, ollama_version="0.33.3")
    assert got["sampling"] == {"top_p": "0.9"}
    assert got["sampling_source"] == "modelfile"


def test_a_model_that_declares_nothing_says_so():
    show = {"modelfile": "FROM /x\n", "model_info": {"general.architecture": "llama"}}
    got = run.parse_ollama_show(show, ollama_version="0.33.3")
    assert got["sampling"] == {}
    assert "declares no sampler" in got["sampling_source"]


def test_model_declared_sampling_strips_the_prefix_and_ignores_the_rest():
    assert run.model_declared_sampling(
        {"model_info": {"general.sampling.top_k": 20, "general.architecture": "x"}}
    ) == {"top_k": 20}
    assert run.model_declared_sampling({}) == {}
    assert run.model_declared_sampling({"model_info": None}) == {}


def test_absent_model_info_is_not_read_as_declaring_nothing():
    """#84: "we could not see" and "it declares nothing" are different facts,
    and the pre-existing regime test was right to protect the distinction."""
    got = run.parse_ollama_show({"modelfile": "FROM x\n"}, ollama_version="0.33.3")
    assert "model_info absent" in got["sampling_source"]
    assert "declares no sampler" not in got["sampling_source"]


def test_prepare_env_reports_a_missing_pyproject_rather_than_pretending(tmp_path):
    """A checkout with no project is a runnable trial; the row must say so."""
    got = run.prepare_env(tmp_path)
    assert got["env_prepared"] is False
    assert "pyproject" in got["env_reason"]


def test_prepare_env_records_success(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    monkeypatch.setattr(
        run.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    got = run.prepare_env(tmp_path)
    assert got == {"env_prepared": True, "env_reason": "uv sync --frozen"}


def test_a_stale_lockfile_is_reported_not_silently_resolved(tmp_path, monkeypatch):
    """--frozen refuses on a stale lock. Falling back to a resolve would
    install versions the lockfile does not pin and change the environment
    under the comparison."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    monkeypatch.setattr(
        run.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=2, stdout="", stderr="error: lockfile is out of date\n"
        ),
    )
    got = run.prepare_env(tmp_path)
    assert got["env_prepared"] is False
    assert "out of date" in got["env_reason"]


def test_prepare_env_never_raises(tmp_path, monkeypatch):
    """A trial that already cost twenty minutes must not die on setup."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")

    def boom(*a, **k):
        raise OSError("uv is not installed")

    monkeypatch.setattr(run.subprocess, "run", boom)
    got = run.prepare_env(tmp_path)
    assert got["env_prepared"] is False
    assert "uv is not installed" in got["env_reason"]


def test_the_flag_exists_and_defaults_to_preparing():
    """Preparing is the new default; the old behaviour needs asking for."""
    source = pathlib.Path(run.__file__).read_text()
    assert "--no-prepare-env" in source
    assert "prepare_env_first=True" in source


# ---------------------------------------------------------------------------
# #112: a transcript that is overwritten is evidence that cannot be recovered.


def _save(tmp_path, name, stdout):
    result: dict = {}
    run.save_transcript(tmp_path, name, stdout, "", result)
    return result


def test_a_second_sweep_does_not_destroy_the_first_transcript(tmp_path):
    """#112's pre-remedy transcripts were lost exactly this way: later sweeps
    wrote the same filenames into the same directory, and the before-side of
    the only question the issue asks became unrecoverable."""
    first = _save(tmp_path, "mbox-scan-1", '{"a": 1}')
    second = _save(tmp_path, "mbox-scan-1", '{"b": 2}')
    assert pathlib.Path(first["client_log"]).read_text() == '{"a": 1}'
    assert pathlib.Path(second["client_log"]).read_text() == '{"b": 2}'
    assert first["client_log"] != second["client_log"]


def test_the_rewritten_transcript_says_it_was_displaced(tmp_path):
    _save(tmp_path, "mbox-scan-1", "one")
    second = _save(tmp_path, "mbox-scan-1", "two")
    assert second.get("client_log_collision") is True


def test_writing_identical_content_twice_does_not_multiply_files(tmp_path):
    """A re-run that produced the same bytes is not new evidence."""
    _save(tmp_path, "mbox-scan-1", "same")
    _save(tmp_path, "mbox-scan-1", "same")
    assert len(list(tmp_path.glob("mbox-scan-1*.jsonl"))) == 1


def test_a_third_collision_gets_its_own_name(tmp_path):
    _save(tmp_path, "t", "a")
    _save(tmp_path, "t", "b")
    third = _save(tmp_path, "t", "c")
    assert pathlib.Path(third["client_log"]).read_text() == "c"
    assert len(list(tmp_path.glob("t*.jsonl"))) == 3


def test_the_first_write_is_not_marked_as_a_collision(tmp_path):
    assert _save(tmp_path, "t", "a").get("client_log_collision") is not True


# ---------------------------------------------------------------------------
# A live run owns its stashed repositories. Restoring them under it destroys
# the real checkout -- measured 2026-09-04, and it took the operator's repo
# with it.


def _marker(tmp_path, monkeypatch, pid: int):
    export, real = tmp_path / "repo", tmp_path / "repo-real"
    real.mkdir()
    (real / "keep.txt").write_text("the real checkout")
    export.mkdir()
    (export / "export.txt").write_text("the excised export")
    marker = tmp_path / "stash.json"
    marker.write_text(
        json.dumps({"moved": [{"export": str(export), "real": str(real)}], "pid": pid})
    )
    monkeypatch.setattr(run, "STASH_MARKER", marker)
    return export, real, marker


def test_a_stash_owned_by_a_live_other_process_is_not_restored(tmp_path, monkeypatch):
    """preflight calls this. Running it during a live batch used to rmtree the
    export and unstash the real repo underneath the running harness, which
    then destroyed the real checkout on its next trial."""
    export, real, marker = _marker(tmp_path, monkeypatch, pid=1)  # pid 1 is alive
    assert run.restore_targets() == []
    assert real.exists(), "the real checkout must stay stashed"
    assert (export / "export.txt").exists(), "the export must not be removed"
    assert marker.exists(), "the marker belongs to the live owner"


def test_a_stash_owned_by_a_dead_process_is_restored(tmp_path, monkeypatch):
    """The case this function exists for: a run killed mid-batch."""
    export, real, marker = _marker(tmp_path, monkeypatch, pid=2_000_000)
    assert run.restore_targets() == [export.name]
    assert (export / "keep.txt").exists()
    assert not real.exists()
    assert not marker.exists()


def test_our_own_stash_is_restored(tmp_path, monkeypatch):
    """atexit runs inside the owning process; it must still restore."""
    export, real, marker = _marker(tmp_path, monkeypatch, pid=os.getpid())
    assert run.restore_targets() == [export.name]
    assert (export / "keep.txt").exists()


def test_a_marker_with_no_pid_is_restored(tmp_path, monkeypatch):
    """Markers written before the pid was recorded must stay recoverable."""
    export, real, marker = _marker(tmp_path, monkeypatch, pid=os.getpid())
    marker.write_text(json.dumps({"moved": json.loads(marker.read_text())["moved"]}))
    assert run.restore_targets() == [export.name]


def test_a_kill_between_the_rename_and_the_marker_is_recoverable(tmp_path, monkeypatch):
    """The marker used to be written after every rename, leaving a window where
    the repositories were moved and nothing on disk said so."""
    marker = tmp_path / "stash.json"
    monkeypatch.setattr(run, "STASH_MARKER", marker)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.txt").write_text("real")
    seen: list[bool] = []
    original = pathlib.Path.rename

    def watched(self, target):
        # At the moment of the rename, the map must already exist on disk.
        seen.append(marker.exists())
        return original(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", watched)
    run.stash_targets([(str(repo), "abc1234")])
    assert seen == [True], "the marker must be written before the rename"


def test_a_partial_restore_keeps_the_marker_for_the_rest(tmp_path, monkeypatch):
    """A missing -real is exactly when the map matters; deleting it there makes
    the un-restored entries unrecoverable."""
    marker = tmp_path / "stash.json"
    good_export, good_real = tmp_path / "a", tmp_path / "a-real"
    good_real.mkdir()
    gone_export, gone_real = tmp_path / "b", tmp_path / "b-real"
    marker.write_text(
        json.dumps(
            {
                "moved": [
                    {"export": str(good_export), "real": str(good_real)},
                    {"export": str(gone_export), "real": str(gone_real)},
                ],
                "pid": 2_000_000,
            }
        )
    )
    monkeypatch.setattr(run, "STASH_MARKER", marker)
    assert run.restore_targets() == [good_export.name]
    assert marker.exists(), "the unrestored entry must stay on the map"
    left = json.loads(marker.read_text())["moved"]
    assert [m["export"] for m in left] == [str(gone_export)]


# --- where one_trial looks for the parked checkout (#54, 846ec66) -----------


def _tiny_repo(tmp_path):
    """A real one-commit repo: build_checkout archives from it with git."""
    repo = tmp_path / "monitor"
    repo.mkdir()
    (repo / "mod.py").write_text("def target_fn():\n    return 1\n")
    run.git(["init", "-q", "-b", "main"], repo)
    run.git(["add", "-A"], repo)
    run.git(
        [
            "-c",
            "user.email=bench@local",
            "-c",
            "user.name=bench",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        repo,
    )
    return repo, run.git(["rev-parse", "HEAD"], repo)


def test_parked_checkout_finds_the_stash_root_copy(tmp_path):
    """846ec66 moved the parking lot to STASH_ROOT. Anything that still looks
    only for the legacy <name>-real sibling reports nothing parked."""
    repo = tmp_path / "monitor"
    parked = run.stash_path(repo)
    parked.mkdir(parents=True)
    assert run.parked_checkout(repo) == parked


def test_parked_checkout_finds_the_legacy_sibling(tmp_path):
    legacy = run.legacy_stash_path(tmp_path / "monitor")
    legacy.mkdir()
    assert run.parked_checkout(tmp_path / "monitor") == legacy


def test_parked_checkout_is_none_when_nothing_is_parked(tmp_path):
    """The third state guarded_repo() cannot express: with nothing parked the
    trial builds from the configured path into a fresh workdir."""
    assert run.parked_checkout(tmp_path / "monitor") is None


def test_one_trial_builds_the_trial_from_the_parked_checkout(tmp_path):
    """The bug this audit exists for: one_trial looked only for the legacy
    <name>-real sibling. Under the stash root it saw nothing parked and built
    the trial from the configured path -- which a live batch has emptied by
    parking the real checkout -- so build_checkout died on every repo trial.
    846ec66 had never executed a trial when this was found: the running batch
    predates it."""
    repo, commit = _tiny_repo(tmp_path)
    run.stash_targets([(str(repo), commit)])
    assert run.stash_path(repo).exists() and not repo.exists()
    row = run.one_trial(
        {"repo": str(repo), "base_commit": commit},
        {
            "name": "seam",
            "file": "mod.py",
            "symbol": "target_fn",
            "tests": [],
            "test_command": "false",
        },
        "seam",
        {"model": "stub", "context_tokens": 1},
        trial=1,
        workdir=tmp_path / "work",
        timeout=10,
        dry_run=True,
        client="claude",
        prepare_env_first=False,
    )
    assert row["removed_symbols"] == ["target_fn"]
    assert row["control_fails_as_expected"] is True


def test_the_sandbox_denies_every_parking_spot(tmp_path):
    """The real checkout keeps full history wherever it is parked, so the deny
    list has to name every parking spot -- not only the legacy -real siblings
    -- plus the two files that say where the parking is."""
    worktree = tmp_path / "work" / "seam"
    worktree.mkdir(parents=True)
    repo = tmp_path / "monitor"
    _profile, denied = run.sandbox_profile(worktree, repo)
    assert str(run.STASH_ROOT) in denied
    assert str(run.legacy_stash_path(repo)) in denied
    assert str(run.STASH_MARKER) in denied
    assert str(run.STASH_NOTICE) in denied
