"""#1151: no tracked file names a LAN (192.168.x.x) address.

This repo is public. Evan, 2026-10-18: keep the 192.168 addresses out of it.
The private 10.x.x.x addresses of the DGX fabric stay allowed. A failure names
the file and line only, never the address: the CI log is public too.
"""

from __future__ import annotations

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAN_ADDRESS = r"192\.168\.[0-9]{1,3}\.[0-9]{1,3}"


def lan_hits() -> list[str]:
    """Return `file:line` for each match. The matched text stays in here, so a
    failure (even under `--showlocals`) cannot print it."""
    result = subprocess.run(
        ["git", "grep", "-I", "-n", "-E", "-e", LAN_ADDRESS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # git grep exits 1 when nothing matches; anything else is an error.
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git grep failed with exit {result.returncode}")
    return [":".join(line.split(":", 2)[:2]) for line in result.stdout.splitlines()]


def test_no_tracked_file_names_a_lan_address() -> None:
    hits = lan_hits()
    assert not hits, "LAN address in tracked files (file:line): " + ", ".join(hits)
