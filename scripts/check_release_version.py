"""Refuse a release whose tag disagrees with the declared version.

    uv run python scripts/check_release_version.py v1.0.0

A tag that says one version while the package says another ships a release
reporting the wrong number, and nothing downstream notices until something
disagrees about which version has which fix. The check is cheap; the failure
is not, so this refuses rather than warns.

Every place the version is declared must agree. Today that is `pyproject.toml`
alone -- there is no `__version__` in any module -- and this script finds the
declarations rather than hard-coding the list, so adding a second one does not
silently escape the check.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import re
import sys
import tomllib
from typing import NoReturn

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: A release tag: `v` then a semantic version. Anything else is not a release.
TAG = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")


def version_from_tag(tag: str) -> str:
    """The version a tag claims, or refuse.

    `1.0.0` without the `v`, or `v1.0` with two components, are both rejected:
    a release process that accepts several spellings produces several answers
    to "which tag is version X".
    """
    match = TAG.match(tag)
    if not match:
        raise ValueError(f"{tag!r} is not a release tag of the form vX.Y.Z")
    return match.group("version")


def declared_versions(root: pathlib.Path = ROOT) -> dict[str, str]:
    """Every declared version, keyed by where it was declared."""
    out: dict[str, str] = {}
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text())
        declared = (data.get("project") or {}).get("version")
        if declared:
            out["pyproject.toml"] = str(declared)
    # A module-level __version__ is the usual second home for this. There is
    # none today; find it if one appears rather than trusting that it will be
    # added to this list by hand.
    for path in sorted(root.glob("src/**/__init__.py")) + sorted(
        root.glob("benchmarks/**/__init__.py")
    ):
        found = re.search(
            r'^__version__\s*=\s*["\']([^"\']+)["\']', path.read_text(), re.MULTILINE
        )
        if found:
            out[str(path.relative_to(root))] = found.group(1)
    return out


def disagreements(tag: str, root: pathlib.Path = ROOT) -> list[str]:
    """Every reason this tag must not be released. Empty means it may."""
    try:
        want = version_from_tag(tag)
    except ValueError as exc:
        return [str(exc)]
    declared = declared_versions(root)
    if not declared:
        return ["no version is declared anywhere; cannot check the tag"]
    return [
        f"{where} declares {found}, but the tag says {want}"
        for where, found in sorted(declared.items())
        if found != want
    ]


def main(argv: list[str] | None = None) -> NoReturn:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the release tag, e.g. v1.0.0")
    args = parser.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)

    problems = disagreements(args.tag)
    if problems:
        for problem in problems:
            logger.error("REFUSING %s: %s", args.tag, problem)
        raise SystemExit(1)
    logger.info("%s agrees with every declared version", args.tag)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
