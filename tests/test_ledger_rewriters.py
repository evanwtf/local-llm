"""Every script that rewrites a ledger must not lose a row while it does it.

Five scripts rewrite `hardware/*/results.jsonl` in place: the three backfills,
`exclude_rows.py` and `archive_pre_dir_rows.py`. Two ways each one could lose
published measurements (review of b7a366b, findings 1 and 2):

- **A crash mid-write.** `Path.write_text` truncates the ledger first, so a
  full disk or a kill after the truncation leaves it empty or half-written.
- **A concurrent append.** `run.py` appends through `results.write_row`. A row
  that lands after the script reads the ledger and before it writes the
  replacement is not in the replacement, and is gone.

The fix is the same for all five: hold `results.ledger_lock` (which
`write_row` also takes) across the read and the replace, and write the
replacement with `results.replace_ledger` (a temp file and `os.replace`).
These tests run every writer on a temp copy, never on a committed ledger.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import pathlib
import sys
import threading
from collections.abc import Callable
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import archive_pre_dir_rows as apdr
import backfill_client_version
import backfill_gates_inapplicable
import backfill_iso8601
import exclude_rows
import results

MACHINE = "MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A"
APPENDED = "appended-during-rewrite"


def _write(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _rows(path: pathlib.Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _parsed(path: pathlib.Path) -> list[dict[str, Any]]:
    """The rows that survive as whole JSON; a torn last line does not count."""
    out = []
    for line in path.read_text().splitlines():
        with contextlib.suppress(ValueError):
            out.append(json.loads(line))
    return out


#: Ten rows the writer leaves alone, then one it must change. A crash halfway
#: through the write loses rows from the first group, which is the evidence.
def _keep(i: int) -> dict[str, Any]:
    return {
        "task": f"kept-{i}",
        "backend": "other",
        "client": "claude",
        "client_version": "2.0.0",
        "started": f"2026-09-04T2{i % 10}:00:00-0400",
        "env": {"harness_head": "28b1da6"},
        "wall_seconds": 100.0 + i,
    }


class Case:
    """One writer, set up on a temp tree, with the hook it reads through."""

    def __init__(
        self,
        name: str,
        target: dict[str, Any],
        module: Any,
        hook: str,
        run: Callable[[pathlib.Path], object],
        ledger_at: Callable[[pathlib.Path], pathlib.Path],
        changed: Callable[[list[dict[str, Any]]], bool],
    ) -> None:
        self.name = name
        self.target = target
        self.module = module
        self.hook = hook
        self.run = run
        self.ledger_at = ledger_at
        self.changed = changed


def _flat(tmp: pathlib.Path) -> pathlib.Path:
    return tmp / "results.jsonl"


def _hardware(tmp: pathlib.Path) -> pathlib.Path:
    return tmp / "hardware" / MACHINE / "results.jsonl"


def _target(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((r for r in rows if r.get("task") == "target"), None)


def _iso_run(monkeypatch: pytest.MonkeyPatch) -> Callable[[pathlib.Path], object]:
    def run(ledger: pathlib.Path) -> object:
        monkeypatch.setattr(backfill_iso8601, "ROOT", ledger.parents[2])
        monkeypatch.setattr(sys, "argv", ["backfill_iso8601.py", "--write"])
        return backfill_iso8601.main()

    return run


def _archive_run(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[pathlib.Path], object]:
    def run(ledger: pathlib.Path) -> object:
        archive = ledger.parent / "archive.jsonl"
        if not archive.exists():
            _write(archive, [dict(_keep(90), task="archived-earlier")])
        monkeypatch.setattr(apdr, "RESULTS", ledger)
        monkeypatch.setattr(apdr, "ARCHIVE", archive)
        monkeypatch.setattr(apdr, "fixed_commits", lambda repo: {"28b1da6"})
        return apdr.main([])

    return run


def _cases(monkeypatch: pytest.MonkeyPatch) -> dict[str, Case]:
    base = {"task": "target", "backend": "b", "trial": 1, "wall_seconds": 42.0}
    return {
        "backfill_client_version": Case(
            "backfill_client_version",
            dict(
                base,
                client="opencode",
                started="2026-09-04T21:00:00-0400",
                env={"opencode": "1.18.27", "harness_head": "28b1da6"},
            ),
            backfill_client_version,
            "plan",
            lambda p: backfill_client_version.main([str(p), "--apply"]),
            _flat,
            lambda rows: (_target(rows) or {}).get("client_version") == "1.18.27",
        ),
        "backfill_gates_inapplicable": Case(
            "backfill_gates_inapplicable",
            dict(
                base,
                client="opencode",
                client_version="1.18.27",
                started="2026-09-04T21:00:00-0400",
                target_repo="~/git/monitor",
                gates_delta={"ruff": 0},
                env={"harness_head": "28b1da6"},
            ),
            backfill_gates_inapplicable,
            "plan",
            lambda p: backfill_gates_inapplicable.main([str(p), "--apply"]),
            _flat,
            lambda rows: (_target(rows) or {}).get("gates_inapplicable") is True,
        ),
        "backfill_iso8601": Case(
            "backfill_iso8601",
            dict(
                base,
                client="opencode",
                client_version="1.18.27",
                started="2026-09-04T21:00:00",
                env={"harness_head": "28b1da6"},
            ),
            backfill_iso8601,
            "walk",
            _iso_run(monkeypatch),
            _hardware,
            lambda rows: (
                (_target(rows) or {}).get("started") == "2026-09-04T21:00:00-0400"
            ),
        ),
        "exclude_rows": Case(
            "exclude_rows",
            dict(
                base,
                client="opencode",
                client_version="1.18.27",
                started="2026-09-04T21:00:00-0400",
                env={"harness_head": "28b1da6"},
            ),
            exclude_rows,
            "selects",
            lambda p: exclude_rows.main(
                [
                    str(p),
                    "--backend",
                    "b",
                    "--since",
                    "2026-09-04T20:57:00-0400",
                    "--until",
                    "2026-09-04T21:27:00-0400",
                    "--reason",
                    "void run",
                    "--apply",
                ]
            ),
            _flat,
            lambda rows: (_target(rows) or {}).get("excluded") is True,
        ),
        "archive_pre_dir_rows": Case(
            "archive_pre_dir_rows",
            dict(
                base,
                client="opencode",
                client_version="1.18.27",
                started="2026-09-04T21:00:00-0400",
                env={"harness_head": "0000000"},
            ),
            apdr,
            "plan",
            _archive_run(monkeypatch),
            _flat,
            lambda rows: _target(rows) is None,
        ),
    }


WRITERS = [
    "backfill_client_version",
    "backfill_gates_inapplicable",
    "backfill_iso8601",
    "exclude_rows",
    "archive_pre_dir_rows",
]


@pytest.fixture(autouse=True)
def _no_harness_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """exclude_rows asks pgrep whether run.py is up; on a dev box it may be."""
    monkeypatch.setattr(exclude_rows, "_harness_running", lambda: False)


def _setup(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> tuple[Case, pathlib.Path, list[dict[str, Any]]]:
    case = _cases(monkeypatch)[name]
    ledger = case.ledger_at(tmp_path)
    before = [_keep(i) for i in range(10)] + [case.target]
    _write(ledger, before)
    return case, ledger, before


@pytest.mark.parametrize("name", WRITERS)
def test_a_row_appended_during_the_rewrite_survives(tmp_path, monkeypatch, name):
    """Finding 2: the append lands between the read and the replace.

    The appender runs on its own thread, as `run.py` would in its own process.
    Without the lock it appends at once, and the rewrite then drops the row.
    With it, the append waits for the rewrite and lands after it.
    """
    case, ledger, _ = _setup(tmp_path, monkeypatch, name)
    original = getattr(case.module, case.hook)
    appender: list[threading.Thread] = []
    new_row = dict(_keep(50), task=APPENDED, client_version="2.0.0")

    def interposed(*args: Any, **kwargs: Any) -> Any:
        if not appender:
            t = threading.Thread(
                target=results.write_row, args=(dict(new_row), ledger), daemon=True
            )
            appender.append(t)
            t.start()
            t.join(timeout=0.5)  # unlocked: done by now; locked: still waiting
        return original(*args, **kwargs)

    monkeypatch.setattr(case.module, case.hook, interposed)
    assert case.run(ledger) == 0
    assert appender, "the hook was never called; the test exercised nothing"
    appender[0].join(timeout=10)
    assert not appender[0].is_alive(), "the appender never got the lock"

    after = _rows(ledger)
    assert [r["task"] for r in after if r["task"] == APPENDED] == [APPENDED]
    assert case.changed(after), "the writer did not make its own change"


def _crash_mid_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a full disk: write half of what was asked, then fail."""
    real = pathlib.Path.write_text

    def half(self: pathlib.Path, data: str, *args: Any, **kwargs: Any) -> int:
        real(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(pathlib.Path, "write_text", half)


def _crash_on_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(os, "replace", fail)


@pytest.mark.parametrize("crash", [_crash_mid_write, _crash_on_replace])
@pytest.mark.parametrize("name", WRITERS)
def test_a_failed_write_leaves_every_row_in_place(tmp_path, monkeypatch, name, crash):
    """Finding 1: a write that fails part-way must not cost a published row.

    After the failure the ledger is either the old file or the new one, never
    a truncated one, and no temp file is left beside it.
    """
    case, ledger, before = _setup(tmp_path, monkeypatch, name)
    archive = ledger.parent / "archive.jsonl"
    if name == "archive_pre_dir_rows":
        _write(archive, [dict(_keep(90), task="archived-earlier")])
    archived_before = archive.read_text() if archive.exists() else None

    crash(monkeypatch)
    with contextlib.suppress(OSError):
        case.run(ledger)
    monkeypatch.undo()

    after = _parsed(ledger)
    kept = [r for r in after if r["task"].startswith("kept-")]
    assert len(kept) == 10, f"{10 - len(kept)} rows lost from the ledger"
    if archived_before is not None:
        text = archive.read_text()
        assert text.startswith(archived_before), "archive truncated"
        assert len(_parsed(archive)) == len(text.splitlines()), "archive torn"
    assert not [p.name for p in ledger.parent.iterdir() if p.suffix == ".tmp"]
    assert len(after) in (len(before), len(before) - 1)
