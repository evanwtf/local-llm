"""#208: every A/B driver refuses a reused outdir before it reaches sweep().

sweep() takes the lock, so a refusal inside it already cost the machine. The
check is in main(), beside the odd-rep refusal, and these tests make sweep()
explode to prove the refusal comes first.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
for sub in ("scripts/lib", "scripts", "benchmarks/agent"):
    sys.path.insert(0, str(REPO / sub))

import decode_ab
import decode_ab_engine
import metal_knob_ab


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("REUSE_OUTDIR", "REPS", "ALLOW_ODD_REPS", "PREFILL_CHUNK"):
        monkeypatch.delenv(var, raising=False)


def _reused(tmp_path: pathlib.Path) -> pathlib.Path:
    out = tmp_path / "out"
    out.mkdir()
    (out / "on-rep1.csv").write_text("x\n")
    return out


def _argv(driver, tmp_path: pathlib.Path, out: pathlib.Path) -> list[str]:
    if driver is decode_ab:
        return ["a", str(tmp_path / "a.gguf"), "b", str(tmp_path / "b.gguf"), str(out)]
    if driver is decode_ab_engine:
        return ["a", str(tmp_path / "A"), "b", str(tmp_path / "B"), "m.gguf", str(out)]
    return ["q4-mpp-payload-reuse", "1", "0", str(tmp_path), "m.gguf", str(out)]


DRIVERS = [decode_ab, decode_ab_engine, metal_knob_ab]


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda d: d.__name__)
def test_a_reused_outdir_is_refused_before_sweep(driver, tmp_path, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("a refused run must not reach sweep() or the lock")

    monkeypatch.setattr(driver, "sweep", explode)
    out = _reused(tmp_path)
    assert driver.main(_argv(driver, tmp_path, out)) != 0
    assert (out / "on-rep1.csv").read_text() == "x\n"


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda d: d.__name__)
def test_reuse_outdir_reaches_sweep(driver, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(driver, "sweep", lambda *a, **k: calls.append(1) or 0)
    out = _reused(tmp_path)
    argv = [*_argv(driver, tmp_path, out), "--reuse-outdir"]
    assert driver.main(argv) == 0
    assert calls == [1]


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda d: d.__name__)
def test_a_fresh_outdir_reaches_sweep(driver, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(driver, "sweep", lambda *a, **k: calls.append(1) or 0)
    assert driver.main(_argv(driver, tmp_path, tmp_path / "new")) == 0
    assert calls == [1]
