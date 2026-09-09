"""The release gates, tested on the cases where they must refuse.

These scripts run unattended on a tag push. Refusing correctly is their whole
job, so the negative cases carry more weight here than the positive one: a
gate that passes everything is indistinguishable from no gate at all, and it
looks green.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_release_version as cv  # noqa: E402
import release_notes as rn  # noqa: E402


# --- the tag itself -------------------------------------------------------


@pytest.mark.parametrize(
    "tag",
    [
        "1.0.0",  # no v: the tag we would push by habit
        "v1.0",  # two components
        "v1",
        "v1.0.0.0",
        "v1.0.0-rc1",  # pre-releases are not a shape this repo publishes
        "release-1.0.0",
        "v1.0.0 ",  # a trailing space from a copy-paste
        "",
    ],
)
def test_a_tag_that_is_not_vXYZ_is_refused(tag: str) -> None:
    with pytest.raises(ValueError):
        cv.version_from_tag(tag)


def test_a_release_tag_yields_its_version() -> None:
    assert cv.version_from_tag("v1.0.0") == "1.0.0"
    assert cv.version_from_tag("v12.34.56") == "12.34.56"


# --- tag against declared version ----------------------------------------


def _tree(tmp_path: pathlib.Path, version: str) -> pathlib.Path:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\n'
    )
    return tmp_path


def test_a_tag_disagreeing_with_pyproject_is_refused(tmp_path: pathlib.Path) -> None:
    problems = cv.disagreements("v2.0.0", _tree(tmp_path, "1.0.0"))
    assert problems, "a tag ahead of the declared version must be refused"
    assert "1.0.0" in problems[0] and "2.0.0" in problems[0]


def test_a_tag_behind_the_declared_version_is_refused(tmp_path: pathlib.Path) -> None:
    # The bump landed and the tag did not: the opposite mistake, equally wrong.
    assert cv.disagreements("v1.0.0", _tree(tmp_path, "2.0.0"))


def test_a_tag_matching_pyproject_passes(tmp_path: pathlib.Path) -> None:
    assert cv.disagreements("v1.0.0", _tree(tmp_path, "1.0.0")) == []


def test_a_second_declaration_that_disagrees_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    # The failure this exists for: pyproject bumped, __init__ forgotten.
    _tree(tmp_path, "1.0.0")
    pkg = tmp_path / "src" / "thing"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('__version__ = "0.9.0"\n')
    problems = cv.disagreements("v1.0.0", tmp_path)
    assert len(problems) == 1
    assert "__init__.py" in problems[0]


def test_no_declared_version_anywhere_is_refused(tmp_path: pathlib.Path) -> None:
    # An empty tree must not read as agreement.
    assert cv.disagreements("v1.0.0", tmp_path)


def test_a_malformed_tag_is_refused_before_any_file_is_read(
    tmp_path: pathlib.Path,
) -> None:
    assert cv.disagreements("nonsense", tmp_path)


# --- release notes --------------------------------------------------------

CHANGELOG = """# Changelog

## v1.0.10 — 2026-09-07

The ten.

## v1.0.1 — 2026-09-01

The one.

## v0.9.0 — 2026-08-01

Older.
"""


def test_a_version_with_no_section_is_refused() -> None:
    assert rn.section("v2.0.0", CHANGELOG) is None


def test_a_section_does_not_match_by_prefix() -> None:
    # v1.0.1 must not be satisfied by v1.0.10's section. Publishing the wrong
    # release's notes is worse than publishing none.
    assert rn.section("v1.0.1", CHANGELOG).strip() == "The one."
    assert rn.section("v1.0.10", CHANGELOG).strip() == "The ten."


def test_a_section_stops_at_the_next_heading() -> None:
    body = rn.section("v1.0.1", CHANGELOG)
    assert "Older" not in body
    assert "The ten" not in body


def test_the_v_prefix_is_optional_in_the_request() -> None:
    assert rn.section("1.0.1", CHANGELOG) == rn.section("v1.0.1", CHANGELOG)


def test_an_empty_section_is_refused() -> None:
    # A heading with nothing under it is the shape of a forgotten entry.
    assert (
        rn.section("v1.0.0", "# Changelog\n\n## v1.0.0 — 2026-09-07\n\n## v0.9.0\n")
        is None
    )


def test_a_changelog_with_no_sections_at_all_is_refused() -> None:
    # This repo's changelog was 1612 lines with one heading before v1.0.0.
    assert rn.section("v1.0.0", "# Changelog\n\n**2026-09-07.** Prose.\n") is None


# --- the scripts as they are actually invoked -----------------------------


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        capture_output=True,
        text=True,
    )


def test_the_version_check_exits_nonzero_when_it_refuses() -> None:
    # A gate that reports failure through stdout alone does not gate anything:
    # CI reads the exit code.
    done = _run("check_release_version.py", "v99.99.99")
    assert done.returncode != 0
    assert "REFUSING" in (done.stdout + done.stderr)


def test_the_notes_script_exits_nonzero_when_it_refuses() -> None:
    done = _run("release_notes.py", "v99.99.99")
    assert done.returncode != 0
    assert "REFUSING" in (done.stdout + done.stderr)


# --- drift guards ---------------------------------------------------------


def _declared() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return str(data["project"]["version"])


def test_the_declared_version_has_a_changelog_section() -> None:
    # Write the entry before bumping, not after tagging. This fails the moment
    # a version bump lands without notes, rather than on the tag push.
    body = rn.section(_declared(), (ROOT / "docs" / "changelog.md").read_text())
    assert body, f"docs/changelog.md has no `## v{_declared()}` section"


def test_the_declared_version_passes_its_own_tag_check() -> None:
    assert cv.disagreements(f"v{_declared()}") == []


def test_the_release_workflow_runs_both_gates() -> None:
    # A workflow that stops calling a gate is the same as deleting it, and
    # nothing else notices.
    workflow = ROOT / ".github" / "workflows" / "release.yml"
    body = workflow.read_text()
    assert "check_release_version.py" in body
    assert "release_notes.py" in body


def test_the_release_workflow_does_not_use_the_tag_message_as_notes() -> None:
    # Backticks in a hand-written `git tag -m` are expanded by the shell and
    # silently delete text. Notes come from the changelog, always.
    body = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "tag -m" not in body
    assert "--notes-file" in body, "notes must come from a file, not an inline string"


def test_the_notes_survive_being_piped_into_head() -> None:
    # The obvious way to look at the notes closes the pipe early. logging
    # answers a closed pipe with a traceback printed over the notes unless
    # SIGPIPE is left at its default.
    notes = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "release_notes.py"), "v1.0.0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    head = subprocess.Popen(["head", "-2"], stdin=notes.stdout, stdout=subprocess.PIPE)
    assert notes.stdout is not None
    notes.stdout.close()
    head.communicate()
    assert notes.stderr is not None
    stderr = notes.stderr.read().decode()
    notes.wait()
    assert "BrokenPipeError" not in stderr, stderr
    assert "Traceback" not in stderr, stderr


def test_no_workflow_uses_the_runner_context_outside_a_step() -> None:
    """The `runner` context does not exist in a workflow- or job-level `env:`.

    A workflow that references it there is created, fails immediately with
    "This run likely failed because of a workflow file issue", and produces no
    logs to read -- so the mistake costs a tag rather than a red step. This
    walks the YAML and asserts `runner.` appears only under a step.
    """
    import yaml

    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        spec = yaml.safe_load(workflow.read_text())
        assert "runner." not in str(spec.get("env", {})), (
            f"{workflow.name}: workflow-level env uses the runner context"
        )
        for name, job in (spec.get("jobs") or {}).items():
            assert "runner." not in str(job.get("env", {})), (
                f"{workflow.name}:{name}: job-level env uses the runner context"
            )


# --- the section must not run off the end of the file ---------------------

LEGACY = """# Changelog

## v1.0.0 — 2026-09-07

The release.

---

**2026-09-07, overnight.** A legacy entry, with no `##` heading of its own.

**2026-09-06.** Another one. There are about 1600 lines of these.
"""


def test_a_section_stops_at_a_horizontal_rule() -> None:
    # The entries below v1.0.0 predate versioning and carry no `##` heading,
    # so a section that only stopped at the next heading ran to end-of-file
    # and produced 102 KB of notes for a six-paragraph release. It looked like
    # success: the gate passed and the notes were not empty.
    body = rn.section("v1.0.0", LEGACY)
    assert body == "The release."
    assert "legacy entry" not in body


def test_the_real_notes_do_not_swallow_the_legacy_history() -> None:
    body = rn.section(_declared(), (ROOT / "docs" / "changelog.md").read_text())
    assert body is not None
    assert "overnight" not in body, "the notes reach into the pre-versioning entries"
    assert len(body) < 8000, (
        f"{len(body)} bytes of release notes: the section is running past its end"
    )


# --- the changelog / history split ----------------------------------------
#
# `docs/changelog.md` was 1652 lines on 2026-09-07, one `#` heading and ~1590
# lines of dated entries. Everything before v1.0.0 moved to `docs/history.md`.
# The split is only worth making if it holds: a new entry appended to the
# bottom of a 1600-line file is an entry nobody reads, and a release whose
# notes reach into that file publishes a book.

HISTORY = ROOT / "docs" / "history.md"
CHANGELOG_MD = ROOT / "docs" / "changelog.md"
#: A pre-versioning entry: a bold date at the start of a line.
LEGACY_ENTRY = re.compile(r"^\*\*(\d{4}-\d{2}-\d{2})", re.M)
VERSION_HEADING = re.compile(r"^##\s+v(\d+\.\d+\.\d+)", re.M)


def test_the_changelog_holds_no_pre_versioning_entries() -> None:
    strays = LEGACY_ENTRY.findall(CHANGELOG_MD.read_text())
    assert not strays, (
        f"docs/changelog.md has entries in the pre-1.0 format ({strays}). "
        "A new entry goes under a `## vX.Y.Z` heading; the old ones live in "
        "docs/history.md."
    )


def test_the_history_holds_no_version_sections() -> None:
    strays = VERSION_HEADING.findall(HISTORY.read_text())
    assert not strays, (
        f"docs/history.md has version sections ({strays}). Released work is "
        "described in docs/changelog.md, which is what release_notes.py reads."
    )


def test_nothing_new_is_appended_to_the_history() -> None:
    # The file is frozen at the split. An entry dated after it is someone
    # adding to the bottom of 1600 lines out of habit -- which is the exact
    # thing the split was made to stop, and it would never be noticed.
    frozen = "2026-09-07"
    later = sorted(d for d in LEGACY_ENTRY.findall(HISTORY.read_text()) if d > frozen)
    assert not later, (
        f"docs/history.md gained entries dated after the {frozen} split "
        f"({later}). New entries belong in docs/changelog.md."
    )


def test_every_version_section_ends_before_the_next_one() -> None:
    # One unterminated section takes every section below it into its notes.
    text = CHANGELOG_MD.read_text()
    versions = VERSION_HEADING.findall(text)
    assert versions, "docs/changelog.md has no version sections at all"
    for version in versions:
        body = rn.section(version, text)
        assert body, f"v{version} has a heading but no body"
        assert len(body) < 8000, f"v{version} runs to {len(body)} bytes; it never ends"
        others = [v for v in versions if v != version]
        for other in others:
            assert f"## v{other}" not in body, (
                f"v{version}'s section swallowed v{other}"
            )
