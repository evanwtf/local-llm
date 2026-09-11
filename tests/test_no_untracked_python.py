"""An untracked `.py` file is a blind spot for every tracked-file guard (#251).

Several guards discover their own inputs with `git ls-files`: the citation
allowlist (`tests/test_citation_allowlist.py`), the `*_test.py` name check
(`tests/test_script_names.py`), the logging-format guard
(`tests/test_logging_format.py`), and the evidence and shell-debt scans under
`scripts/`. `git ls-files` lists TRACKED files only, so a new `.py` that has
not been `git add`-ed is invisible to all of them. The guard scans a tree that
does not contain the file under test and reports success -- and success is
indistinguishable from a real pass.

That cost a red push on #250: the full suite read `2181 passed, 4 skipped` with
`scripts/decode_ab.py` still untracked, then `1 failed` the moment the commit
made the file visible. Nothing about the code changed between the two runs; the
difference was `git add`.

It is the same shape as the rule the repo already enforces -- *a skipping test
is not a passing test* -- a guard that cannot see its subject reports success.
This is the artifact for it, per the repo's standard that a convention should
be a test rather than a line of prose.

A checkout is always clean, so in CI this passes for free; it can only fail
locally, which is exactly where the mistake happens -- after writing a new file
and before committing it.
"""

from __future__ import annotations

import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent

# The source roots the `ls-files` guards read. `hardware/` is excluded: it holds
# per-machine data and frozen transcripts, not guard-scanned source, and is
# already outside the ruff and format gates (pyproject `extend-exclude`).
SOURCE_ROOTS = ("scripts/", "tests/", "benchmarks/")


def _untracked_python() -> list[str]:
    """Untracked, non-ignored `.py` files under the source roots.

    `--others` lists untracked paths; `--exclude-standard` honours
    `.gitignore`, so a deliberately ignored file does not trip the guard.
    """
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [
        line
        for line in out
        if line.endswith(".py") and (line.startswith(SOURCE_ROOTS) or "/" not in line)
    ]


def test_no_untracked_python_under_the_source_roots() -> None:
    """A new `.py` must be `git add`-ed before a green suite means anything.

    Until it is tracked, the `ls-files`-based guards cannot see it and pass
    over it in silence. Add the file (or remove it, or ignore it in
    `.gitignore` if it is genuinely scratch) before trusting this run.
    """
    untracked = _untracked_python()
    assert not untracked, (
        "untracked Python files are invisible to the tracked-file guards "
        "(citation allowlist, script-name check, logging-format), so a green "
        "suite says nothing about them (#250, #251). `git add` them (or remove "
        f"or .gitignore them) before trusting this run: {sorted(untracked)}"
    )
