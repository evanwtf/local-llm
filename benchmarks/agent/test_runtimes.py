"""#324: which engine runtimes exist on which (OS, arch).

The fleet is three points: (Darwin, arm64) is the M5 Max, (Linux, aarch64) is
the DGX Spark, and (Linux, x86_64) is the 3080 Ti desktop. The cases below are
known absolute values for those three, not relationships between calls.
"""

from __future__ import annotations

import pathlib
import tomllib

import pytest
import runtimes

HERE = pathlib.Path(__file__).resolve().parent
MAC, DGX, X86 = runtimes.DARWIN_ARM64, runtimes.LINUX_AARCH64, runtimes.LINUX_X86_64


@pytest.mark.parametrize(
    "system, machine, want",
    [
        ("Darwin", "arm64", ("Darwin", "arm64")),
        ("Linux", "aarch64", ("Linux", "aarch64")),
        ("Linux", "x86_64", ("Linux", "x86_64")),
        # Linux on Apple-style naming, and the Windows-style x86 name.
        ("Linux", "arm64", ("Linux", "aarch64")),
        ("Linux", "AMD64", ("Linux", "x86_64")),
    ],
)
def test_the_host_key_is_normalized(system: str, machine: str, want: tuple) -> None:
    assert runtimes.host(system, machine) == want


@pytest.mark.parametrize(
    "engine, host, ways",
    [
        # MLX ships no Linux arm64 runtime (#293).
        ("mlx-serve", DGX, frozenset()),
        ("mlx-serve", MAC, frozenset({"source"})),
        # CUDA engines have no Metal build.
        ("vllm", MAC, frozenset()),
        ("sglang", MAC, frozenset()),
        ("vllm", DGX, frozenset({"prebuilt"})),
        ("vllm", X86, frozenset({"prebuilt"})),
        ("tensorfold", DGX, frozenset({"prebuilt"})),
        ("llama.cpp", MAC, frozenset({"source"})),
        ("llama.cpp", DGX, frozenset({"source"})),
        ("llama.cpp", X86, frozenset({"source"})),
        ("ollama", MAC, frozenset({"prebuilt"})),
        ("ollama", DGX, frozenset({"prebuilt"})),
        ("ollama", X86, frozenset({"prebuilt"})),
        ("ds4", MAC, frozenset({"source"})),
        ("ds4", DGX, frozenset({"source"})),
    ],
)
def test_known_runtimes_on_the_three_hosts(engine: str, host: tuple, ways) -> None:
    got = runtimes.lookup(engine, host)
    assert got is not None
    assert got.ways == ways


def test_an_unrecorded_pair_is_unknown_not_absent() -> None:
    """ds4 has never been built on the x86 desktop: unknown, so no refusal."""
    assert runtimes.lookup("ds4", X86) is None
    assert runtimes.lookup("no-such-engine", DGX) is None


@pytest.fixture(autouse=True)
def _real_platform_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """The root conftest lets every engine run anywhere; these tests need the
    real rules, and a local run."""
    monkeypatch.delenv("LOCAL_LLM_ANY_PLATFORM", raising=False)
    monkeypatch.delenv("LOCAL_LLM_SERVER_HOST", raising=False)


def _local(engine: str) -> dict:
    return {"engine": engine, "base_url": "http://127.0.0.1:8000/v1"}


def test_a_local_backend_with_no_runtime_here_is_refused() -> None:
    why = runtimes.refusal({"mlxbe": _local("mlx-serve")}, where=DGX)
    assert why is not None
    assert "mlxbe" in why
    assert "mlx-serve" in why
    assert "Linux aarch64" in why
    assert "#293" in why


def test_the_three_hosts_refuse_exactly_their_own_gaps() -> None:
    backends = {
        "a": _local("vllm"),
        "b": _local("mlx-serve"),
        "c": _local("llama.cpp"),
    }
    mac = runtimes.refusal(backends, where=MAC)
    assert mac is not None and "a (vllm:" in mac and "b (" not in mac
    dgx = runtimes.refusal(backends, where=DGX)
    assert dgx is not None and "b (mlx-serve:" in dgx and "a (" not in dgx
    # x86_64 Linux: vllm and llama.cpp run; MLX there is not recorded either way.
    assert runtimes.refusal(backends, where=X86) is None


def test_a_remote_or_hosted_backend_is_not_this_hosts_question() -> None:
    remote = {"engine": "mlx-serve", "base_url": "http://10.0.0.5:8000/v1"}
    hosted = {"model": "claude-opus-5"}
    assert runtimes.refusal({"r": remote, "h": hosted}, where=DGX) is None


def test_every_engine_in_tasks_toml_has_a_row() -> None:
    """A new engine must be placed in the table on purpose, even as unknown."""
    with (HERE / "tasks.toml").open("rb") as fh:
        backends = tomllib.load(fh)["backend"]
    engines = {str(b["engine"]).lower() for b in backends.values() if b.get("engine")}
    missing = sorted(engines - set(runtimes.RUNTIMES))
    assert not missing, f"add these engines to runtimes.RUNTIMES: {missing}"


def test_a_runtime_that_exists_names_how_it_is_obtained() -> None:
    for engine, hosts in runtimes.RUNTIMES.items():
        for host, rt in hosts.items():
            assert rt.ways <= {"prebuilt", "source"}, (engine, host)
            assert rt.note, (engine, host)


def test_the_suite_escape_and_a_remote_run_without_facts_skip_the_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backends = {"b": _local("mlx-serve")}
    assert runtimes.refusal(backends, where=DGX) is not None
    monkeypatch.setenv("LOCAL_LLM_SERVER_HOST", "10.0.0.1")
    assert runtimes.refusal(backends, where=DGX) is None
    monkeypatch.delenv("LOCAL_LLM_SERVER_HOST")
    monkeypatch.setenv("LOCAL_LLM_ANY_PLATFORM", "1")
    assert runtimes.refusal(backends, where=DGX) is None


def test_run_py_refuses_before_it_claims_the_machine() -> None:
    """The refusal is a gate in run.py's main, ahead of the lock."""
    text = (HERE / "run.py").read_text()
    gate = text.index(
        "_no_runtime = runtimes.refusal(backends, server_facts=server_facts)"
    )
    assert "raise SystemExit(_no_runtime)" in text[gate : gate + 200]
    assert gate < text.index("preflight.acquire_lock(")


def test_describe_names_how_each_engine_is_obtained_on_the_spark() -> None:
    lines = runtimes.describe(DGX)
    assert "mlx-serve: none (MLX ships no Linux arm64 runtime (#293))" in lines
    assert "vllm: prebuilt (released images and wheels for aarch64)" in lines
    assert "mtplx: unknown" in lines


# --- #324 follow-up: the server's host, the row field, the remote gate -------


def _facts(arch: str | None = "aarch64", os_: str | None = "Linux 6.17.0-nvidia"):
    """A server facts file as scripts/server_facts.py writes it (fake values)."""
    machine: dict = {"cpu": "Cortex-X925", "memory_gib": 121.7}
    if arch is not None:
        machine["arch"] = arch
    if os_ is not None:
        machine["os"] = os_
    return {
        "directory": "Cortex-X925-128GB-GB10-x2",
        "backend": "b",
        "facts": machine,
        "env": {"cluster": {"nodes": 2, "peer": {"host": "peer.example.invalid"}}},
    }


def _served(engine: str, **extra: str) -> dict:
    """A remote backend after remote.rewrite: its URL names the server."""
    return {
        "engine": engine,
        "base_url": "http://server.example.invalid:8000/v1",
        **extra,
    }


@pytest.mark.parametrize(
    "arch, os_, want",
    [
        ("aarch64", "Linux 6.17.0-nvidia", DGX),
        ("x86_64", "Linux 6.8.0-generic", X86),
        ("arm64", "Darwin 26.0.0", MAC),
    ],
)
def test_the_server_host_comes_from_the_facts(arch: str, os_: str, want) -> None:
    got, why = runtimes.server_host(_facts(arch, os_))
    assert got == want
    assert why is None


@pytest.mark.parametrize(
    "facts",
    [_facts(arch=None), _facts(os_=None), _facts(arch=""), {"directory": "x"}],
)
def test_facts_without_arch_or_os_give_no_host_and_a_reason(facts: dict) -> None:
    got, why = runtimes.server_host(facts)
    assert got is None
    assert why


@pytest.mark.parametrize(
    "engine, host, backend, want",
    [
        ("vllm", DGX, {}, "prebuilt"),
        ("tensorfold", DGX, {}, "prebuilt"),
        ("hyperflare", DGX, {}, "source"),
        ("llama.cpp", MAC, {}, "source"),
        ("ds4", X86, {}, "unknown"),
        ("mtplx", MAC, {}, "unknown"),
        ("mlx-serve", DGX, {}, "none"),
        # A declaration wins over the table: a recipe that compiles vLLM.
        ("vllm", DGX, {"engine_runtime": "source"}, "source"),
        ("tensorfold", DGX, {"engine_runtime": "unknown"}, "unknown"),
        # A `local/` tag says nothing by itself: no declaration, the table.
        ("vllm", DGX, {"image": "local/vllm-openai:main-abc123"}, "prebuilt"),
        # A declaration does not hide a pair with no runtime.
        ("mlx-serve", DGX, {"engine_runtime": "source"}, "none"),
    ],
)
def test_the_runtime_kind_is_a_fixed_word(
    engine: str, host: tuple, backend: dict, want: str
) -> None:
    assert runtimes.kind(engine, host, backend) == want


def test_an_undeclared_engine_with_two_ways_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The table cannot say which of two ways a backend took."""
    both = runtimes.Runtime(frozenset({"prebuilt", "source"}), "either")
    monkeypatch.setitem(runtimes.RUNTIMES, "twoway", {DGX: both})
    assert runtimes.kind("twoway", DGX, {}) == "unknown"
    assert runtimes.kind("twoway", DGX, {"engine_runtime": "source"}) == "source"


@pytest.mark.parametrize(
    "backend, want",
    [
        ({"engine": "vllm", "image": "vllm/vllm-openai:v0.34.0"}, False),
        ({"engine": "sglang", "image": "lmsysorg/sglang:nightly-x"}, False),
        ({"engine": "vllm"}, False),
        ({"engine": "vllm", "recipe_dir": "~/recipe"}, True),
        ({"engine": "vllm", "image": "vllm/vllm-openai:x", "recipe_dir": "~/r"}, True),
        ({"engine": "vllm", "image": "local/vllm-openai:x"}, True),
        ({"engine": "vllm", "image": "someone/their-vllm:v1"}, True),
    ],
)
def test_a_recipe_or_a_non_vendor_image_needs_a_declaration(
    backend: dict, want: bool
) -> None:
    assert runtimes.needs_declaration(backend) is want


def _live_backends() -> dict[str, dict]:
    with (HERE / "tasks.toml").open("rb") as fh:
        backends = tomllib.load(fh)["backend"]
    return {n: b for n, b in backends.items() if not b.get("retired")}


def test_every_live_backend_that_needs_it_declares_its_engine_runtime() -> None:
    """A recipe can build the engine from source or pull it, and the table
    cannot tell which: 33 two-node rows would have said `prebuilt` wrongly.
    Declare `prebuilt`, `source` or `unknown`, with a comment that cites the
    recipe line that shows it."""
    missing = sorted(
        n
        for n, b in _live_backends().items()
        if runtimes.needs_declaration(b) and "engine_runtime" not in b
    )
    assert not missing, f"declare engine_runtime in tasks.toml for: {missing}"


def test_every_declared_engine_runtime_is_an_allowed_word() -> None:
    with (HERE / "tasks.toml").open("rb") as fh:
        backends = tomllib.load(fh)["backend"]
    bad = {
        n: b["engine_runtime"]
        for n, b in backends.items()
        if "engine_runtime" in b and b["engine_runtime"] not in runtimes.DECLARED
    }
    assert not bad, bad


def test_a_local_row_records_the_kind_on_this_host() -> None:
    backends = {"a": _local("vllm"), "h": {"model": "claude-opus-5"}}
    env_for: dict[str, dict] = {"a": {}, "h": {}}
    runtimes.stamp(env_for, backends, server_facts=None, here=DGX)
    assert env_for["a"]["engine_runtime"] == "prebuilt"
    # A hosted backend has no engine, so no field.
    assert "engine_runtime" not in env_for["h"]


def test_a_remote_row_records_the_kind_on_the_servers_host() -> None:
    """The client is an x86_64 laptop; the server is a Spark. The row
    describes the server's engine, so it takes the server's host."""
    backends = {"b": _served("hyperflare")}
    env_for: dict[str, dict] = {"b": {}}
    runtimes.stamp(env_for, backends, server_facts=_facts(), here=X86)
    assert env_for["b"]["engine_runtime"] == "source"


def test_a_remote_row_on_facts_without_arch_records_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backends = {"b": _served("vllm")}
    env_for: dict[str, dict] = {"b": {}}
    with caplog.at_level("WARNING", logger="agent-bench"):
        runtimes.stamp(env_for, backends, server_facts=_facts(arch=None), here=DGX)
    assert "engine_runtime" not in env_for["b"]
    assert "arch" in caplog.text


def test_a_remote_run_refuses_an_engine_with_no_runtime_on_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#324 item 2: the client may be anything; the server's host decides."""
    monkeypatch.setenv("LOCAL_LLM_SERVER_HOST", "server.example.invalid")
    why = runtimes.refusal(
        {"b": _served("mlx-serve")}, where=MAC, server_facts=_facts()
    )
    assert why is not None
    assert "b (mlx-serve:" in why
    assert "Linux aarch64" in why
    assert "#293" in why


def test_a_remote_run_passes_an_engine_the_server_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_LLM_SERVER_HOST", "server.example.invalid")
    # The client is a Mac, where vllm has no runtime; the server is a Spark.
    assert (
        runtimes.refusal({"b": _served("vllm")}, where=MAC, server_facts=_facts())
        is None
    )


def test_a_remote_run_logs_an_unknown_pair_and_does_not_refuse(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LOCAL_LLM_SERVER_HOST", "server.example.invalid")
    with caplog.at_level("WARNING", logger="agent-bench"):
        why = runtimes.refusal(
            {"b": _served("ds4")}, server_facts=_facts("x86_64", "Linux 6.8.0")
        )
    assert why is None
    assert "unknown" in caplog.text
    assert "ds4" in caplog.text
    assert "Linux x86_64" in caplog.text


def test_a_local_run_logs_an_unknown_pair_and_does_not_refuse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="agent-bench"):
        assert runtimes.refusal({"a": _local("ds4")}, where=X86) is None
    assert "unknown" in caplog.text and "ds4" in caplog.text


@pytest.mark.parametrize("facts", [_facts(arch=None), _facts(os_=None)])
def test_a_remote_run_on_facts_without_arch_skips_the_check_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, facts: dict
) -> None:
    monkeypatch.setenv("LOCAL_LLM_SERVER_HOST", "server.example.invalid")
    with caplog.at_level("WARNING", logger="agent-bench"):
        why = runtimes.refusal({"b": _served("mlx-serve")}, server_facts=facts)
    assert why is None
    assert "skipped" in caplog.text


def test_the_suite_escape_skips_the_remote_check_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_LLM_SERVER_HOST", "server.example.invalid")
    monkeypatch.setenv("LOCAL_LLM_ANY_PLATFORM", "1")
    assert runtimes.refusal({"b": _served("mlx-serve")}, server_facts=_facts()) is None


def _two_node_backends() -> dict[str, dict]:
    with (HERE / "tasks.toml").open("rb") as fh:
        backends = tomllib.load(fh)["backend"]
    return {
        n: b
        for n, b in backends.items()
        if b.get("tier") == "gb10-spark-x2" and not b.get("retired")
    }


def test_every_two_node_backend_passes_the_gate_on_the_spark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each live two-node backend's engine has a runtime on Linux aarch64, so
    a two-node screen is not refused. Its row value is the backend's own
    declaration, or else the table's single way for an engine that needs no
    declaration; never a value nobody stated."""
    backends = _two_node_backends()
    assert backends, "no two-node backends in tasks.toml"
    monkeypatch.setenv("LOCAL_LLM_SERVER_HOST", "server.example.invalid")
    for name, b in backends.items():
        engine = str(b["engine"])
        assert runtimes.refusal({name: b}, server_facts=_facts()) is None, name
        got = runtimes.kind(engine, DGX, b)
        if "engine_runtime" in b:
            assert got == b["engine_runtime"], name
        else:
            assert not runtimes.needs_declaration(b), name
            rt = runtimes.lookup(engine, DGX)
            assert rt is not None and rt.ways == {got}, name


def test_hyperflare_two_node_is_a_source_build() -> None:
    """#1532 added the hyperflare backend; its CLI is a cargo build."""
    hf = {n: b for n, b in _two_node_backends().items() if b["engine"] == "hyperflare"}
    assert hf
    for b in hf.values():
        assert runtimes.kind("hyperflare", DGX, b) == "source"
