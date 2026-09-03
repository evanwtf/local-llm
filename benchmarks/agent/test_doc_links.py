"""Every relative link in the documentation must resolve.

Written because the #85 hardware restructure moved six benchmark documents into
`hardware/<machine>/` and left README pointing at all of them. The links sat
broken for days, through several sweeps and a close of #85 as done, because a
dead relative link in markdown renders as ordinary text on GitHub -- it does not
warn, it just goes nowhere.

That is the same shape as the other failures this project keeps finding: the
broken thing looks exactly like the working thing. A `git mv` is easy to verify
and nobody verified it, so this is the verification.

Only relative links are checked. External URLs are not fetched -- that would
make the suite depend on the network and on other people's uptime.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# The documents a reader actually starts from. Not a glob: a wildcard would pull
# in archived pages whose links are allowed to rot with the snapshot they record.
DOCS = [
    "README.md",
    "NEXT.md",
    "AGENTS.md",
    "CONVENTIONS.md",
    "RECOMMENDATIONS.md",
    "SOURCES.md",
    "TESTING-SET.md",
    "docs/changelog.md",
    "docs/upstream.md",
    "hardware/README.md",
    "benchmarks/agent/README.md",
    "benchmarks/agent/METHODOLOGY.md",
]

LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _relative_targets(text: str) -> list[str]:
    out = []
    for match in LINK.finditer(text):
        url = match.group(1)
        if url.startswith(("http://", "https://", "mailto:", "#")):
            continue
        out.append(url)
    return out


@pytest.mark.parametrize("rel", DOCS)
def test_every_relative_link_resolves(rel: str) -> None:
    doc = ROOT / rel
    if not doc.exists():
        pytest.skip(f"{rel} does not exist")
    broken = []
    for url in _relative_targets(doc.read_text()):
        target = (doc.parent / url.split("#")[0]).resolve()
        if not target.exists():
            broken.append(url)
    assert not broken, f"{rel} links to paths that do not exist: {sorted(set(broken))}"


def test_the_changelog_is_reachable_from_next() -> None:
    """A document nobody can find is a document that was deleted.

    The changelog moved out of NEXT.md; the pointer replacing it is the only
    thing making that a move rather than a loss.
    """
    assert "docs/changelog.md" in (ROOT / "NEXT.md").read_text()
