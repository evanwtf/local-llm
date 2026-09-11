"""`logs/sweeps/` holds only machine-independent field observations (#292).

This repo is one `main` with one directory per machine. A log is filed by what
it is a record *of*, not by which machine happened to run it:

- A **sweep** records what the outside world looked like on a day -- what
  upstream shipped, what Hugging Face has, what X said. That is the same fact on
  the laptop, the Ryzen box and the Spark, so `provenance.log_path(...,
  machine_specific=False)` keeps it in the shared `logs/sweeps/`. The machine
  slug in the filename is copy-provenance (who ran the sweep), not attribution
  of its contents.
- A **benchmark, preflight or build** log is a property of the machine that
  produced it, so `machine_specific=True` routes it to `hardware/<id>/logs/`.

`#292` item 4 named `logs/sweeps/` as a machine-mixed surface. The fix is not to
move the sweeps -- they do not conflict across machines, being slugged and
timestamped, and `.gitattributes` marks `logs/**` `-merge`. The fix is to keep
machine-specific logs *out* of the shared directory. Four `#228` benchmark logs
had been committed there by hand; this test is the artifact that stops the next
one, rather than leaving the rule as prose in CONVENTIONS.md.
"""

from __future__ import annotations

import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]

# A file in logs/sweeps/ must be one of these. Each is a sweep surface whose
# output is a fact about the outside world, identical on every machine. Add a
# prefix here only for a new surface of that kind; a machine-specific log
# (a benchmark, preflight or build) belongs in hardware/<id>/logs/ instead.
SWEEP_PREFIXES = (
    "grok-",  # X/Twitter gathers, copied in by the source-sweep skill
    "hf-sweep-",  # Hugging Face: new quants of models we run
    "upstream-sweep-",  # watched engine repos: releases, commits, open PRs
    "verify-posts-",  # verification of gathered X posts
)


def _tracked_sweep_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "logs/sweeps"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [pathlib.PurePosixPath(p).name for p in out]


def test_logs_sweeps_holds_only_machine_independent_sweeps() -> None:
    tracked = _tracked_sweep_files()
    assert tracked, "no tracked files under logs/sweeps/ -- test is checking nothing"
    stray = [name for name in tracked if not name.startswith(SWEEP_PREFIXES)]
    assert not stray, (
        "machine-specific logs do not belong in the shared logs/sweeps/; "
        f"move them to hardware/<id>/logs/ (#292): {sorted(stray)}"
    )
