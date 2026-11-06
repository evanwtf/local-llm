"""#1151, #1153: no tracked file names a LAN address or a DGX host name.

This repo is public. Evan, 2026-10-18: keep the 192.168 addresses out of it,
and the DGX host names too. The private 10.x.x.x addresses of the DGX fabric
stay allowed. A failure names the file and line only, never the match: the CI
log is public too.

The host names are the private DNS names (`<name>.internal`) and the system
host name the DGX OS gives a Spark (`spark-` and four hex digits). A script
that needs a node's name reads it from local config: `scripts/lib/dgx_hosts.py`.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAN_ADDRESS = r"192\.168\.[0-9]{1,3}\.[0-9]{1,3}"
#: `<name>.internal`, and a Spark's own `spark-` plus four hex digits.
HOST_NAME = r"[A-Za-z0-9-]+\.internal\b|\bspark-[0-9a-f]{4}\b"


def lan_hits(pattern: str = LAN_ADDRESS) -> list[str]:
    """Return `file:line` for each match. The matched text stays in here, so a
    failure (even under `--showlocals`) cannot print it."""
    result = subprocess.run(
        ["git", "grep", "-I", "-n", "-E", "-e", pattern],
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


def test_no_tracked_file_names_a_dgx_host() -> None:
    hits = lan_hits(HOST_NAME)
    assert not hits, "host name in tracked files (file:line): " + ", ".join(hits)


@pytest.mark.parametrize(
    "text, hit",
    [
        # Built by concatenation, so this file does not trip its own guard.
        ("ssh node7" + ".internal", True),
        ('instance="gpu-box' + '.internal:9400"', True),
        ("syslog from spark" + "-0a1f kernel", True),
        ("spark-vllm-b12x image", False),  # a container name, not a host
        ("the `*.internal` names", False),
        ("10.0.0.2", False),
    ],
)
def test_the_host_name_pattern(text: str, hit: bool) -> None:
    assert bool(re.search(HOST_NAME, text)) is hit
