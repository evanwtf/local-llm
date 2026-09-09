"""#235's two numbers, computed rather than quoted.

The negative cases are the point. This script's whole job is to say "done" or
"not done", so what matters is that it cannot say "done" wrongly -- and the
way it would is a stale REPLACED entry pointing at a file that has since been
renamed or removed.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

import shell_debt


def test_every_mapping_names_a_shell_file_that_exists() -> None:
    """A key for a deleted .sh is a mapping nothing will ever consult again."""
    tracked = set(shell_debt.shell_files())
    stale = sorted(set(shell_debt.REPLACED) - tracked)
    assert not stale, f"{stale} are in REPLACED but are not tracked {shell_debt.SCOPE}"


def test_every_mapping_names_a_python_file_that_exists() -> None:
    """The failure this guards: a rename that turns done into silently-done.

    `disk_kv_mechanism_test.py` became `disk_kv_mechanism.py` because pytest
    collects `*_test.py` from the whole repo. A table that still named the old
    path would keep reporting the shell replaced.
    """
    missing = sorted(
        f"{sh} -> {py}"
        for sh, py in shell_debt.REPLACED.items()
        if not (ROOT / py).exists()
    )
    assert not missing, f"REPLACED points at files that do not exist: {missing}"


def test_a_mapping_to_a_missing_file_reads_as_unreplaced(monkeypatch) -> None:
    """Asserted, not assumed: the survey must not trust its own table."""
    monkeypatch.setitem(
        shell_debt.REPLACED, "scripts/stack_agent_ab.sh", "scripts/gone.py"
    )
    got = shell_debt.survey()
    row = next(f for f in got["files"] if f["path"] == "scripts/stack_agent_ab.sh")
    assert row["replaced_by"] == "scripts/gone.py"
    assert row["replacement_exists"] is False
    assert got["by_name_if_replaced_deleted"] >= row["by_name"]


def test_the_by_name_pattern_is_a_word_not_a_substring() -> None:
    # `pgrep` inside a longer identifier is not a process lookup, and a
    # substring match would inflate the number this reports as done.
    find = shell_debt.BY_NAME.findall
    assert find("until ! pgrep -f 'x.sh'") == ["pgrep"]
    assert find("pkill -f x") == ["pkill"]
    assert find("my_pgrep_helper") == []
    assert find("# no process lookup here") == []


def test_no_shell_that_matches_a_process_by_name_is_unreplaced() -> None:
    """The #235 milestone: pgrep-first, regardless of file size.

    An earlier plan ordered the port largest-first, hit its line target, and
    left three process-by-name lookups alive in two small files -- which sort
    last precisely because they are small. This fails if that happens again,
    including for a NEW .sh: a rule that only removes shell while new shell
    arrives is a treadmill.
    """
    got = shell_debt.survey()
    offenders = sorted(
        f["path"] for f in got["files"] if f["by_name"] and not f["replacement_exists"]
    )
    assert not offenders, (
        f"{offenders} match a process by name and have no Python replacement. "
        "A pattern matches the shell that quoted it; bracketing only ever "
        "protected against self-match."
    )


def test_the_survey_totals_agree_with_its_own_rows() -> None:
    got = shell_debt.survey()
    assert got["total_lines"] == sum(f["lines"] for f in got["files"])
    assert got["total_by_name"] == sum(f["by_name"] for f in got["files"])


def test_the_reduction_is_measured_against_the_written_baseline() -> None:
    # AGENTS.md quotes 3,589 -> 359. If either moves, the document is wrong,
    # not the script.
    assert shell_debt.TARGET == round(shell_debt.BASELINE * 0.1)
    agents = (ROOT / "AGENTS.md").read_text()
    assert "3,589" in agents and "359" in agents
