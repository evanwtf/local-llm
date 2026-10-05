"""A void run's rows must not be publishable, and must not be rewritten."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import exclude_rows


def ledger(tmp_path, rows) -> pathlib.Path:
    p = tmp_path / "r.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


ROW = {
    "backend": "kimat",
    "started": "2026-09-04T21:00:00-0400",
    "passed": True,
    "wall_seconds": 123.4,
}


def test_it_marks_the_window(tmp_path):
    p = ledger(tmp_path, [ROW])
    newly, already = exclude_rows.mark(
        p,
        backend="kimat",
        since="2026-09-04T20:57-0400",
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
    old = dict(ROW, started="2026-09-03T10:00:00-0400")
    p = ledger(tmp_path, [old])
    newly, _ = exclude_rows.mark(
        p,
        backend="kimat",
        since="2026-09-04T20:57-0400",
        until=None,
        reason="x",
        apply=True,
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
            [str(p), "--since", "2026-09-04T20:57-0400", "--reason", "x", "--apply"]
        )
        == 2
    )
    assert "excluded" not in json.loads(p.read_text().strip())


def test_a_dry_run_may_be_open_ended(tmp_path):
    """Reporting is safe; only writing needs the closed interval."""
    p = ledger(tmp_path, [ROW])
    assert (
        exclude_rows.main([str(p), "--since", "2026-09-04T20:57-0400", "--reason", "x"])
        == 0
    )


def test_until_is_exclusive(tmp_path):
    p = ledger(tmp_path, [ROW])  # started 21:00:00
    newly, _ = exclude_rows.mark(
        p,
        backend=None,
        since=None,
        until="2026-09-04T21:00:00-0400",
        reason="x",
        apply=True,
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
            "2026-09-05T00:00-0400",
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


# ---------------------------------------------------------------------------
# Review of b7a366b. Finding 3: the window compared timestamps as strings, so
# a row and a bound in different offsets landed on the wrong side of it.
# Finding 2: a failed process check read as "no benchmark running".


def test_a_window_selects_by_instant_across_offsets(tmp_path):
    """12:30 UTC is 08:30 New York: inside 08:00-09:00 -0400.

    As strings, "2026-10-05T12:30:00+0000" sorts after the "...T09:00:00-0400"
    bound, so the void row stayed publishable.
    """
    p = ledger(tmp_path, [dict(ROW, started="2026-10-05T12:30:00+0000")])
    newly, _ = exclude_rows.mark(
        p,
        backend="kimat",
        since="2026-10-05T08:00:00-0400",
        until="2026-10-05T09:00:00-0400",
        reason="void",
        apply=True,
    )
    assert newly == 1
    assert json.loads(p.read_text().strip())["excluded"] is True


def test_a_row_outside_the_window_by_instant_is_untouched(tmp_path):
    """The opposite error: 08:30 UTC is 04:30 New York, before the window,
    but "2026-10-05T08:30:00+0000" sorts inside "...T08:00" to "...T09:00".
    A valid measurement would have been excluded."""
    p = ledger(tmp_path, [dict(ROW, started="2026-10-05T08:30:00+0000")])
    newly, _ = exclude_rows.mark(
        p,
        backend="kimat",
        since="2026-10-05T08:00:00-0400",
        until="2026-10-05T09:00:00-0400",
        reason="void",
        apply=True,
    )
    assert newly == 0
    assert "excluded" not in json.loads(p.read_text().strip())


def test_the_stored_timestamp_is_not_rewritten(tmp_path):
    """Parsing is for the comparison only; the row keeps its own spelling."""
    p = ledger(tmp_path, [dict(ROW, started="2026-10-05T12:30:00+0000")])
    exclude_rows.mark(
        p,
        backend="kimat",
        since="2026-10-05T08:00:00-0400",
        until="2026-10-05T09:00:00-0400",
        reason="void",
        apply=True,
    )
    assert json.loads(p.read_text().strip())["started"] == "2026-10-05T12:30:00+0000"


@pytest.mark.parametrize("flag", ["--since", "--until"])
def test_a_bound_without_an_offset_is_refused(tmp_path, flag):
    """A naive bound names no instant: New York and UTC are four hours apart,
    and the ledgers hold both. Say which."""
    p = ledger(tmp_path, [ROW])
    other = "--until" if flag == "--since" else "--since"
    code = exclude_rows.main(
        [
            str(p),
            "--backend",
            "kimat",
            flag,
            "2026-09-04T20:57",
            other,
            "2026-09-04T20:00-0400" if other == "--since" else "2026-09-05T00:00-0400",
            "--reason",
            "x",
        ]
    )
    assert code == 2


def test_a_selected_row_without_an_offset_is_refused(tmp_path):
    """Every committed row carries an offset (test_iso8601_timestamps). One
    that does not is refused, not guessed into a zone."""
    p = ledger(tmp_path, [dict(ROW, started="2026-09-04T21:00:00")])
    with pytest.raises(ValueError, match="offset"):
        exclude_rows.mark(
            p,
            backend="kimat",
            since="2026-09-04T20:57-0400",
            until="2026-09-04T21:27-0400",
            reason="x",
            apply=False,
        )


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("pgrep"),
        subprocess.TimeoutExpired(["pgrep"], 10),
        PermissionError("pgrep"),
    ],
)
def test_a_process_check_that_cannot_run_refuses(monkeypatch, failure):
    """No answer is not "nothing is running": the check must fail closed."""

    def boom(*args, **kwargs):
        raise failure

    monkeypatch.setattr(exclude_rows.subprocess, "run", boom)
    assert exclude_rows._harness_running() is True


@pytest.mark.parametrize(("code", "running"), [(0, True), (1, False), (2, True)])
def test_pgrep_exit_status_is_read(monkeypatch, code, running):
    """pgrep exits 0 on a match, 1 on none, and 2 or 3 on its own error."""
    out = "4242\n" if code == 0 else ""

    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], code, stdout=out, stderr="")

    monkeypatch.setattr(exclude_rows.subprocess, "run", fake)
    assert exclude_rows._harness_running() is running
