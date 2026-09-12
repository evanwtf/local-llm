"""summarize.py must actually run to completion.

It did not, from 500491a (2026-09-02) until 2026-09-11: a bare `print()`
became `logger.info()` with no argument, which raises TypeError. The table
printed, then the process died before the per-backend totals -- so the script
looked like it worked if you only read the top of its output.

There was no test that ran the file at all. That is the gap this closes: these
call `main()` end to end rather than testing a helper, because every helper
here was already fine.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _ledger(tmp_path: pathlib.Path) -> pathlib.Path:
    rows = [
        {
            "schema_version": 2,
            "task": "mbox-scan",
            "backend": "b1",
            "client": "opencode",
            "trial": 1,
            "passed": True,
            "wall_seconds": 12.5,
            "num_turns": 4,
        },
        {
            "schema_version": 2,
            "task": "mbox-scan",
            "backend": "b1",
            "client": "opencode",
            "trial": 2,
            "passed": False,
            "wall_seconds": 30.0,
            "num_turns": 9,
            "error": "timeout",
        },
        # A dry run: set aside, never counted in the denominator.
        {
            "schema_version": 2,
            "task": "mbox-scan",
            "backend": "b1",
            "client": "opencode",
            "trial": 3,
            "dry_run": True,
        },
    ]
    path = tmp_path / "results.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _run(path: pathlib.Path, *extra: str) -> subprocess.CompletedProcess:
    """Run summarize.py end to end, then remove the log it teed.

    `provenance.tee(machine_specific=True)` writes into
    `hardware/<machine>/logs/`, which is inside the repo -- so running this
    test leaves an artifact in the working tree, and running it in CI leaves
    one on every build. Twelve were committed before anyone noticed.

    The log path is on stdout because summarize.py now reports it, so the test
    can find and remove exactly the file it caused. Deleting by that line
    rather than by glob means a real summarize log sitting in the same
    directory is never touched.
    """
    got = subprocess.run(
        [sys.executable, str(HERE / "summarize.py"), "--results", str(path), *extra],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    for line in (got.stdout + got.stderr).splitlines():
        marker = "log: "
        if marker in line:
            logged = pathlib.Path(line.split(marker, 1)[1].strip())
            if logged.is_file() and logged.suffix == ".log":
                logged.unlink()
    return got


def test_it_exits_zero_and_reaches_the_totals(tmp_path) -> None:
    """The regression: the totals come AFTER the blank line that crashed."""
    got = _run(_ledger(tmp_path))
    assert got.returncode == 0, got.stderr
    assert "TypeError" not in got.stderr
    combined = got.stdout + got.stderr
    assert "1/2 passed" in combined, combined
    assert "timeouts 1" in combined, combined
    # The log path is reported after the totals -- the last thing main() does,
    # and the line whose absence made `log_file` look unused (ruff F841).
    assert "log: " in combined, combined


def test_markdown_mode_also_completes(tmp_path) -> None:
    got = _run(_ledger(tmp_path), "--markdown")
    assert got.returncode == 0, got.stderr
    assert "TypeError" not in got.stderr
