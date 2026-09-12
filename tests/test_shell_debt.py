"""#235's two numbers, computed rather than quoted.

The negative cases are the point. This script's whole job is to say "done" or
"not done", so what matters is that it cannot say "done" wrongly -- and the
way it would is a stale REPLACED entry pointing at a file that has since been
renamed or removed.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

import shell_debt

#: `source .../lib/<name>` -- the statement, not the name.
#:
#: A substring match on the filename reads a COMMENT as a dependency, and it
#: does so silently. `lib/mlx_serve.sh` opens by calling itself "the sibling
#: of lib/ds4_server.sh", which a substring match counted as sourcing it: the
#: library appeared to have nine sourcers when it has eight, and deleting it
#: would then have waited on a file that does not use it. This repo has paid
#: for a substring matcher once already -- see the header of
#: benchmarks/agent/test_ds4_route.py, where fixture and parser agreed with
#: each other and neither agreed with ds4.
SOURCE_LINE = "source"


def sourcers(library: str) -> list[str]:
    """Every tracked shell that actually `source`s `library`."""
    name = pathlib.Path(library).name
    pattern = re.compile(rf"^\s*{SOURCE_LINE}\s+.*lib/{re.escape(name)}", re.MULTILINE)
    return sorted(
        f
        for f in shell_debt.shell_files()
        if f != library and pattern.search((ROOT / f).read_text())
    )


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
        shell_debt.REPLACED, "vault/stack_agent_ab.sh", "scripts/gone.py"
    )
    got = shell_debt.survey()
    row = next(f for f in got["files"] if f["path"] == "vault/stack_agent_ab.sh")
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
    # docs/automation-hazards.md quotes 3,589 -> 359. If either moves, the
    # document is wrong, not the script.
    assert shell_debt.TARGET == round(shell_debt.BASELINE * 0.1)
    doc = (ROOT / "docs/automation-hazards.md").read_text()
    assert "3,589" in doc and "359" in doc


def test_the_keep_list_names_files_that_exist_and_gives_a_reason() -> None:
    tracked = set(shell_debt.shell_files())
    for path, why in shell_debt.KEEP.items():
        assert path in tracked, f"{path} is in KEEP but is not tracked shell"
        assert why.strip(), f"{path} is kept for no stated reason"


def test_the_user_facing_installer_is_kept() -> None:
    """The 90% target must not be read as "port everything".

    RECOMMENDATIONS.md section 3 tells a stranger to run
    `scripts/local-agent.sh`. Porting it changes published instructions and
    buys nothing -- a Python installer is still a script you paste -- and at
    284 lines it is the single biggest file left, which is exactly what makes
    it the one somebody reaches for when the line count is close.
    """
    assert "scripts/local-agent.sh" in shell_debt.KEEP
    assert "local-agent.sh" in (ROOT / "RECOMMENDATIONS.md").read_text()


def test_dies_with_names_scripts_that_are_actually_replaced() -> None:
    """A file retired by deleting others only if every one of them IS replaced.

    All of them, not the first: a library dies with its LAST sourcer, so one
    unreplaced sourcer keeps it alive however many of the rest are done.
    """
    for path, owners in shell_debt.DIES_WITH.items():
        assert isinstance(owners, tuple), f"{path} must name a tuple of sourcers"
        assert owners, f"{path} dies with nothing, which retires it silently"
        for owner in owners:
            assert owner in shell_debt.REPLACED, f"{path} waits on unreplaced {owner}"


def test_dies_with_names_every_file_that_sources_the_library() -> None:
    """The list has to be complete, or the library is deleted too early.

    This is the failure the mapping exists to prevent, and it is a quiet one:
    a sourcer left off the list is not wrong today -- it is wrong on the day
    somebody deletes the library because the names that ARE listed are gone.
    So the list is checked against the tree rather than maintained by hand.
    """
    for path, owners in shell_debt.DIES_WITH.items():
        assert sourcers(path) == sorted(owners), f"{path}: tree says {sourcers(path)}"


def test_a_sourced_library_is_never_asked_for_its_own_evidence() -> None:
    """EVIDENCE names a differential, and a library cannot produce one.

    It is never invoked on its own, so there is no recorded argv to compare.
    An entry here would be a differential somebody wrote against nothing.
    """
    for path in shell_debt.DIES_WITH:
        assert path not in shell_debt.EVIDENCE, f"{path} is a library, not a driver"


def test_every_deviation_names_a_replaced_file_and_gives_evidence() -> None:
    """A deviation retires a REPLACED shell without an agreeing run, so it must
    name the file AND the evidence that it cannot produce one. A deviation on
    an unreplaced file would be a way to skip porting it."""
    for path, why in shell_debt.DEVIATIONS.items():
        assert path in shell_debt.REPLACED, f"{path} is deviated but not replaced"
        assert "Evidence:" in why, f"{path} deviation names no evidence"
        assert why.strip(), f"{path} is deviated for no stated reason"


def test_transcript_move_is_sourced_only_by_the_script_it_dies_with() -> None:
    """Asserted, because 'nothing else uses it' is the whole claim.

    Every other mention of transcript_move.sh in the tree is a comment
    explaining why lib/batch.py filters on mtime instead. If a second script
    ever sources it, deleting stack_agent_ab.sh stops being enough.
    """
    got = sourcers("scripts/lib/transcript_move.sh")
    assert got == ["vault/stack_agent_ab.sh"], got


def test_every_unreplaced_file_is_classified() -> None:
    """No file may sit in the remainder unaccounted for.

    Either it is portable, or it dies with something, or it is kept for a
    written reason. An unclassified file is one nobody has decided about, and
    it will be discovered at 359 lines when the decision is expensive.
    """
    got = shell_debt.survey()
    counted = len(got["portable"]) + len(got["dies_with"]) + len(got["keep"])
    unreplaced = [f for f in got["files"] if not f["replacement_exists"]]
    assert counted == len(unreplaced)


def test_the_target_is_reachable_without_porting_a_kept_file() -> None:
    """If this fails, the 90% target needs a person, not more porting.

    It says: port every portable file, retire every replaced one, and the
    lines that MUST stay shell still come in under 359. Today that is 342 --
    17 to spare. If a new .sh lands in KEEP and pushes the floor over the
    target, the choice is to move the target or to move a file out of KEEP,
    and either one is the operator's call.
    """
    got = shell_debt.survey()
    assert got["target_reachable"], (
        f"the floor is {got['floor_lines']} lines against a target of "
        f"{got['target_lines']}; porting everything portable is no longer enough"
    )


# --- the retirement evidence, which must name tests that exist ---------------


def test_every_evidence_entry_names_a_test_that_exists() -> None:
    """An entry pointing at a renamed or deleted test reports a shell as
    cleared to delete when nothing checks it any more -- worse than no entry,
    because it reads as done."""
    root = pathlib.Path(shell_debt.ROOT)
    for shell, node in shell_debt.EVIDENCE.items():
        path, _, name = node.partition("::")
        test_file = root / path
        assert test_file.exists(), f"{shell}: {path} does not exist"
        assert f"def {name}(" in test_file.read_text(), f"{shell}: no {name} in {path}"


def test_evidence_only_names_shells_that_have_a_replacement() -> None:
    """Evidence for a file with no port is a category error: the differential
    would have nothing to compare the shell against."""
    for shell in shell_debt.EVIDENCE:
        assert shell in shell_debt.REPLACED, f"{shell} has evidence but no replacement"


#: Shells that legitimately carry BOTH a differential and a deviation, with
#: the reason each one is not a full agreeing run. Enumerated, so a third is a
#: decision somebody makes rather than a row that appears.
OVERLAP = {
    # The shell is dead by #264 and cannot produce an agreeing run at all.
    "vault/route_agent_ab.sh": "cannot produce one",
    # 541 lines whose top level takes the lock, arms two traps, syncs
    # worktrees and starts a shim. The differential compares the two command
    # lines and says so; the rest is covered by other files.
    "vault/stack_agent_ab.sh": "not compared",
}


def test_an_overlap_between_evidence_and_deviation_says_what_it_misses() -> None:
    """The two tables may overlap, and the overlap must explain ITSELF.

    An entry in EVIDENCE claims a differential; an entry in DEVIATIONS says
    the retirement is not on a full agreeing run. A file in both is making a
    narrower claim than EVIDENCE alone would imply -- "here is the test, and
    here is what it does not reach" -- which is more honest than either table
    alone, and is worthless if the deviation does not name the gap.

    This replaced a version that pinned the overlap to route_agent_ab by name.
    It passed for one file and had nothing to say about the second, which is
    the difference between asserting a rule and recording an instance.
    """
    both = set(shell_debt.EVIDENCE) & set(shell_debt.DEVIATIONS)
    assert both == set(OVERLAP), f"unlisted overlap: {both ^ set(OVERLAP)}"
    for shell, marker in OVERLAP.items():
        why = shell_debt.DEVIATIONS[shell]
        assert marker.lower() in why.lower(), (
            f"{shell} is in both tables but its deviation does not say what "
            f"the differential misses (looked for {marker!r})"
        )


def test_cleared_and_waiting_account_for_every_replaced_file() -> None:
    """A file that is neither would vanish from the retirement count."""
    got = shell_debt.survey()
    replaced = [f for f in got["files"] if f["replacement_exists"]]
    assert len(got["cleared"]) + len(got["waiting"]) == len(replaced)


def test_a_library_is_not_cleared_while_a_sourcer_still_waits() -> None:
    """The rule that replaced a table entry, asserted in both directions.

    `lib/ds4_server.sh` is REPLACED and has no EVIDENCE of its own, so under
    the old rule it sat in the report as "NO EVIDENCE" -- which reads as a
    differential somebody forgot to write, for a file that cannot have one.
    It is deletable when nothing sources it, and not before.
    """
    got = shell_debt.survey()
    waiting = {f["path"]: f for f in got["waiting"]}
    cleared = {f["path"] for f in got["cleared"]}

    for lib in ("vault/lib/ds4_server.sh", "vault/lib/mlx_serve.sh"):
        owners = set(shell_debt.DIES_WITH[lib])
        if owners & set(waiting):
            assert lib in waiting, f"{lib} cleared while {owners & set(waiting)} wait"
            assert waiting[lib]["blocked_on"], f"{lib} waits but names nobody"
            assert set(waiting[lib]["blocked_on"]) == owners & set(waiting)
        else:
            assert lib in cleared, f"{lib}: every sourcer is done, so it goes too"


def test_a_driver_waiting_for_a_differential_names_nobody() -> None:
    """`blocked_on` is the library case only. A driver waits on its own test,
    and listing a sourcer for it would invent a dependency that is not there.
    """
    for f in shell_debt.survey()["waiting"]:
        if f["path"] not in shell_debt.DIES_WITH:
            assert not f["blocked_on"], f"{f['path']} is a driver, not a library"
