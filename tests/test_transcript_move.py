"""The transcript mover's mtime filter (#112, offered by the peer).

`stack_agent_ab.sh` moves each sweep's transcripts out of the shared log dir.
The plain glob had a hole: a run killed before ITS move ran leaves transcripts
behind, and the next run of the same arm swept them into its own directory --
`old-sweep1` once held 22 transcripts for a 15-task sweep. The helper keeps
only files written after the sweep's recorded start.

The start instant is passed as a `touch -t` token (CCYYMMDDhhmm.ss), which the
helper stamps onto a marker and compares with `find -newer`. That is POSIX on
both the BSD find the Metal run uses and the GNU find CI runs, so this test is
faithful on either platform.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent
HELPER = REPO / "scripts" / "lib" / "transcript_move.sh"

#: The sweep start as `touch -t` CCYYMMDDhhmm.ss: 2026-09-06 12:00:00.
START_TOK = "202609061200.00"


def run_mover(
    src: pathlib.Path, out: pathlib.Path, tag: str, tok: str, backend: str
) -> None:
    script = src / "run_move.sh"
    script.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\nsource {HELPER}\n"
        'move_transcripts_since "$@"\n'
    )
    script.chmod(0o755)
    subprocess.run(
        ["bash", str(script), str(src), str(out), tag, tok, backend],
        check=True,
        capture_output=True,
    )


def stamp(
    path: pathlib.Path, y: int, m: int, d: int, hh: int, mm: int, ss: int
) -> None:
    """Set the file's mtime to a fixed local instant."""
    mtime = datetime.datetime(y, m, d, hh, mm, ss).timestamp()
    os.utime(path, (mtime, mtime))


def test_only_files_newer_than_the_start_move(tmp_path):
    """A stale leftover from a killed run must stay put; this sweep's files go."""
    src = tmp_path / "bench-logs"
    out = tmp_path / "out"
    src.mkdir()
    stale = src / "old-backend-opencode-1-aaa.md"
    stale.write_text("stale")
    stamp(stale, 2026, 9, 6, 11, 30, 0)  # before the sweep started

    fresh = src / "old-backend-opencode-1-bbb.md"
    fresh.write_text("fresh")
    stamp(fresh, 2026, 9, 6, 12, 15, 5)  # after the sweep started

    run_mover(src, out, "old-sweep2", START_TOK, "old-backend")

    moved = out / "old-sweep2"
    assert (moved / "old-backend-opencode-1-bbb.md").exists(), "fresh file must move"
    assert (moved / "old-backend-opencode-1-aaa.md").exists(), (
        "stale file must NOT move; it belongs to the killed run"
    )
    # Nothing is deleted: the stale one stays in the shared log dir.
    assert stale.exists()


def test_a_different_backend_is_untouched(tmp_path):
    """The glob is per-backend; the other arm's files never match it."""
    src = tmp_path / "bench-logs"
    out = tmp_path / "out"
    src.mkdir()
    other = src / "new-backend-opencode-1-ccc.md"
    other.write_text("other arm")
    stamp(other, 2026, 9, 6, 12, 15, 5)

    run_mover(src, out, "old-sweep2", START_TOK, "old-backend")

    assert other.exists(), "the other arm's transcript must stay in the shared dir"
    assert sorted(p.name for p in (out / "old-sweep2").iterdir()) == []


def test_no_files_moves_nothing_and_makes_the_dir(tmp_path):
    """An empty sweep still records its (empty) directory for the row count."""
    src = tmp_path / "bench-logs"
    out = tmp_path / "out"
    src.mkdir()
    run_mover(src, out, "old-sweep3", START_TOK, "old-backend")
    moved = out / "old-sweep3"
    assert moved.is_dir(), "the row count reads this dir even when it is empty"
    assert sorted(p.name for p in moved.iterdir()) == []


def test_a_non_matching_file_is_left(tmp_path):
    """A log that is not a transcript (per-sweep server log) stays behind."""
    src = tmp_path / "bench-logs"
    out = tmp_path / "out"
    src.mkdir()
    server_log = src / "server-old-sweep2.log"
    server_log.write_text("server log")
    stamp(server_log, 2026, 9, 6, 12, 15, 5)
    run_mover(src, out, "old-sweep2", START_TOK, "old-backend")
    assert server_log.exists()
