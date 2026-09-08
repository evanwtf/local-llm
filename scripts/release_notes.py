"""Print the changelog section for a release, or refuse.

    uv run python scripts/release_notes.py v1.0.0

Release notes are generated from `docs/changelog.md`, never written into the
tag message. Backticks in a hand-written `git tag -m` are expanded by the
shell and silently delete text, and a tag message cannot be corrected once
pushed without moving the tag.

A version with no changelog section is refused rather than released with empty
notes. An empty release note is worse than no release: it looks like the work
was trivial rather than undocumented.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import pathlib
import re
import signal
import sys
from typing import NoReturn

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "docs" / "changelog.md"


def section(version: str, text: str) -> str | None:
    """The body of `## v<version>`, or None if there is no such section.

    The heading may carry a date or any trailing prose -- `## v1.0.0 — 2026-09-07`
    is the intended shape -- so it is matched by prefix, anchored so that
    `v1.0.10` cannot satisfy a request for `v1.0.1`.
    """
    want = version.removeprefix("v")
    pattern = re.compile(
        rf"^##\s+v{re.escape(want)}(?![\w.])[^\n]*\n(?P<body>.*?)(?=^##\s|\Z)",
        re.M | re.S,
    )
    found = pattern.search(text)
    if not found:
        return None
    body = found.group("body").strip()
    return body or None


def main(argv: list[str] | None = None) -> NoReturn:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="the release, e.g. v1.0.0")
    parser.add_argument("--changelog", type=pathlib.Path, default=CHANGELOG)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    # These notes get piped -- `release_notes.py v1.0.0 | head` is the obvious
    # way to look at them. Restore the default SIGPIPE handler so a closed
    # reader ends the process quietly, instead of logging printing a
    # BrokenPipeError traceback over the notes it just emitted.
    with contextlib.suppress(AttributeError, ValueError):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    try:
        text = args.changelog.read_text()
    except OSError as exc:
        logger.error("REFUSING: cannot read %s: %s", args.changelog, exc)
        raise SystemExit(1) from exc

    body = section(args.version, text)
    if body is None:
        logger.error(
            "REFUSING %s: %s has no `## %s` section. Write the entry before "
            "tagging -- a release with empty notes reads as trivial work "
            "rather than undocumented work.",
            args.version,
            args.changelog,
            args.version,
        )
        raise SystemExit(1)
    logger.info("%s", body)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
