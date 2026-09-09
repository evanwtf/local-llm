"""Nothing in vault/ may run, and nothing may come to depend on it (#235).

`vault/` holds the 18 shell drivers retired by the port. They are kept because
the rows they produced are still cited and a result whose driver has been
deleted cannot be re-read -- see vault/README.md for the whole argument.

A README is advice. These are the rules.

The one that matters most is the last: each of these was retired on the promise
that a Python replacement exists. If a replacement is ever deleted, this stops
being an archive and becomes the only copy of a thing that takes the machine
for hours -- and then somebody will run it.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"

sys.path.insert(0, str(ROOT / "scripts"))

import shell_debt


def vault_shells() -> list[pathlib.Path]:
    return sorted(VAULT.rglob("*.sh"))


def test_the_vault_is_not_empty() -> None:
    """A guard over an empty directory asserts nothing at all."""
    assert len(vault_shells()) == 18, [p.name for p in vault_shells()]


def test_nothing_in_the_vault_is_executable() -> None:
    """The rule, made mechanical.

    "Do not run these" in a README is advice a hurried person skips. A missing
    executable bit is a refusal from the shell itself, and it is the same
    mechanism `test_scripts_executable.py` uses in the opposite direction for
    the drivers that ARE live.
    """
    runnable = [p.name for p in vault_shells() if os.access(p, os.X_OK)]
    assert not runnable, (
        f"executable in vault/: {runnable}. These take the machine lock and "
        "start ~100 GiB servers; running one does not fail, it succeeds and "
        "voids whatever was in flight. chmod -x them."
    )


def test_the_vault_has_a_readme_that_says_not_to_run_them() -> None:
    text = (VAULT / "README.md").read_text()
    assert "Do not run" in text
    assert "may be executed" in text, "the README must state the rule"


def test_every_vaulted_shell_still_has_its_replacement() -> None:
    """The promise the retirement was granted against.

    Each of these was retired because a Python file does its job and a
    differential showed the two agree. Delete the Python and the archive
    becomes the only copy -- which is the one state in which somebody would
    reasonably run it.
    """
    missing = []
    for rel, replacement in shell_debt.REPLACED.items():
        if not rel.startswith("vault/"):
            continue
        if not (ROOT / replacement).exists():
            missing.append(f"{rel} -> {replacement}")
    assert not missing, (
        "a vaulted shell has lost its replacement, so the archive is now the "
        f"only copy: {missing}"
    )


def test_every_vaulted_shell_is_accounted_for_in_shell_debt() -> None:
    """No file may sit in vault/ without a table entry.

    A shell moved here with no REPLACED row would drop out of the retirement
    count while still being in the repo -- the exact way a `git mv` could be
    made to look like a port.
    """
    tracked = {f for f in shell_debt.shell_files() if f.startswith("vault/")}
    on_disk = {str(p.relative_to(ROOT)) for p in vault_shells()}
    assert tracked == on_disk, f"untracked or unlisted: {tracked ^ on_disk}"
    unlisted = sorted(f for f in tracked if f not in shell_debt.REPLACED)
    assert not unlisted, f"vaulted with no replacement recorded: {unlisted}"


def test_the_report_never_folds_archived_lines_into_the_live_count() -> None:
    """Moving a file retires nothing. The differential retires it.

    If these two ever became one number, a `git mv` would move the headline
    and no reader could tell archiving from porting.
    """
    got = shell_debt.survey()
    assert got["vaulted_lines"] > 0 and got["live_lines"] > 0
    assert got["vaulted_lines"] + got["live_lines"] == got["total_lines"]
    assert got["vaulted_files"] == len(vault_shells())


#: Files allowed to name a vault path. The differentials read these shells as
#: TEXT -- they slice a function out, or run it under bash against recording
#: fakes -- which is the whole evidence that the port agrees with them. That is
#: reading, not depending: none of it survives the day the shells are deleted,
#: and each such test carries its own
#: `test_the_shell_it_replaces_is_still_here` to say so.
READERS = re.compile(r"^tests/|^scripts/shell_debt\.py$|^vault/")


def test_nothing_outside_the_readers_builds_a_path_into_the_vault() -> None:
    """Production code must not reach in here.

    A driver, a report or a hook that resolved a vault path would make the
    archive load-bearing, and the next person to delete it would break a run
    rather than a test.
    """
    out = subprocess.run(
        ["git", "grep", "-l", "-E", r"vault/[A-Za-z0-9_/-]*\.sh", "--", "*.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    offenders = sorted(f for f in out.stdout.split() if f and not READERS.match(f))
    assert not offenders, (
        f"these resolve a vault path but are not tests: {offenders}. The "
        "archive must not be load-bearing."
    )


def test_no_live_shell_sources_a_vaulted_library() -> None:
    """`lib/ds4_server.sh` and `lib/mlx_serve.sh` moved with their callers.

    A live script left sourcing one would work today and break the moment the
    vault is cleared -- and it would be sourcing a library carrying the
    chained-EXIT status defect these two are archived with.
    """
    for path in sorted((ROOT / "scripts").rglob("*.sh")):
        text = path.read_text()
        assert "vault/" not in text, f"{path.name} reaches into the vault"
