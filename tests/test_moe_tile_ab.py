"""The MoE tensor-tile A/B driver's refusals, without running a measurement (#328).

The driver measures a prefill claim that trades against #149 drift, so its
guards matter: an odd rep count biases the result, two equal arms are one arm
wearing two labels, and a knob that changes nothing must not publish a
prefill difference that is only noise. These test the refusals and the argv,
not a real run.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import moe_tile_ab as m


def test_bench_argv_includes_ple_only_when_given() -> None:
    g, p, csv = pathlib.Path("m.gguf"), pathlib.Path("p.txt"), pathlib.Path("o.csv")
    with_ple = m.bench_argv(
        g,
        p,
        csv,
        pathlib.Path("ple.gguf"),
        ctx_start=8192,
        ctx_max=8192,
        step=2048,
        gen=0,
    )
    assert "--ple" in with_ple and "ple.gguf" in with_ple
    assert with_ple[with_ple.index("--gen-tokens") + 1] == "0"
    without = m.bench_argv(
        g, p, csv, None, ctx_start=8192, ctx_max=8192, step=2048, gen=0
    )
    assert "--ple" not in without


def test_probe_argv_is_greedy() -> None:
    argv = m.probe_argv(pathlib.Path("m.gguf"), pathlib.Path("ple.gguf"))
    assert argv[argv.index("--temp") + 1] == "0"
    assert "--ple" in argv


def test_generated_text_drops_header_and_ds4_diagnostics(tmp_path) -> None:
    """The `#` arm header and `ds4:` lines must go, or identical generations
    read as different (the header carries =2 vs =5)."""
    log = tmp_path / "probe.log"
    log.write_text(
        "# probe: a DS4_QWEN4_MOE_MM_NAX=2 ./ds4 ...\n"
        "ds4: loading\nhello world\nds4: prefill 100 t/s\nmore text\n"
    )
    assert m.generated_text(log) == "hello world\nmore text"


def test_refuses_odd_reps() -> None:
    with pytest.raises(m.Refusing, match="lead equally"):
        m.sweep(
            pathlib.Path("/nonexistent"),
            pathlib.Path("m.gguf"),
            None,
            a_value="2",
            b_value="5",
            reps=3,
            out=pathlib.Path("/tmp/x"),
            ctx_start=8192,
            ctx_max=8192,
            step=2048,
            gen=0,
            owner_pid=1,
        )


def test_refuses_equal_arms() -> None:
    with pytest.raises(m.Refusing, match="one arm wearing two labels"):
        m.sweep(
            pathlib.Path("/nonexistent"),
            pathlib.Path("m.gguf"),
            None,
            a_value="2",
            b_value="2",
            reps=4,
            out=pathlib.Path("/tmp/x"),
            ctx_start=8192,
            ctx_max=8192,
            step=2048,
            gen=0,
            owner_pid=1,
        )


def test_refuses_missing_binary() -> None:
    with pytest.raises(m.Refusing, match="missing or not executable"):
        m.sweep(
            pathlib.Path("/nonexistent"),
            pathlib.Path("m.gguf"),
            None,
            a_value="2",
            b_value="5",
            reps=4,
            out=pathlib.Path("/tmp/x"),
            ctx_start=8192,
            ctx_max=8192,
            step=2048,
            gen=0,
            owner_pid=1,
        )


def test_check_eligible_refuses_q4_0(monkeypatch) -> None:
    """A Q4_0 (type 2) expert pack is inert for the nax tiles -- refuse it."""
    monkeypatch.setattr(m, "expert_type", lambda gguf: (2, "type 2"))
    with pytest.raises(m.Refusing, match="inert for this pack"):
        m.check_eligible(pathlib.Path("q40.gguf"))


def test_check_eligible_passes_q4_k(monkeypatch) -> None:
    """A Q4_K (type 12) expert pack does select the nax tiles."""
    monkeypatch.setattr(m, "expert_type", lambda gguf: (12, "Q4_K"))
    assert m.check_eligible(pathlib.Path("q4k.gguf")) == (12, "Q4_K")


def test_probe_arms_does_not_refuse_on_identical(monkeypatch, tmp_path) -> None:
    """Identical greedy text is expected for a precision knob, not a refusal."""
    monkeypatch.setattr(m, "probe", lambda label, value, **kw: "same text")
    a, b = m.probe_arms(
        "2", "5", out=tmp_path, tree=tmp_path, gguf=pathlib.Path("m.gguf"), ple=None
    )
    assert a == b == "same text"


def test_probe_arms_refuses_empty_output(monkeypatch, tmp_path) -> None:
    """No generated text means the model did not run -- that is still a failure."""
    monkeypatch.setattr(m, "probe", lambda label, value, **kw: "")
    with pytest.raises(m.Refusing, match="did not run"):
        m.probe_arms(
            "2", "5", out=tmp_path, tree=tmp_path, gguf=pathlib.Path("m.gguf"), ple=None
        )
