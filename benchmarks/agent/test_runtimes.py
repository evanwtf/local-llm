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


def test_the_suite_escape_and_a_remote_run_skip_the_refusal(
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
    gate = text.index("_no_runtime = runtimes.refusal(backends)")
    assert "raise SystemExit(_no_runtime)" in text[gate : gate + 200]
    assert gate < text.index("preflight.acquire_lock(")


def test_describe_names_how_each_engine_is_obtained_on_the_spark() -> None:
    lines = runtimes.describe(DGX)
    assert "mlx-serve: none (MLX ships no Linux arm64 runtime (#293))" in lines
    assert "vllm: prebuilt (released images and wheels for aarch64)" in lines
    assert "mtplx: unknown" in lines
