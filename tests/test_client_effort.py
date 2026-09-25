"""scripts/client_effort.py: per-client turns and tokens for a client A/B (#707)."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import client_effort


def _row(task: str, client: str, turns: int | None, out: int | None, **kw) -> dict:
    return {
        "task": task,
        "client": client,
        "backend": kw.get("backend", "qwen36coding"),
        "batch": kw.get("batch", "0924-707"),
        "num_turns": turns,
        "output_tokens": out,
        "input_tokens": kw.get("peak"),
    }


def test_select_keeps_one_backend_and_batch() -> None:
    rows = [
        _row("a", "unreal", 5, 100),
        _row("a", "unreal", 5, 100, backend="other"),
        _row("a", "unreal", 5, 100, batch="old"),
    ]
    assert len(client_effort.select(rows, "qwen36coding", "0924-707")) == 1
    assert len(client_effort.select(rows, "qwen36coding", None)) == 2


def test_medians_are_real_values_and_count_what_they_used() -> None:
    rows = [
        _row("a", "unreal", 4, 100, peak=900),
        _row("a", "unreal", 6, 300, peak=1500),
        _row("a", "unreal", None, None),  # a timeout parses nothing
    ]
    m = client_effort.medians(rows)
    assert m["num_turns"] == (5, 2)
    assert m["output_tokens"] == (200, 2)
    assert m["input_tokens"] == (1200, 2)


def test_a_missing_value_is_counted_not_hidden() -> None:
    assert client_effort._cell((None, 0), 3) == "- (0 of 3)"
    assert client_effort._cell((5.0, 2), 3) == "5 (2 of 3)"
    assert client_effort._cell((1234.0, 3), 3) == "1,234"


def test_main_reads_a_ledger(tmp_path, caplog) -> None:
    ledger = tmp_path / "results.jsonl"
    rows = [
        _row("a", "opencode", 10, 500, peak=2000),
        _row("a", "unreal", 4, 200, peak=1000),
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))
    caplog.set_level("INFO")
    assert client_effort.main([str(ledger), "--backend", "qwen36coding"]) == 0
    text = caplog.text
    assert "| `a` | opencode | 10 | 500 | 2,000 |" in text
    assert "| `a` | unreal | 4 | 200 | 1,000 |" in text


def test_main_refuses_an_empty_selection(tmp_path) -> None:
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(json.dumps(_row("a", "unreal", 4, 200)) + "\n")
    assert client_effort.main([str(ledger), "--backend", "nope"]) == 1
