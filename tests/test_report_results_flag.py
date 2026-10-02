"""report.py --results reads a ledger other than the machine's default.

The two-Spark cluster's rows live in hardware/Cortex-X925-128GB-GB10-x2/,
but `results.default_path()` on the head node resolves to the single Spark's
ledger. Without the flag, every cluster readout needed a wrapper that patched
`results.default_path` in memory, and a command quoted in a recommendations
doc could not be rerun as written.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
sys.path.insert(0, str(HERE / "benchmarks" / "agent"))

import report


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[pathlib.Path]:
    got: list[pathlib.Path] = []

    def load(path: pathlib.Path):  # type: ignore[no-untyped-def]
        got.append(pathlib.Path(path))
        return [], 0, 0, 0

    monkeypatch.setattr(report.ledger_summary, "load", load)
    monkeypatch.setattr(report.provenance, "configure", lambda *a, **k: None)
    monkeypatch.setattr(report.provenance, "tee", lambda *a, **k: None)
    monkeypatch.setattr(report.provenance, "banner", lambda *a, **k: None)
    return got


def test_results_flag_names_the_ledger(
    seen: list[pathlib.Path], tmp_path: pathlib.Path
) -> None:
    ledger = tmp_path / "results.jsonl"
    ledger.write_text("")
    # No rows match, so main() reports "no trials" and returns 1.
    assert report.main(["--results", str(ledger), "--backend", "x"]) == 1
    assert seen == [ledger]


def test_without_the_flag_the_machine_default_is_read(
    seen: list[pathlib.Path],
) -> None:
    assert report.main(["--backend", "x"]) == 1
    assert seen == [pathlib.Path(report.RESULTS)]
