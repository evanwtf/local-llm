"""A void run's rows must not be publishable, and must not be rewritten."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import exclude_rows


def ledger(tmp_path, rows) -> pathlib.Path:
    p = tmp_path / "r.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


ROW = {
    "backend": "kimat",
    "started": "2026-09-04T21:00:00",
    "passed": True,
    "wall_seconds": 123.4,
}


def test_it_marks_the_window(tmp_path):
    p = ledger(tmp_path, [ROW])
    newly, already = exclude_rows.mark(
        p,
        backend="kimat",
        since="2026-09-04T20:57",
        until=None,
        reason="aborted sweep",
        apply=True,
    )
    assert (newly, already) == (1, 0)
    got = json.loads(p.read_text().strip())
    assert got["excluded"] is True and got["exclusion_reason"] == "aborted sweep"


def test_it_never_touches_a_measured_field(tmp_path):
    p = ledger(tmp_path, [ROW])
    exclude_rows.mark(
        p, backend="kimat", since=None, until=None, reason="x", apply=True
    )
    got = json.loads(p.read_text().strip())
    assert got["wall_seconds"] == 123.4 and got["passed"] is True


def test_a_row_outside_the_window_is_untouched(tmp_path):
    old = dict(ROW, started="2026-09-03T10:00:00")
    p = ledger(tmp_path, [old])
    newly, _ = exclude_rows.mark(
        p, backend="kimat", since="2026-09-04T20:57", until=None, reason="x", apply=True
    )
    assert newly == 0
    assert "excluded" not in json.loads(p.read_text().strip())


def test_another_backend_is_untouched(tmp_path):
    p = ledger(tmp_path, [dict(ROW, backend="other")])
    newly, _ = exclude_rows.mark(
        p, backend="kimat", since=None, until=None, reason="x", apply=True
    )
    assert newly == 0


def test_it_is_idempotent_and_keeps_the_first_reason(tmp_path):
    p = ledger(tmp_path, [ROW])
    exclude_rows.mark(
        p, backend="kimat", since=None, until=None, reason="first", apply=True
    )
    newly, already = exclude_rows.mark(
        p, backend="kimat", since=None, until=None, reason="second", apply=True
    )
    assert (newly, already) == (0, 1)
    assert json.loads(p.read_text().strip())["exclusion_reason"] == "first"


def test_a_dry_run_writes_nothing(tmp_path):
    p = ledger(tmp_path, [ROW])
    before = p.read_text()
    newly, _ = exclude_rows.mark(
        p, backend="kimat", since=None, until=None, reason="x", apply=False
    )
    assert newly == 1 and p.read_text() == before


def test_it_refuses_to_select_everything(tmp_path):
    p = ledger(tmp_path, [ROW])
    assert exclude_rows.main([str(p), "--reason", "x", "--apply"]) == 2


# ---------------------------------------------------------------------------
# The two holes a review found after this shipped: a forward-open window, and
# a non-atomic rewrite.


def test_apply_refuses_an_open_ended_window(tmp_path):
    """`--since` alone keeps matching rows that do not exist yet. Re-running
    the documented example after the next batch would exclude ITS rows."""
    p = ledger(tmp_path, [ROW])
    assert (
        exclude_rows.main(
            [str(p), "--since", "2026-09-04T20:57", "--reason", "x", "--apply"]
        )
        == 2
    )
    assert "excluded" not in json.loads(p.read_text().strip())


def test_a_dry_run_may_be_open_ended(tmp_path):
    """Reporting is safe; only writing needs the closed interval."""
    p = ledger(tmp_path, [ROW])
    assert (
        exclude_rows.main([str(p), "--since", "2026-09-04T20:57", "--reason", "x"]) == 0
    )


def test_until_is_exclusive(tmp_path):
    p = ledger(tmp_path, [ROW])  # started 21:00:00
    newly, _ = exclude_rows.mark(
        p, backend=None, since=None, until="2026-09-04T21:00:00", reason="x", apply=True
    )
    assert newly == 0


def test_it_refuses_while_a_benchmark_is_appending(tmp_path, monkeypatch):
    """run.py appends to this file; a row written between the read and the
    write would be destroyed."""
    monkeypatch.setattr(exclude_rows, "_harness_running", lambda: True)
    p = ledger(tmp_path, [ROW])
    code = exclude_rows.main(
        [
            str(p),
            "--backend",
            "kimat",
            "--until",
            "2026-09-05T00:00",
            "--reason",
            "x",
            "--apply",
        ]
    )
    assert code == 2
    assert "excluded" not in json.loads(p.read_text().strip())


def test_the_write_leaves_no_temp_file_behind(tmp_path):
    p = ledger(tmp_path, [ROW])
    exclude_rows.mark(
        p, backend="kimat", since=None, until=None, reason="x", apply=True
    )
    assert list(tmp_path.glob("*.tmp")) == []
    assert json.loads(p.read_text().strip())["excluded"] is True
