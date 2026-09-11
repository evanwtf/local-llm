"""The machine registry is the single source of truth for the hardware we
manage, and three things read it -- the GitHub machine labels, the generated
`hardware/MACHINES.md`, and this test (#302). These pin that the registry, the
`hardware/<id>/` directories, and the generated doc cannot drift apart, and
that a class tag (`macOS`/`Nvidia`) can never stand in for a machine label.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import machines


def test_every_registered_machine_has_its_hardware_directory() -> None:
    """A slug names a box we keep results for, so its directory must exist."""
    for m in machines.MACHINES:
        assert (ROOT / "hardware" / m.directory).is_dir(), m.slug


def test_every_hardware_directory_is_registered() -> None:
    """A new machine's directory without a registry entry would have no label
    and no row in the doc -- the drift this registry exists to prevent. Force
    the registry to be updated when a machine joins."""
    registered = {m.directory for m in machines.MACHINES}
    on_disk = {p.name for p in (ROOT / "hardware").iterdir() if p.is_dir()}
    assert on_disk <= registered, (
        f"unregistered machine directories: {on_disk - registered}"
    )


def test_slugs_and_directories_are_unique_and_path_safe() -> None:
    """The slug is a label, a filename stamp and a prose token, so it must be
    unique and carry no whitespace or path separator. Two entries sharing a
    directory would each claim the same machine's data, so directories are
    unique too."""
    slugs = [m.slug for m in machines.MACHINES]
    assert len(slugs) == len(set(slugs)), slugs
    directories = [m.directory for m in machines.MACHINES]
    assert len(directories) == len(set(directories)), directories
    for slug in slugs:
        assert slug and not any(c.isspace() for c in slug), repr(slug)
        assert "/" not in slug and "\\" not in slug, repr(slug)


def test_the_github_label_is_the_slug_under_the_hardware_namespace() -> None:
    """The label a machine queries is `hardware:<slug>`. The slug stays the bare
    identity so it is unchanged in log filenames and `--slug`; the prefix is
    only the label's namespace (#302)."""
    for m in machines.MACHINES:
        assert m.label == f"{machines.HARDWARE_PREFIX}{m.slug}"
        assert m.label.startswith("hardware:")
        assert not m.slug.startswith("hardware:")


def test_a_class_label_is_never_a_machine_slug() -> None:
    """`Nvidia` covers both the RTX 3080 Ti desktop and the DGX Spark, so a
    class tag cannot identify a machine. It must never be a slug, and every
    machine's declared classes must be real class labels."""
    slugs = {m.slug for m in machines.MACHINES}
    assert slugs.isdisjoint(machines.CLASS_LABELS)
    for m in machines.MACHINES:
        assert set(m.classes) <= set(machines.CLASS_LABELS), m.slug


def test_the_generated_doc_is_current() -> None:
    """hardware/MACHINES.md is generated from the registry; regenerate it when
    the registry changes. Run `uv run python scripts/machines.py`."""
    assert machines.DOC.read_text() == machines.render_markdown(), (
        "hardware/MACHINES.md is stale -- run `uv run python scripts/machines.py`"
    )


def test_this_machine_if_managed_matches_its_registry_entry() -> None:
    """Run on a managed box -- the DGX Spark, say -- this catches a slug that
    was inferred from the directory rather than read from the hardware.

    It keys on the DIRECTORY, which is ground truth: the box generated it with
    `directory_name`, and it is committed under `hardware/`. Keying on the slug
    instead would defeat the purpose -- a wrong slug is simply not in the
    registry, so `by_slug` returns None and the machine looks unmanaged, and the
    test would skip exactly when it should fail. On an unmanaged machine (a CI
    runner, a dev laptop) the directory is not registered, so it skips.
    """
    import hardware_id
    import provenance

    try:
        facts, platform = hardware_id.facts_for_this_machine()
        directory = hardware_id.directory_name(facts, platform)
        derived_slug = hardware_id.short_slug(facts, platform)
    except Exception as exc:  # noqa: BLE001 -- a detection failure is a skip, not a failure
        pytest.skip(f"cannot identify this machine: {exc}")
    entry = machines.by_directory(directory)
    if entry is None:
        pytest.skip(f"this machine ({directory}) is not one we manage")
    assert derived_slug == entry.slug, (
        f"registry slug {entry.slug!r} for {directory} does not match the slug "
        f"the hardware derives ({derived_slug!r}) -- the registered slug is wrong"
    )
    # machine_slug() is what actually stamps logs and is the label's source, so
    # pin it to the same value here rather than trusting it equals short_slug.
    assert provenance.machine_slug() == entry.slug
